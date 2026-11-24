import os
import tempfile
import threading
import unittest
from concurrent.futures import CancelledError
from pathlib import Path
from unittest.mock import patch

from inbox_archive.config import Settings
from inbox_archive.database import Database
from inbox_archive.desktop_settings import ConfigStore
from inbox_archive.export import export_archive


class DesktopSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.values = {'META_PAGE_ID': '123', 'META_PAGE_ACCESS_TOKEN': 'test-token',
            'META_APP_SECRET': 'test-secret', 'WEBHOOK_VERIFY_TOKEN': 'test-verify',
            'DATA_DIR': 'data', 'POLL_SECONDS': '300'}

    def test_saved_config_reloads_and_ignores_stale_environment(self):
        store = ConfigStore(self.root)
        store.save(self.values)
        with patch.dict(os.environ, {'META_PAGE_ACCESS_TOKEN': 'stale-token'}):
            values = store.read()
            values['META_PAGE_ACCESS_TOKEN'] = 'updated-token'
            store.save(values)
            self.assertEqual(store.settings().page_token, 'updated-token')
        self.assertEqual(store.settings().data_dir, self.root / 'data')
        self.assertNotIn('updated-token', repr(store.settings()))

    def test_save_keeps_comments_and_unknown_keys(self):
        (self.root / '.env').write_text('# owner note\nCUSTOM_VALUE=keep\n', encoding='utf-8')
        ConfigStore(self.root).save(self.values)
        text = (self.root / '.env').read_text(encoding='utf-8')
        self.assertIn('# owner note', text)
        self.assertIn('CUSTOM_VALUE=keep', text)

    def test_invalid_settings_do_not_overwrite_existing_config(self):
        store = ConfigStore(self.root)
        store.save(self.values)
        original = (self.root / '.env').read_bytes()
        for key, bad in [('META_PAGE_ID', 'abc'), ('PORT', '90000'),
                         ('POLL_SECONDS', '0'), ('META_APP_SECRET', 'bad\nINJECTED=yes')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                store.save({**self.values, key: bad})
        self.assertEqual((self.root / '.env').read_bytes(), original)

    def test_page_change_rejects_archive_of_another_page(self):
        Database(self.root / 'data', '123')
        with self.assertRaises(ValueError):
            ConfigStore(self.root).save({**self.values, 'META_PAGE_ID': '456'})
        self.assertFalse((self.root / '.env').exists())

    def test_cancelled_export_is_not_marked_complete(self):
        db = Database(self.root / 'data', '123')
        stop = threading.Event()
        stop.set()
        destination = self.root / 'cancelled-export'
        with self.assertRaises(CancelledError):
            export_archive(db, destination, cancel=stop)
        self.assertFalse((destination / 'EXPORT_COMPLETE').exists())
        self.assertTrue((destination / 'EXPORT_INCOMPLETE').exists())

    def test_verify_token_is_persisted_before_cross_process_start(self):
        values = {**self.values, 'WEBHOOK_VERIFY_TOKEN': ''}
        (self.root / '.env').write_text('\n'.join(k+'='+v for k,v in values.items()), encoding='utf-8')
        store = ConfigStore(self.root)
        store.ensure_verify_token()
        first = store.read()['WEBHOOK_VERIFY_TOKEN']
        self.assertTrue(first)
        self.assertEqual(ConfigStore(self.root).read()['WEBHOOK_VERIFY_TOKEN'], first)

    def test_export_rejects_absolute_attachment_path_without_touching_live_file(self):
        db = Database(self.root / 'data', '123')
        folder = db.root / 'attachments'
        folder.mkdir()
        live = folder / 'receipt.jpg'
        live.write_bytes(b'original live image')
        db.save_conversation({'id':'c1'})
        db.save_message('c1', {'id':'m1','message':'receipt','attachments':{'data':[{'id':'a1'}]}})
        db.execute("UPDATE attachments SET status='downloaded',local_path=?", (str(live),))
        with self.assertRaises(ValueError):
            export_archive(db, self.root / 'unsafe-export')
        self.assertEqual(live.read_bytes(), b'original live image')

    def test_publishing_a_build_preserves_existing_portable_user_data(self):
        from inbox_archive.packaging import publish_bundle
        previous = self.root / 'dist' / 'InboxArchive'
        previous.mkdir(parents=True)
        (previous / '.env').write_text('private settings', encoding='utf-8')
        staging = self.root / 'staging'
        staging.mkdir()
        (staging / 'InboxArchive.exe').write_bytes(b'new app')
        release = publish_bundle(staging, self.root / 'dist')
        self.assertNotEqual(release, previous)
        self.assertEqual((previous / '.env').read_text(), 'private settings')
        self.assertEqual((release / 'InboxArchive.exe').read_bytes(), b'new app')

    def test_recovering_unreadable_configuration_keeps_an_original_backup(self):
        original = b'\xff\xfe\x00broken configuration'
        (self.root / '.env').write_bytes(original)
        ConfigStore(self.root).save(self.values)
        backups=list(self.root.glob('.env.backup-*'))
        self.assertEqual(len(backups),1)
        self.assertEqual(backups[0].read_bytes(),original)
        self.assertEqual(ConfigStore(self.root).settings().page_id,'123')


if __name__ == '__main__':
    unittest.main()
