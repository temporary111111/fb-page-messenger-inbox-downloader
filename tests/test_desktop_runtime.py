import queue
import subprocess
import sys
import tempfile
import unittest
import io
from unittest.mock import patch
from pathlib import Path

from inbox_archive.desktop_runtime import DesktopController, TunnelStatus, WindowsJob, redact
from inbox_archive.desktop_settings import ConfigStore


class DesktopRuntimeTests(unittest.TestCase):
    def test_tunnel_url_is_not_a_connection(self):
        status = TunnelStatus()
        status.feed('Visit it at https://sample-preview.trycloudflare.com')
        self.assertEqual(status.callback, 'https://sample-preview.trycloudflare.com/webhook')
        self.assertNotEqual(status.state, 'Connected')
        status.feed('Registered tunnel connection connIndex=0 protocol=quic')
        self.assertEqual(status.state, 'Connected')
        status.feed('Unregistered tunnel connection connIndex=0')
        self.assertEqual(status.state, 'Disconnected')

    def test_secrets_are_scrubbed_from_activity(self):
        value = redact('token=my-secret https://graph.facebook.com/a?access_token=123&appsecret_proof=456', ['my-secret'])
        self.assertNotIn('my-secret', value)
        self.assertNotIn('access_token=123', value)
        self.assertNotIn('appsecret_proof=456', value)

    def test_controller_stop_only_terminates_owned_processes(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = DesktopController(Path(folder), queue.Queue())
            owned = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], stdin=subprocess.PIPE)
            tunnel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
            unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
            try:
                controller.adopt_owned(owned, 'worker')
                controller.adopt_owned(tunnel, 'tunnel')
                controller.stop(timeout=0.1)
                self.assertIsNotNone(owned.poll())
                self.assertIsNotNone(tunnel.poll())
                self.assertIsNone(unrelated.poll())
                controller.stop(timeout=0.1)
            finally:
                controller.close()
                if owned.poll() is None:
                    owned.kill()
                if tunnel.poll() is None:
                    tunnel.kill()
                    tunnel.wait()
                unrelated.terminate()
                unrelated.wait(timeout=5)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows Job Object test')
    def test_job_close_kills_owned_child(self):
        job = WindowsJob()
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            job.assign(child)
            job.close()
            child.wait(timeout=5)
            self.assertIsNotNone(child.poll())
        finally:
            job.close()
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_repeated_start_cannot_create_duplicate_workers(self):
        class Child:
            def __init__(self):
                self.code = None
                self.stdin = io.StringIO()
                self.stdout = io.StringIO()
                self.stderr = io.StringIO()
            def poll(self): return self.code
            def wait(self, timeout=None): self.code=0
        with tempfile.TemporaryDirectory() as folder:
            ConfigStore(folder).save({'META_PAGE_ID':'123','META_PAGE_ACCESS_TOKEN':'token',
                'META_APP_SECRET':'secret','WEBHOOK_VERIFY_TOKEN':'verify'})
            controller=DesktopController(folder,queue.Queue())
            child=Child()
            def adopt(process,role): setattr(controller,role,process)
            try:
                with patch('inbox_archive.desktop_runtime.subprocess.Popen',return_value=child) as launch, \
                     patch('inbox_archive.desktop_runtime.threading.Thread.start'), \
                     patch.object(controller,'adopt_owned',side_effect=adopt):
                    self.assertTrue(controller.start())
                    self.assertFalse(controller.start())
                    self.assertEqual(launch.call_count,1)
            finally:
                controller.close()


if __name__ == '__main__':
    unittest.main()
