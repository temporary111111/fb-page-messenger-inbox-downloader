import hashlib
import hmac
import http.client
import json
import tempfile
import threading
from dataclasses import replace
import unittest
from pathlib import Path

from inbox_archive.config import Settings
from inbox_archive.database import Database
from inbox_archive.server import make_server


class WebhookHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name), '123')
        settings = Settings(Path(self.temp.name), '123', 'token', 'secret', 'verify', port=0)
        self.wake = threading.Event()
        self.server = make_server(self.db, settings, self.wake)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, body=None, headers=None):
        con = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            con.request(method, path, body, headers or {})
            response = con.getresponse()
            return response.status, response.read()
        finally:
            con.close()

    def test_verification_health_and_archive_files_not_public(self):
        self.assertEqual(self.request('GET', '/health'), (200, b'ok'))
        self.assertEqual(self.request('GET', '/webhook?hub.mode=subscribe&hub.verify_token=verify&hub.challenge=1234'), (200, b'1234'))
        self.assertEqual(self.request('GET', '/webhook?hub.mode=subscribe&hub.verify_token=bad')[0], 403)
        for path in ('/data/inbox.sqlite3', '/.env', '/status', '/attachments/test.jpg'):
            self.assertEqual(self.request('GET', path)[0], 404)

    def test_signed_event_is_durable_before_acknowledgement(self):
        body = json.dumps({'object': 'page', 'entry': [{'id': '123', 'messaging': [{
            'sender': {'id': 'customer'}, 'recipient': {'id': '123'},
            'timestamp': 1704103200123, 'message': {'mid': 'm1', 'text': 'hello'}}]}]}).encode()
        signature = 'sha256=' + hmac.new(b'secret', body, hashlib.sha256).hexdigest()
        headers = {'X-Hub-Signature-256': signature, 'Content-Type': 'application/json'}
        self.assertEqual(self.request('POST', '/webhook', body, headers)[0], 200)
        self.assertEqual(self.db.status()['messages'], 1)
        self.assertTrue(self.wake.is_set())
        self.assertEqual(self.request('POST', '/webhook', body)[0], 403)
        self.assertEqual(self.db.status()['messages'], 1)

    def test_signed_malformed_payload_is_rejected(self):
        body = b'{"object":"page","entry":[{"id":"123","messaging":[null]}]}'
        signature = 'sha256=' + hmac.new(b'secret', body, hashlib.sha256).hexdigest()
        self.assertEqual(self.request('POST', '/webhook', body, {'X-Hub-Signature-256': signature})[0], 400)
        self.assertEqual(self.db.status()['webhook_events'], 0)

    def test_second_archive_cannot_bind_an_existing_webhook_port(self):
        second = Database(Path(self.temp.name)/'other', '456')
        settings = Settings(Path(self.temp.name)/'other','456','token','secret','verify',port=self.server.server_port)
        with self.assertRaises(OSError):
            duplicate=make_server(second,settings,threading.Event())
            duplicate.server_close()


if __name__ == '__main__':
    unittest.main()
