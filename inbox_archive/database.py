import hashlib
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .graph import clean_next


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def encode(value):
    def scrub(item):
        if isinstance(item, dict):
            cleaned = {}
            for key, child in item.items():
                if key in ('access_token', 'appsecret_proof'):
                    continue
                if key in ('next', 'previous') and isinstance(child, str):
                    try:
                        child = clean_next(child)
                    except ValueError:
                        child = None
                cleaned[key] = scrub(child)
            return cleaned
        if isinstance(item, list):
            return [scrub(child) for child in item]
        return item
    return json.dumps(scrub(value), ensure_ascii=False, separators=(',', ':'))


def attachment_url(item):
    image = item.get('image_data') or {}
    video = item.get('video_data') or {}
    return (item.get('file_url') or image.get('animated_gif_url') or image.get('url')
            or video.get('url') or (item.get('payload') or {}).get('url'))


class Database:
    def __init__(self, root, page_id):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'inbox.sqlite3'
        self.page_id = page_id
        with self.connect() as con:
            con.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS conversations(
                    id TEXT PRIMARY KEY, updated_time TEXT, raw_json TEXT NOT NULL,
                    complete INTEGER NOT NULL DEFAULT 0, cursor TEXT,
                    last_synced TEXT, last_full_scan TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS messages(
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    sender_id TEXT, sender_name TEXT, created_time TEXT, sort_time REAL,
                    text TEXT NOT NULL, unsupported INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                    api_imported INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS messages_conversation_time
                    ON messages(conversation_id, sort_time, id);
                CREATE TABLE IF NOT EXISTS attachments(
                    id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(id),
                    name TEXT, url TEXT, raw_json TEXT NOT NULL, local_path TEXT,
                    sha256 TEXT, bytes INTEGER, status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0, error TEXT);
                CREATE INDEX IF NOT EXISTS attachments_status ON attachments(status);
                CREATE TABLE IF NOT EXISTS webhook_events(
                    id TEXT PRIMARY KEY, received_at TEXT NOT NULL, raw_json TEXT NOT NULL);
            ''')
            existing = con.execute("SELECT value FROM metadata WHERE key='page_id'").fetchone()
            if existing and existing[0] != page_id:
                raise ValueError('This data directory belongs to a different Page. Choose another --data-dir.')
            columns = {row['name'] for row in con.execute('PRAGMA table_info(messages)')}
            if 'api_imported' not in columns:
                con.execute('ALTER TABLE messages ADD COLUMN api_imported INTEGER NOT NULL DEFAULT 0')
            con.execute("INSERT OR IGNORE INTO metadata VALUES('page_id', ?)", (page_id,))
            con.execute("INSERT OR REPLACE INTO metadata VALUES('schema_version', '2')")

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            with con:
                yield con
        finally:
            con.close()

    def rows(self, sql, params=()):
        with self.connect() as con:
            return [dict(row) for row in con.execute(sql, params)]

    @contextmanager
    def writer_lock(self):
        with (self.root / '.sync.lock').open('a+b') as lock:
            if lock.tell() == 0:
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError('Another sync/downloader is running for this archive. Stop it first.') from None
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def execute(self, sql, params=()):
        with self.connect() as con:
            con.execute(sql, params)

    def set_meta(self, key, value):
        self.execute('INSERT OR REPLACE INTO metadata VALUES(?, ?)', (key, value))

    def save_conversation(self, item, con=None):
        if con is None:
            with self.connect() as con:
                return self.save_conversation(item, con)
        cid = str(item['id'])
        con.execute('''INSERT INTO conversations(id, updated_time, raw_json) VALUES(?,?,?)
            ON CONFLICT(id) DO UPDATE SET updated_time=excluded.updated_time, raw_json=excluded.raw_json''',
            (cid, item.get('updated_time'), encode(item)))
        for participant in item.get('participants', {}).get('data', []):
            provisional = 'webhook:' + str(participant['id'])
            if provisional != cid:
                con.execute('UPDATE messages SET conversation_id=? WHERE conversation_id=?', (cid, provisional))
                con.execute('DELETE FROM conversations WHERE id=?', (provisional,))

    def save_message(self, cid, item, con=None, partial=False):
        if con is None:
            with self.connect() as con:
                return self.save_message(cid, item, con, partial)
        stamp = now()
        created = item.get('created_time')
        try:
            order = datetime.fromisoformat(created.replace('Z', '+00:00')).timestamp()
        except (ValueError, AttributeError):
            order = None
        sender = item.get('from') or {}
        conflict = 'DO NOTHING' if partial else '''DO UPDATE SET
            conversation_id=excluded.conversation_id, sender_id=excluded.sender_id,
            sender_name=excluded.sender_name, created_time=excluded.created_time,
            sort_time=excluded.sort_time, text=excluded.text, unsupported=excluded.unsupported,
            raw_json=excluded.raw_json, last_seen=excluded.last_seen,api_imported=1'''
        inserted = con.execute(f'''INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) {conflict}''',
            (item['id'], cid, sender.get('id'), sender.get('name'), created, order,
             item.get('message', ''), bool(item.get('is_unsupported')), encode(item), stamp, stamp, int(not partial)))
        if partial and not inserted.rowcount:
            return
        attachments = item.get('attachments', {})
        items = attachments.get('data', []) if isinstance(attachments, dict) else attachments
        for index, attachment in enumerate(items):
            key = hashlib.sha256(f"{item['id']}:{attachment.get('id', index)}".encode()).hexdigest()
            provisional_key = hashlib.sha256(f"{item['id']}:{index}".encode()).hexdigest()
            url = attachment_url(attachment)
            if key != provisional_key:
                # Only reuse downloaded bytes when identity is established by the URL, never by order.
                candidates = con.execute('SELECT * FROM attachments WHERE message_id=?', (item['id'],)).fetchall()
                provisional = [row for row in candidates if 'id' not in json.loads(row['raw_json'])]
                match = next((row for row in provisional if url and row['url'] == url), None)
                if not match:
                    match = next((row for row in provisional if row['id'] == provisional_key), None)
                if match and not any(row['id'] == key for row in candidates):
                    if match['url'] != url:
                        con.execute("""UPDATE attachments SET status='pending',local_path=NULL,
                            sha256=NULL,bytes=NULL WHERE id=?""", (match['id'],))
                    con.execute('UPDATE attachments SET id=? WHERE id=?', (key, match['id']))
            con.execute('''INSERT INTO attachments(id,message_id,name,url,raw_json,status)
                VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=COALESCE(excluded.name,attachments.name),
                url=COALESCE(excluded.url,attachments.url),raw_json=excluded.raw_json,
                status=CASE WHEN attachments.status='downloaded' THEN 'downloaded'
                    WHEN excluded.url IS NOT NULL THEN 'pending' ELSE attachments.status END''',
                (key, item['id'], attachment.get('name'), url, encode(attachment),
                 'pending' if url else 'unavailable'))

    def status(self):
        with self.connect() as con:
            count = lambda sql: con.execute(sql).fetchone()[0]
            return {'page_id': self.page_id,
                'conversations': count("SELECT COUNT(*) FROM conversations WHERE id NOT LIKE 'webhook:%'"),
                'incomplete_conversations': count('SELECT COUNT(*) FROM conversations WHERE complete=0'),
                'messages': count('SELECT COUNT(*) FROM messages'),
                'unsupported_messages': count('SELECT COUNT(*) FROM messages WHERE unsupported=1'),
                'attachments': count('SELECT COUNT(*) FROM attachments'),
                'downloaded_attachments': count("SELECT COUNT(*) FROM attachments WHERE status='downloaded'"),
                'failed_attachments': count("SELECT COUNT(*) FROM attachments WHERE status='failed'"),
                'pending_attachments': count("SELECT COUNT(*) FROM attachments WHERE status='pending'"),
                'unavailable_attachments': count("SELECT COUNT(*) FROM attachments WHERE status='unavailable'"),
                'webhook_events': count('SELECT COUNT(*) FROM webhook_events'),
                'sync': {row['key']: row['value'] for row in con.execute(
                    "SELECT * FROM metadata WHERE key NOT IN ('page_id','schema_version')")}}

    def backup(self, target):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.resolve() == self.path:
            raise ValueError('Backup destination must differ from the live database')
        with self.connect() as source, closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
