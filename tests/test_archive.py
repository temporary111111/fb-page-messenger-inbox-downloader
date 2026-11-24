import hashlib
import hmac
import io
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import URLError

from inbox_archive.config import Settings
from inbox_archive.database import Database
from inbox_archive.graph import GraphClient, GraphError
from inbox_archive.media import download_pending, validate_media_url
from inbox_archive.sync import SyncEngine
from inbox_archive.webhook import accept_webhook
from inbox_archive.export import export_archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Database(self.root, 'page-1')

    def message(self, mid='m1'):
        return {'id': mid, 'created_time': '2024-01-01T10:00:00+0000',
                'from': {'id': 'customer', 'name': 'Juan'}, 'message': 'Bayad na ₱500',
                'attachments': {'data': [{'id': 'a1', 'name': 'receipt.jpg',
                    'image_data': {'url': 'https://scontent.xx.fbcdn.net/receipt.jpg'}}]}}

    def test_upsert_preserves_media_and_exact_message_on_repeated_import(self):
        self.db.save_conversation({'id': 'c1', 'participants': {'data': [{'id': 'customer'}]}})
        self.db.save_message('c1', self.message())
        self.db.save_message('c1', self.message())
        self.assertEqual(self.db.status()['messages'], 1)
        self.assertEqual(self.db.status()['attachments'], 1)
        self.assertEqual(self.db.rows('SELECT text FROM messages')[0]['text'], 'Bayad na ₱500')

    def test_page_binding_prevents_mixing_inboxes(self):
        with self.assertRaises(ValueError):
            Database(self.root, 'another-page')

    def test_sync_follows_all_pages_and_resumes_failed_conversation(self):
        class FakeGraph:
            fail = True
            def get(inner, path, params=None):
                if path == 'page-1/conversations':
                    return {'data': [{'id': 'c1', 'updated_time': 'now'}]}
                if path == 'c1/messages':
                    return {'data': [self.message()], 'paging': {'next': 'https://graph.facebook.com/v27.0/c1/messages?after=two&access_token=SECRET'}}
                if 'after=two' in path:
                    if inner.fail:
                        raise GraphError('network unavailable')
                    return {'data': [dict(self.message('m2'), attachments={'data': []})]}
                raise AssertionError(path)
        graph = FakeGraph()
        engine = SyncEngine(self.db, graph)
        engine.run()
        status = self.db.status()
        self.assertEqual(status['incomplete_conversations'], 1)
        self.assertEqual(status['messages'], 1)
        self.assertNotIn('SECRET', self.db.rows('SELECT cursor FROM conversations')[0]['cursor'])
        graph.fail = False
        engine.run()
        self.assertEqual(self.db.status()['messages'], 2)
        self.assertEqual(self.db.status()['incomplete_conversations'], 0)

    def test_discovers_every_conversation_page_before_importing_messages(self):
        class Graph:
            def get(inner, path, params=None):
                if path == 'page-1/conversations':
                    return {'data': [{'id': 'c1'}], 'paging': {
                        'next': 'https://graph.facebook.com/v27.0/page-1/conversations?after=next'}}
                if 'page-1/conversations?after=next' in path:
                    return {'data': [{'id': 'c2'}]}
                if path == 'c1/messages':
                    self.assertEqual(self.db.status()['conversations'], 2)
                    return {'data': []}
                if path == 'c2/messages':
                    return {'data': []}
                raise AssertionError(path)

        SyncEngine(self.db, Graph()).run()
        self.assertEqual(self.db.status()['conversations'], 2)
        self.assertEqual(self.db.status()['incomplete_conversations'], 0)

    def test_message_history_uses_reliable_bounded_page_size(self):
        class Graph:
            def get(inner, path, params=None):
                if path == 'page-1/conversations':
                    return {'data': [{'id': 'c1'}]}
                if path == 'c1/messages':
                    self.assertEqual(params['limit'], 5)
                    return {'data': []}
                raise AssertionError(path)

        SyncEngine(self.db, Graph()).run()

    def test_webhook_signature_dedup_and_canonical_merge(self):
        payload = {'object': 'page', 'entry': [{'id': 'page-1', 'messaging': [{
            'sender': {'id': 'customer'}, 'recipient': {'id': 'page-1'},
            'timestamp': 1704103200123, 'message': {'mid': 'm1', 'text': 'hello'}}]}]}
        body = json.dumps(payload).encode()
        signature = 'sha256=' + hmac.new(b'secret', body, hashlib.sha256).hexdigest()
        with self.assertRaises(PermissionError):
            accept_webhook(self.db, body, 'sha256=wrong', 'secret')
        accept_webhook(self.db, body, signature, 'secret')
        accept_webhook(self.db, body, signature, 'secret')
        self.assertEqual(self.db.status()['messages'], 1)
        self.assertEqual(self.db.status()['webhook_events'], 1)
        self.assertIn('.123', self.db.rows('SELECT created_time FROM messages')[0]['created_time'])
        self.db.save_conversation({'id': 'c1', 'participants': {'data': [{'id': 'customer'}]}})
        self.assertEqual(self.db.rows('SELECT conversation_id FROM messages')[0]['conversation_id'], 'c1')

    def test_outgoing_echo_and_wrong_page(self):
        payload = {'object': 'page', 'entry': [{'id': 'page-1', 'messaging': [{
            'sender': {'id': 'page-1'}, 'recipient': {'id': 'customer'},
            'timestamp': 1704103200000,
            'message': {'mid': 'out', 'is_echo': True, 'text': 'Verification code 123'}}]}]}
        def send():
            body = json.dumps(payload).encode()
            sig = 'sha256=' + hmac.new(b'secret', body, hashlib.sha256).hexdigest()
            accept_webhook(self.db, body, sig, 'secret')
        send()
        self.assertEqual(self.db.rows('SELECT sender_id FROM messages')[0]['sender_id'], 'page-1')
        payload['entry'][0]['id'] = 'other-page'
        send()
        self.assertEqual(self.db.status()['webhook_events'], 1)

    def test_download_is_atomic_retryable_and_export_includes_files(self):
        self.db.save_conversation({'id': 'c1'})
        self.db.save_message('c1', self.message())
        def offline(url):
            raise URLError('offline')
        download_pending(self.db, opener=offline)
        self.assertEqual(self.db.status()['failed_attachments'], 1)
        class Response(io.BytesIO):
            headers = {'Content-Type': 'image/jpeg'}
        download_pending(self.db, opener=lambda url: Response(b'actual image bytes'))
        self.assertEqual(self.db.status()['downloaded_attachments'], 1)
        row = self.db.rows('SELECT * FROM attachments')[0]
        self.assertEqual((self.root / row['local_path']).read_bytes(), b'actual image bytes')
        self.assertEqual(row['sha256'], hashlib.sha256(b'actual image bytes').hexdigest())
        target = export_archive(self.db, self.root / 'export')
        manifest = json.loads((target / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['counts']['messages'], 1)
        self.assertEqual(len(list((target / 'conversations').glob('*.jsonl'))), 1)
        self.assertEqual((target / row['local_path']).read_bytes(), b'actual image bytes')

    def test_media_refuses_local_urls_and_fake_cdn_domains(self):
        for url in ('http://127.0.0.1/x', 'https://localhost/x',
                    'https://fbcdn.net.evil.test/x', 'file:///etc/passwd',
                    'https://user:pass@scontent.fbcdn.net/x'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_media_url(url)
        validate_media_url('https://scontent.xx.fbcdn.net/a.jpg')

    def test_graph_rejects_untrusted_pagination_before_sending_token(self):
        client = GraphClient('secret', 'v27.0', 'app-secret')
        for path in ('https://evil.test/messages', '//evil.test/messages',
                     'https://graph.facebook.com.evil.test/messages'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                client.get(path)

    def test_graph_error_does_not_expose_credentials(self):
        client = GraphClient('secret-token', 'v27.0', 'app-secret')
        self.assertNotIn('secret-token', repr(client))

    def test_graph_client_allows_short_retryable_request_budget(self):
        client = GraphClient('secret-token', 'v27.0', 'app-secret', timeout_seconds=10, retries=1,
                             hard_timeout_seconds=15)
        self.assertEqual(client.timeout_seconds, 10)
        self.assertEqual(client.retries, 1)
        self.assertEqual(client.hard_timeout_seconds, 15)

    def test_windows_curl_keeps_tokens_out_of_command_arguments(self):
        client = GraphClient('secret-token', 'v27.0', 'app-secret', hard_timeout_seconds=15)
        client._curl_path = 'curl.exe'
        captured = {}
        def fake_run(config):
            captured['args'] = [client._curl_path, '--config', 'temporary-file']
            captured['config'] = config
            return {'data': []}
        from unittest.mock import patch
        with patch.object(client, '_run_curl', fake_run):
            self.assertEqual(client.get('page-1/conversations'), {'data': []})
        self.assertNotIn('secret-token', ' '.join(captured['args']))
        self.assertIn('Authorization: Bearer secret-token', captured['config'])

    def test_windows_curl_uses_a_nonblocking_deadline(self):
        client = GraphClient('secret-token', 'v27.0', 'app-secret', hard_timeout_seconds=0.01)
        client._curl_path = 'curl.exe'
        class Process:
            args = ['curl.exe']
            returncode = None
            def poll(self): return None
            def kill(self): self.killed = True
        process = Process()
        from unittest.mock import patch
        with patch('inbox_archive.graph.subprocess.Popen', return_value=process):
            with self.assertRaises(subprocess.TimeoutExpired):
                client._run_curl('url = "https://example.test"')
        self.assertTrue(process.killed)

    def test_windows_curl_reports_safe_failure_reason(self):
        client = GraphClient('secret-token', 'v27.0', 'app-secret', retries=1, hard_timeout_seconds=15)
        client._curl_path = 'curl.exe'
        from unittest.mock import patch
        with patch.object(client, '_run_curl', side_effect=OSError('curl exit 28')):
            with self.assertRaises(GraphError) as raised:
                client.get('page-1/conversations')
        self.assertIn('curl exit 28', str(raised.exception))
        self.assertNotIn('secret-token', str(raised.exception))

    def test_duplicate_attachment_pagination_stops_without_looping(self):
        message = self.message()
        message['attachments']['paging'] = {
            'next': 'https://graph.facebook.com/v27.0/m1/attachments?after=a1'
        }
        calls = []
        class Graph:
            def get(self, path, params=None):
                calls.append(path)
                if path == 'page-1/conversations':
                    return {'data': [{'id': 'c1', 'updated_time': '2026-11-24T10:00:00+0000'}]}
                if path.startswith('c1/messages'):
                    return {'data': [message]}
                if path == 'https://graph.facebook.com/v27.0/m1/attachments?after=a1':
                    return {
                        'data': [message['attachments']['data'][0]],
                        'paging': {'next': 'https://graph.facebook.com/v27.0/m1/attachments?after=a2'},
                    }
                self.fail(f'Unexpected extra attachment page requested: {path}')
        SyncEngine(self.db, Graph()).run()
        status = self.db.status()
        self.assertEqual(status['messages'], 1)
        self.assertEqual(status['attachments'], 1)
        self.assertEqual(status['incomplete_conversations'], 0)
        self.assertIn('https://graph.facebook.com/v27.0/m1/attachments?after=a1', calls)

    def test_duplicate_idless_attachment_pagination_stops_without_looping(self):
        template_item = {'generic_template': {'title': 'Order summary'}}
        message = {
            'id': 'm1',
            'created_time': '2026-11-24T10:00:00+0000',
            'from': {'id': 'p1', 'name': 'Shop'},
            'to': {'data': [{'id': 'u1', 'name': 'Customer'}]},
            'message': '',
            'attachments': {
                'data': [template_item],
                'paging': {'next': 'https://graph.facebook.com/v27.0/m1/attachments?after=a1'},
            },
        }
        class Graph:
            def get(self, path, params=None):
                if path == 'page-1/conversations':
                    return {'data': [{'id': 'c1', 'updated_time': '2026-11-24T10:00:00+0000'}]}
                if path.startswith('c1/messages'):
                    return {'data': [message]}
                if path == 'https://graph.facebook.com/v27.0/m1/attachments?after=a1':
                    return {
                        'data': [dict(template_item)],
                        'paging': {'next': 'https://graph.facebook.com/v27.0/m1/attachments?after=a2'},
                    }
                self.fail(f'Unexpected extra attachment page requested: {path}')
        SyncEngine(self.db, Graph()).run()
        status = self.db.status()
        self.assertEqual(status['messages'], 1)
        self.assertEqual(status['incomplete_conversations'], 0)

    def test_raw_api_pagination_credentials_are_not_written_to_disk(self):
        message = self.message()
        message['attachments']['paging'] = {'next': 'https://graph.facebook.com/v27.0/m1/attachments?after=x&access_token=PRIVATE_TOKEN&appsecret_proof=PRIVATE_PROOF'}
        self.db.save_conversation({'id': 'c1'})
        self.db.save_message('c1', message)
        raw = self.db.rows('SELECT raw_json FROM messages')[0]['raw_json']
        self.assertNotIn('PRIVATE_TOKEN', raw)
        self.assertNotIn('PRIVATE_PROOF', raw)

    def test_webhook_media_and_graph_media_merge_without_duplicate_files(self):
        self.db.save_conversation({'id': 'c1'})
        partial = dict(self.message(), attachments=[{'type': 'image', 'payload': {
            'url': 'https://scontent.xx.fbcdn.net/receipt.jpg'}}])
        self.db.save_message('c1', partial, partial=True)
        self.db.save_message('c1', self.message())
        self.db.save_message('c1', partial, partial=True)
        self.assertEqual(self.db.status()['attachments'], 1)

    def test_mismatched_download_length_is_not_marked_downloaded(self):
        self.db.save_conversation({'id': 'c1'})
        self.db.save_message('c1', self.message())
        class Truncated(io.BytesIO):
            headers = {'Content-Type': 'image/jpeg', 'Content-Length': '100'}
        download_pending(self.db, opener=lambda url: Truncated(b'truncated'))
        self.assertEqual(self.db.status()['downloaded_attachments'], 0)
        self.assertEqual(self.db.status()['failed_attachments'], 1)

    def test_deleted_local_attachment_is_rediscovered_and_downloaded(self):
        self.db.save_conversation({'id': 'c1'})
        self.db.save_message('c1', self.message())
        self.db.execute("UPDATE attachments SET status='downloaded',local_path='attachments/missing.jpg'")
        class Image(io.BytesIO):
            headers = {'Content-Type': 'image/jpeg'}
        download_pending(self.db, opener=lambda url: Image(b'image data'))
        local = self.db.rows('SELECT local_path FROM attachments')[0]['local_path']
        self.assertTrue((self.root / local).is_file())

    def test_only_one_sync_process_can_use_an_archive(self):
        with self.db.writer_lock():
            with self.assertRaises(ValueError):
                with self.db.writer_lock():
                    self.fail('A second writer acquired the archive')
        with self.db.writer_lock():
            pass

    def test_incremental_scan_stops_after_known_messages_but_full_scan_does_not(self):
        class Graph:
            older_calls = 0
            def get(inner, path, params=None):
                if path == 'page-1/conversations':
                    return {'data': [{'id': 'c1', 'updated_time': 'changed'}]}
                if path == 'c1/messages':
                    return {'data': [self.message()], 'paging': {'next': 'https://graph.facebook.com/v27.0/c1/messages?after=old'}}
                inner.older_calls += 1
                return {'data': [dict(self.message('m2'), attachments={'data': []})]}
        self.db.save_conversation({'id': 'c1', 'updated_time': 'before'})
        self.db.save_message('c1', self.message())
        from inbox_archive.database import now
        self.db.execute('UPDATE conversations SET complete=1,last_full_scan=?', (now(),))
        graph = Graph()
        SyncEngine(self.db, graph).run()
        self.assertEqual(graph.older_calls, 0)
        SyncEngine(self.db, graph).run(full=True)
        self.assertEqual(graph.older_calls, 1)
        self.assertEqual(self.db.status()['messages'], 2)

    def test_page_callback_downloads_before_fetching_the_next_page(self):
        calls = []
        class Graph:
            def get(inner, path, params=None):
                calls.append(path)
                if path == 'page-1/conversations':
                    return {'data': [{'id': 'c1'}]}
                if path == 'c1/messages':
                    return {'data': [self.message()], 'paging': {'next': 'https://graph.facebook.com/v27.0/c1/messages?after=old'}}
                return {'data': []}
        def download(ids):
            self.assertEqual(ids, ['m1'])
            self.assertEqual(self.db.status()['messages'], 1)
            calls.append('download')
        SyncEngine(self.db, Graph(), after_page=download).run()
        self.assertLess(calls.index('download'), calls.index('c1/messages?after=old'))

    def test_attachment_reordering_cannot_reuse_the_wrong_file(self):
        self.db.save_conversation({'id': 'c1'})
        a = 'https://scontent.xx.fbcdn.net/a.jpg'
        b = 'https://scontent.xx.fbcdn.net/b.jpg'
        partial = dict(self.message(), attachments=[{'payload': {'url': a}}, {'payload': {'url': b}}])
        self.db.save_message('c1', partial, partial=True)
        self.db.execute("UPDATE attachments SET status='downloaded',local_path='a.jpg' WHERE url=?", (a,))
        self.db.execute("UPDATE attachments SET status='downloaded',local_path='b.jpg' WHERE url=?", (b,))
        self.db.save_message('c1', dict(self.message(), attachments={'data': [
            {'id': 'b-id', 'file_url': b}, {'id': 'a-id', 'file_url': a}]}))
        by_url = {row['url']: row for row in self.db.rows('SELECT * FROM attachments')}
        self.assertEqual(by_url[a]['local_path'], 'a.jpg')
        self.assertEqual(by_url[b]['local_path'], 'b.jpg')

    def test_export_refuses_to_claim_missing_downloaded_files_are_complete(self):
        self.db.save_conversation({'id': 'c1'})
        self.db.save_message('c1', self.message())
        self.db.execute("UPDATE attachments SET status='downloaded',local_path='attachments/missing.jpg'")
        target = self.root / 'broken-export'
        with self.assertRaises(FileNotFoundError):
            export_archive(self.db, target)
        self.assertFalse((target / 'EXPORT_COMPLETE').exists())
        self.assertTrue((target / 'EXPORT_INCOMPLETE').exists())

    def test_webhook_only_messages_cannot_hide_a_gap_during_incremental_sync(self):
        self.db.save_conversation({'id': 'c1', 'updated_time': 'before'})
        self.db.save_message('c1', self.message(), partial=True)
        from inbox_archive.database import now
        self.db.execute('UPDATE conversations SET complete=1,last_full_scan=?', (now(),))
        class Graph:
            def get(inner, path, params=None):
                if path == 'page-1/conversations':
                    return {'data': [{'id': 'c1', 'updated_time': 'changed'}]}
                if path == 'c1/messages':
                    return {'data': [self.message()], 'paging': {'next': 'https://graph.facebook.com/v27.0/c1/messages?after=gap'}}
                return {'data': [dict(self.message('missed-offline'), attachments={'data': []})]}
        SyncEngine(self.db, Graph()).run()
        self.assertEqual(self.db.status()['messages'], 2)


if __name__ == '__main__':
    unittest.main()
