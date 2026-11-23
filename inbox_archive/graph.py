import hashlib
import hmac
import json
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
    def __init__(self, token, version, app_secret):
        self.token = token
        self.version = version
        self.proof = hmac.new(app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()
        self.opener = build_opener(NoRedirect())

    def get(self, path, params=None):
        if '://' in path or path.startswith('//'):
            url = clean_next(path)
        else:
            url = f'https://graph.facebook.com/{self.version}/{path.lstrip("/")}'
        separator = '&' if '?' in url else '?'
        url += separator + urlencode({**(params or {}), 'appsecret_proof': self.proof})
        request = Request(url, headers={'Authorization': f'Bearer {self.token}', 'User-Agent': 'PageInboxArchive/1'})
        for attempt in range(4):
            try:
                with self.opener.open(request, timeout=45) as response:
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
                if transient and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                # Meta error messages can contain URLs/tokens. Store only safe numeric diagnostics.
                safe_code = code if isinstance(code, int) else 'unknown'
                raise GraphError(f'Meta HTTP {exc.code}, API code {safe_code}. Check token, permissions, app mode and rate limits.') from None
            except (URLError, TimeoutError, OSError):
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise GraphError('Could not reach Meta after four attempts') from None
