import hashlib
import hmac
import json
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class GraphError(RuntimeError):
    pass


def clean_next(url):
    """Never persist credentials embedded in Graph pagination links."""
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.netloc != 'graph.facebook.com':
        raise ValueError('Untrusted Graph pagination URL')
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in ('access_token', 'appsecret_proof')]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GraphError('Unexpected Graph redirect refused')


class GraphClient:
    def __init__(self, token, version, app_secret, timeout_seconds=10, retries=1,
                 hard_timeout_seconds=15):
        if timeout_seconds <= 0 or retries < 1 or hard_timeout_seconds < 0:
            raise ValueError('Graph request timeout and retries must be positive')
        self.token = token
        self.version = version
        self.proof = hmac.new(app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()
        self.opener = build_opener(NoRedirect())
        # A failed cursor is checkpointed and retried on the next archive pass.
        # Do not let one unavailable thread stall discovery/import of the whole inbox.
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.hard_timeout_seconds = hard_timeout_seconds
        # Windows curl uses the OS networking stack and enforces --max-time even
        # when Python's SSL read fails to honour a socket timeout.
        self._curl_path = shutil.which('curl.exe') if sys.platform == 'win32' else None

    def get(self, path, params=None):
        return self._get_with_curl(path, params) if self._curl_path else self._get_direct(path, params)

    @staticmethod
    def _config_value(value):
        return value.replace('\\', '\\\\').replace('"', '\\"')

    def _url_for(self, path, params):
        if '://' in path or path.startswith('//'):
            url = clean_next(path)
        else:
            url = f'https://graph.facebook.com/{self.version}/{path.lstrip("/")}'
        separator = '&' if '?' in url else '?'
        return url + separator + urlencode({**(params or {}), 'appsecret_proof': self.proof})

    def _get_with_curl(self, path, params=None):
        url = self._url_for(path, params)
        config = '\n'.join((
            f'url = "{self._config_value(url)}"',
            f'header = "Authorization: Bearer {self._config_value(self.token)}"',
            'header = "User-Agent: PageInboxArchive/1"',
            f'connect-timeout = {self.timeout_seconds}',
            f'max-time = {self.hard_timeout_seconds}',
            'silent',
            'show-error',
        ))
        for attempt in range(self.retries):
            try:
                payload = self._run_curl(config)
                if 'error' in payload:
                    raise GraphError('Meta returned an API error; check permissions and Page token')
                return payload
            except (OSError, subprocess.TimeoutExpired, ValueError):
                if attempt < self.retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise GraphError(f'Could not reach Meta after {self.retries} attempts') from None

    def _run_curl(self, config):
        # Do not use capture_output on Windows: its reader threads can remain stuck
        # when a network process is terminated. Temporary handles leave no response
        # payload on disk once closed.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen([self._curl_path, '--config', '-'], stdin=subprocess.PIPE,
                stdout=output, stderr=errors)
            try:
                process.stdin.write(config.encode('utf-8'))
                process.stdin.close()
                process.wait(timeout=self.hard_timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise
            finally:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
            if process.returncode:
                raise OSError('curl request failed')
            output.seek(0)
            return json.loads(output.read().decode('utf-8'))

    def _get_direct(self, path, params=None):
        url = self._url_for(path, params)
        request = Request(url, headers={'Authorization': f'Bearer {self.token}', 'User-Agent': 'PageInboxArchive/1'})
        for attempt in range(self.retries):
            try:
                with self.opener.open(request, timeout=self.timeout_seconds) as response:
                    result = json.load(response)
                if 'error' in result:
                    raise GraphError('Meta returned an API error; check permissions and Page token')
                return result
            except HTTPError as exc:
                try:
                    error = json.loads(exc.read()).get('error', {})
                except (ValueError, AttributeError):
                    error = {}
                code = error.get('code')
                transient = exc.code == 429 or exc.code >= 500 or code in (1, 2, 4, 17, 32, 613)
                if transient and attempt < self.retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                # Meta error messages can contain URLs/tokens. Store only safe numeric diagnostics.
                safe_code = code if isinstance(code, int) else 'unknown'
                raise GraphError(f'Meta HTTP {exc.code}, API code {safe_code}. Check token, permissions, app mode and rate limits.') from None
            except (URLError, TimeoutError, OSError):
                if attempt < self.retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise GraphError(f'Could not reach Meta after {self.retries} attempts') from None
