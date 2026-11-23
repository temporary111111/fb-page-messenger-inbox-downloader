import queue
import tempfile
import tkinter as tk
from tkinter import font, ttk
import unittest
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
        self.addCleanup(self.window.destroy)
        self.app = DesktopApp(self.window, Path(self.temp.name), autostart=False, controller_factory=FakeController)

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


if __name__ == '__main__':
    unittest.main()
