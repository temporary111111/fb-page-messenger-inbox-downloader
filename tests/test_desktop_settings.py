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


if __name__ == '__main__':
    unittest.main()
