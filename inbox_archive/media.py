import hashlib
import mimetypes
import os
from http.client import HTTPException
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def validate_media_url(url):
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    allowed = ('fbcdn.net', 'fbsbx.com', 'facebook.com', 'cdninstagram.com')
    if (parts.scheme != 'https' or parts.username or parts.password
            or parts.port not in (None, 443)
            or not any(host == domain or host.endswith('.' + domain) for domain in allowed)):
        raise ValueError('Attachment URL is outside the allowed Meta CDN hosts')


class MediaRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_media_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_media(url):
    validate_media_url(url)
    return build_opener(MediaRedirect()).open(Request(url, headers={'User-Agent': 'PageInboxArchive/1'}), timeout=45)


def download_pending(db, max_mb=100, opener=open_media, message_ids=None):
    folder = db.root / 'attachments'
    folder.mkdir(exist_ok=True)
    # Recover files removed outside the app. Bounded cursor iteration avoids loading the archive in RAM.
    if message_ids is None:
        with db.connect() as con:
            for saved in con.execute("SELECT id,local_path FROM attachments WHERE status='downloaded'"):
                if not saved['local_path'] or not (db.root / saved['local_path']).is_file():
                    con.execute("UPDATE attachments SET status='pending',local_path=NULL WHERE id=?", (saved['id'],))
    if message_ids == []:
        return
    message_filter = (' AND message_id IN (' + ','.join('?' for _ in message_ids) + ')') if message_ids is not None else ''
    last_id = ''
    while True:
        rows = db.rows("""SELECT * FROM attachments WHERE status IN ('pending','failed')
            AND url IS NOT NULL AND id>?""" + message_filter + ' ORDER BY id LIMIT 50', (last_id, *(message_ids or [])))
        if not rows:
            break
        for row in rows:
            last_id = row['id']
            temporary = folder / (row['id'] + '.part')
            try:
                validate_media_url(row['url'])
                digest = hashlib.sha256()
                length = 0
                with opener(row['url']) as response:
                    content_type = response.headers.get('Content-Type', '').split(';')[0].strip()
                    if content_type in ('text/html', 'application/xhtml+xml'):
                        raise ValueError('Attachment returned a web page instead of a file')
                    extension = mimetypes.guess_extension(content_type) or '.bin'
                    expected = response.headers.get('Content-Length')
                    if expected is not None and int(expected) > max_mb * 1024 * 1024:
                        raise ValueError('Attachment exceeds MAX_ATTACHMENT_MB')
                    with temporary.open('wb') as output:
                        while chunk := response.read(128 * 1024):
                            length += len(chunk)
                            if length > max_mb * 1024 * 1024:
                                raise ValueError('Attachment exceeds MAX_ATTACHMENT_MB')
                            output.write(chunk)
                            digest.update(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                if not length:
                    raise ValueError('Attachment download was empty')
                if expected is not None and length != int(expected):
                    raise ValueError('Attachment download was truncated; will retry')
                relative = 'attachments/' + row['id'] + extension
                os.replace(temporary, db.root / relative)
                db.execute('''UPDATE attachments SET local_path=?,sha256=?,bytes=?,
                    status='downloaded',attempts=attempts+1,error=NULL WHERE id=?''',
                    (relative, digest.hexdigest(), length, row['id']))
            except (OSError, ValueError, HTTPException) as exc:
                temporary.unlink(missing_ok=True)
                # Exception URLs may include signed CDN credentials, so persist safe diagnostics only.
                detail = str(exc) if isinstance(exc, ValueError) else 'Download failed; check network or refresh expired URL with sync --full'
                db.execute("UPDATE attachments SET status='failed',attempts=attempts+1,error=? WHERE id=?", (detail, row['id']))
