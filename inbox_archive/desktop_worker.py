"""Internal collector process; stdout is a credential-free JSON event stream."""
import json
import logging
import sys
import threading
from concurrent.futures import CancelledError

from .database import Database
from .desktop_settings import ConfigStore
from .graph import GraphClient
from .media import download_pending
from .server import make_server
from .sync import SyncEngine


def attach_standard_pipes():
    """Windowed PyInstaller builds have no Python streams until explicitly attached."""
    if sys.platform != 'win32':
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    for name, code, mode, flags in [('stdin', -10, 'r', 0), ('stdout', -11, 'w', 1), ('stderr', -12, 'w', 1)]:
        if getattr(sys, name) is None:
            handle = kernel.GetStdHandle(code & 0xffffffff)
            if handle and handle != ctypes.c_void_p(-1).value:
                fd = msvcrt.open_osfhandle(handle, flags)
                import os
                setattr(sys, name, os.fdopen(fd, mode, encoding='utf-8', buffering=1))


def run_worker(root):
    attach_standard_pipes()
    def emit(kind, **payload):
        print(json.dumps({'kind': kind, **payload}), flush=True)
    stop, wake = threading.Event(), threading.Event()
    def control():
        for line in sys.stdin:
            try:
                if json.loads(line).get('command') == 'stop':
                    break
            except (ValueError, AttributeError):
                continue
        stop.set()
        wake.set()
    threading.Thread(target=control, daemon=True).start()
    server = None
    try:
        store = ConfigStore(root)
        settings = store.validate(store.read())
        db = Database(settings.data_dir, settings.page_id)
        with db.writer_lock():
            server = make_server(db, settings, wake)
            threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.2}, daemon=True).start()
            emit('ready')
            graph = GraphClient(settings.page_token, settings.api_version, settings.app_secret)
            def after_page(ids):
                download_pending(db, settings.max_attachment_mb, message_ids=ids, stop=stop)
                emit('status', counts=db.status())
            engine = SyncEngine(db, graph, settings.full_scan_hours, after_page=after_page, stop=stop)
            while not stop.is_set():
                wake.clear()
                emit('phase', state='Syncing')
                engine.run()
                download_pending(db, settings.max_attachment_mb, stop=stop)
                counts = db.status()
                emit('status', counts=counts)
                problem = (counts['sync'].get('last_error') or counts['incomplete_conversations']
                    or counts['failed_attachments'] or counts['unavailable_attachments'] or counts['pending_attachments'])
                emit('phase', state='Needs attention' if problem else 'Up to date')
                emit('activity', message='Sync needs attention.' if problem else 'Sync finished. Archive saved on this PC.')
                if stop.wait(1):
                    break
                wake.wait(settings.poll_seconds)
    except CancelledError:
        pass
    except ValueError as exc:
        emit('error', message=str(exc))
        return 1
    except OSError:
        emit('error', message='Could not open the archive or webhook port. Another app may already be running.')
        return 1
    except Exception:
        emit('error', message='Collection could not continue. Check disk access, settings and connection, then restart sync.')
        return 1
    finally:
        if server:
            server.shutdown()
            server.server_close()
    return 0
