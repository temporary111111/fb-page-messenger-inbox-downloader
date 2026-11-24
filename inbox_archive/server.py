import hmac
import json
import logging
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .webhook import accept_webhook


log = logging.getLogger(__name__)
MAX_WEBHOOK_BYTES = 2 * 1024 * 1024


def make_server(db, settings, wake):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'InboxArchive'
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, format, *args):
            # Default request logging exposes verify tokens in the query string.
            pass

        def reply(self, code, body, content_type='text/plain; charset=utf-8'):
            data = body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            route = urlsplit(self.path)
            if route.path == '/health':
                return self.reply(200, 'ok')
            if route.path != '/webhook':
                return self.reply(404, 'Not found. Use the local CLI for status and export.')
            query = parse_qs(route.query)
            token = query.get('hub.verify_token', [''])[0]
            if (query.get('hub.mode') == ['subscribe'] and settings.verify_token
                    and hmac.compare_digest(token, settings.verify_token)):
                return self.reply(200, query.get('hub.challenge', [''])[0])
            self.reply(403, 'Verification failed')

        def do_POST(self):
            if urlsplit(self.path).path != '/webhook':
                return self.reply(404, 'Not found')
            if self.headers.get('Transfer-Encoding'):
                return self.reply(400, 'Transfer encoding is not supported')
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                return self.reply(400, 'Invalid content length')
            if not 0 < length <= MAX_WEBHOOK_BYTES:
                return self.reply(413, 'Invalid payload size')
            try:
                body = self.rfile.read(length)
                if len(body) != length:
                    return self.reply(400, 'Incomplete payload')
                accept_webhook(db, body, self.headers.get('X-Hub-Signature-256'), settings.app_secret)
            except PermissionError:
                return self.reply(403, 'Invalid signature')
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                return self.reply(400, 'Invalid webhook payload')
            except Exception:
                log.error('Webhook persistence failed; returning 503 so Meta can retry.')
                return self.reply(503, 'Please retry')
            wake.set()
            self.reply(200, 'EVENT_RECEIVED')

    # Loopback only. Cloudflare publishes this port; archive files are never served.
    class ExclusiveServer(ThreadingHTTPServer):
        allow_reuse_address = False

        def server_bind(self):
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    return ExclusiveServer(('127.0.0.1', settings.port), Handler)
