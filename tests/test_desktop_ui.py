import queue
import gc
import tempfile
import tkinter as tk
from tkinter import font, ttk
import unittest
from unittest.mock import patch
from pathlib import Path

from inbox_archive.desktop import DesktopApp


class FakeJob:
    def close(self):
        pass


class FakeController:
    def __init__(self, root, events):
        self.running = False
        self.job = FakeJob()
        self.events = events
    def start(self):
        self.running = True
    def stop(self):
        self.running = False
    def close(self):
        self.stop()


class DesktopUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = tk.Tk()
        self.window.withdraw()
        self.app = DesktopApp(self.window, Path(self.temp.name), autostart=False, controller_factory=FakeController)
        self.addCleanup(self.cleanup_window)

    def cleanup_window(self):
        for thread in self.app.background_threads:
            thread.join(timeout=2)
        for timer in self.window.tk.call('after', 'info'):
            self.window.after_cancel(timer)
        self.window.destroy()
        self.app = None
        gc.collect()

    def test_unconfigured_launch_opens_settings_without_starting_network(self):
        self.assertTrue(self.app.settings_open)
        self.assertFalse(self.app.controller.running)
        self.assertEqual(self.app.interval_choice.get(), 'Every 5 minutes')

    def test_invalid_page_id_stays_in_settings_and_does_not_write_secrets(self):
        self.app.variables['META_PAGE_ID'].set('not-a-number')
        self.app.variables['META_PAGE_ACCESS_TOKEN'].set('test-token')
        self.app.variables['META_APP_SECRET'].set('test-secret')
        self.app.save_settings()
        self.assertIn('numbers', self.app.settings_feedback.get())
        self.assertFalse(self.app.store.path.exists())

    def test_tunnel_allocation_does_not_show_connected(self):
        self.app.events.put({'kind': 'tunnel', 'state': 'Starting', 'callback': 'https://sample.trycloudflare.com/webhook'})
        self.app.pump()
        self.assertEqual(self.app.tunnel_status.get(), 'Starting')
        self.assertEqual(self.app.callback, 'https://sample.trycloudflare.com/webhook')

    def test_status_has_a_readable_heading_font(self):
        configured = self.app.status_label.cget('font') or ttk.Style(self.window).lookup('Status.TLabel', 'font')
        self.assertGreaterEqual(font.Font(self.window, font=configured).actual()['size'], 15)

    def test_switching_to_empty_archive_resets_counts_and_connection(self):
        self.app.update_counts({'messages':12,'conversations':1,'sync':{'last_sync_success':'2026-11-24T02:00:00+0800'}})
        self.app.update_counts(None)
        self.assertEqual(self.app.counts['messages'].get(), '0')
        self.assertEqual(self.app.last_sync.get(), 'Not yet synced')
        self.assertEqual(self.app.page_status.get(), 'Not checked')

    def test_stopping_invalidates_the_previous_callback_address(self):
        self.app.callback = 'https://old.trycloudflare.com/webhook'
        self.app.events.put({'kind':'tunnel','state':'Stopped','callback':''})
        self.app.pump()
        self.assertEqual(self.app.callback, '')

    def test_settings_reopen_masks_a_previously_revealed_secret(self):
        variable = str(self.app.variables['META_PAGE_ACCESS_TOKEN'])
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        entry = next(w for w in descendants(self.window) if isinstance(w,ttk.Entry) and str(w.cget('textvariable')) == variable)
        entry.configure(show='')
        self.app.show_settings()
        self.assertTrue(entry.cget('show'))

    def test_unreadable_encoding_opens_recovery_settings(self):
        # Configured windows must still open so credentials can be corrected locally.
        folder=Path(self.temp.name) / 'malformed'
        folder.mkdir()
        (folder/'.env').write_bytes(b'\xff\xfe\x00broken')
        other=tk.Toplevel(self.window)
        try:
            app=DesktopApp(other,folder,autostart=False,controller_factory=FakeController)
            self.assertTrue(app.settings_open)
            self.assertTrue(app.settings_feedback.get())
        finally:
            other.destroy()

    def test_failed_settings_save_does_not_stop_existing_collection(self):
        self.app.store.save({'META_PAGE_ID':'123','META_PAGE_ACCESS_TOKEN':'sample-token',
            'META_APP_SECRET':'sample-secret','WEBHOOK_VERIFY_TOKEN':'sample-verify'})
        self.app.show_settings()
        self.app.configured = True
        self.app.controller.running = True
        def immediate(callback, *args):
            try:
                callback()
            except OSError:
                pass
        with patch.object(self.app.store,'save',side_effect=OSError('disk denied')), patch.object(self.app,'background',side_effect=immediate):
            self.app.save_settings()
        self.assertTrue(self.app.controller.running)


if __name__ == '__main__':
    unittest.main()
