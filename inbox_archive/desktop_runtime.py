"""Owned processes and safe status transport for the desktop window."""
import ctypes
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .desktop_settings import ConfigStore, SECRET_KEYS


def redact(text, secrets=()):
    for value in secrets:
        if value:
            text = text.replace(value, '[hidden]')
    return re.sub(r'(access_token|appsecret_proof)=([^&\s]+)', r'\1=[hidden]', text)[:1000]


class TunnelStatus:
    def __init__(self):
        self.callback = ''
        self.state = 'Starting'

    def feed(self, line):
        match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com\b', line)
        if match:
            self.callback = match.group(0) + '/webhook'
        if 'Unregistered tunnel connection' in line or 'Failed to dial' in line:
            self.state = 'Disconnected'
        elif 'Registered tunnel connection' in line:
            self.state = 'Connected'
        return {'kind': 'tunnel', 'state': self.state, 'callback': self.callback}


class WindowsJob:
    """Closing this handle terminates only processes assigned by this app."""
    def __init__(self):
        self.handle = None
        if os.name != 'nt':
            return
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [('ProcessTime', ctypes.c_int64), ('JobTime', ctypes.c_int64),
                ('LimitFlags', wintypes.DWORD), ('MinWorkingSet', ctypes.c_size_t),
                ('MaxWorkingSet', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]

        class IOCounts(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('ReadOps', 'WriteOps', 'OtherOps', 'ReadBytes', 'WriteBytes', 'OtherBytes')]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IOCounts),
                ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError('Could not configure child-process cleanup')

    def assign(self, process):
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            process.terminate()
            process.wait(timeout=5)
            raise OSError('Could not supervise the app child process')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def entry_command(root, worker=False):
    flags = ['--app-dir', str(root)]
    if worker:
        flags.append('--worker')
    if getattr(sys, 'frozen', False):
        return [sys.executable, *flags]
    return [sys.executable, '-u', str(Path(__file__).resolve().parent.parent / 'desktop_entry.py'), *flags]


def locate_cloudflared(root, explicit=''):
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            candidate = Path(root) / candidate
        return str(candidate) if candidate.is_file() else None
    bundled = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent)) / 'bin/cloudflared.exe'
    portable = Path(root) / 'bin/cloudflared.exe'
    for candidate in (portable, bundled):
        if candidate.is_file():
            return str(candidate)
    installed = shutil.which('cloudflared')
    if installed:
        return installed
    # WinGet installs are not necessarily on this already-running process's PATH.
    packages = Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft/WinGet/Packages'
    if packages.is_dir():
        for package in packages.glob('Cloudflare.cloudflared_*'):
            candidates = list(package.glob('cloudflared*.exe'))
            if candidates:
                return str(candidates[0])
    return None


class DesktopController:
    def __init__(self, root, events):
        self.root = Path(root).resolve()
        self.events = events
        self.worker = None
        self.tunnel = None
        self.job = WindowsJob()
        self.lock = threading.RLock()
        self.stopping = False
        self.secrets = []
        self.closed = False

    @property
    def running(self):
        return self.worker is not None and self.worker.poll() is None

    def emit(self, kind, **payload):
        event = {'kind': kind, **payload}
        try:
            self.events.put_nowait(event)
        except queue.Full:
            try:
                self.events.get_nowait()
            except queue.Empty:
                pass
            self.events.put_nowait(event)

    def adopt_owned(self, process, role):
        self.job.assign(process)
        setattr(self, role, process)

    def start(self):
        with self.lock:
            if self.closed or self.running:
                return False
            store = ConfigStore(self.root)
            store.ensure_verify_token()
            values = store.read()
            settings = store.validate(values)
            self.secrets = [values.get(key, '') for key in SECRET_KEYS]
            self.stopping = False
            self.emit('phase', state='Starting')
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            process = subprocess.Popen(entry_command(self.root, worker=True), cwd=self.root,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace', creationflags=flags)
            self.adopt_owned(process, 'worker')
            threading.Thread(target=self._read_worker, args=(process,), daemon=True).start()
            threading.Thread(target=self._read_errors, args=(process,), daemon=True).start()
            return True

    def _read_errors(self, process):
        with process.stderr as stream:
            for line in stream:
                # Safe activity only. Never forward arbitrary exception/log bodies to the GUI.
                if 'failed' in line.lower() or 'error' in line.lower():
                    self.emit('activity', message='Collection reported a problem; check connection status.')

    def _read_worker(self, process):
        try:
            for line in process.stdout:
                try:
                    if self.worker is not process or self.stopping:
                        continue
                    event = json.loads(line)
                    kind = event.pop('kind')
                    if kind in ('phase', 'status', 'error', 'activity', 'ready'):
                        if 'message' in event:
                            event['message'] = redact(str(event['message']), self.secrets)
                        self.emit(kind, **event)
                        if kind == 'ready':
                            self._start_tunnel(process)
                except (ValueError, TypeError, KeyError):
                    continue
        finally:
            process.wait()
            if not self.stopping and self.worker is process:
                self._stop_tunnel()
                self.emit('phase', state='Needs attention')
                self.emit('activity', message='Collection stopped. Check the connection or earlier app instance.')
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()

    def _start_tunnel(self, worker):
        with self.lock:
            if self.closed or self.stopping or worker is not self.worker or worker.poll() is not None:
                return
            settings = ConfigStore(self.root).settings()
            executable = locate_cloudflared(self.root, settings.cloudflared_path)
            if not executable:
                self.emit('tunnel', state='Not installed', callback='')
                self.emit('activity', message='Tunnel not found. Periodic sync still works; locate cloudflared in Settings.')
                return
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            try:
                process = subprocess.Popen([executable, 'tunnel', '--url', f'http://127.0.0.1:{settings.port}'],
                    cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace', creationflags=flags)
                self.adopt_owned(process, 'tunnel')
                self.emit('tunnel', state='Starting', callback='')
                threading.Thread(target=self._read_tunnel, args=(process,), daemon=True).start()
            except OSError:
                self.emit('tunnel', state='Unavailable', callback='')
                self.emit('activity', message='Could not start cloudflared. Periodic sync continues.')

    def _read_tunnel(self, process):
        status = TunnelStatus()
        last = None
        for line in process.stdout:
            event = status.feed(line)
            if event != last and not self.stopping and self.tunnel is process:
                self.emit(**event)
                last = event.copy()
        process.wait()
        process.stdout.close()
        if not self.stopping and self.tunnel is process:
            self.emit('tunnel', state='Disconnected', callback=status.callback)

    def _stop_tunnel(self):
        process = self.tunnel
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self.tunnel = None

    def stop(self, timeout=4):
        with self.lock:
            self.stopping = True
            self.emit('phase', state='Stopping')
            process = self.worker
            deadline = time.monotonic() + timeout
            if process and process.poll() is None:
                try:
                    process.stdin.write('{"command":"stop"}\n' if isinstance(process.stdin, io.TextIOBase) else b'{"command":"stop"}\n')
                    process.stdin.flush()
                except (OSError, ValueError):
                    pass
            self._stop_tunnel()
            if process and process.poll() is None:
                try:
                    process.wait(timeout=max(0.01, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=1)
            if process and process.stdin and not process.stdin.closed:
                process.stdin.close()
            self.worker = None
            self.emit('tunnel', state='Stopped', callback='')
            self.emit('phase', state='Stopped')

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.stop()
            finally:
                self.job.close()
