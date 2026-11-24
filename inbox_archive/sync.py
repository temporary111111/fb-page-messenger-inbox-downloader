import logging
from concurrent.futures import CancelledError
from datetime import datetime, timedelta, timezone

from .database import now
from .graph import GraphError, clean_next


log = logging.getLogger(__name__)
MESSAGE_FIELDS = 'id,created_time,from,to,message,attachments,is_unsupported'


class SyncEngine:
    def __init__(self, db, graph, full_scan_hours=24, after_page=None, stop=None):
        self.db = db
        self.graph = graph
        self.full_scan_hours = full_scan_hours
        self.after_page = after_page
        self.stop = stop

    def check_stop(self):
        if self.stop and self.stop.is_set():
            raise CancelledError('Sync stopped')

    def run(self, full=False):
        self.db.set_meta('last_attempt', now())
        self.db.set_meta('last_error', '')
        path = f'{self.db.page_id}/conversations'
        params = {'platform': 'MESSENGER', 'fields': 'id,updated_time,participants', 'limit': 100}
        failures = 0
        seen = set()
        pending = []
        try:
            # Discover the entire inbox before one slow message thread can delay later
            # conversation pages. This is especially important for the first archive run.
            while path:
                self.check_stop()
                if path in seen:
                    raise GraphError('Repeated conversation pagination cursor')
                seen.add(path)
                page = self.graph.get(path, params)
                for conversation in page.get('data', []):
                    self.check_stop()
                    cid = str(conversation['id'])
                    previous = self.db.rows('SELECT * FROM conversations WHERE id=?', (cid,))
                    self.db.save_conversation(conversation)
                    previous = previous[0] if previous else None
                    pending.append((cid, previous, conversation.get('updated_time')))
                next_url = page.get('paging', {}).get('next')
                path, params = (clean_next(next_url), None) if next_url else (None, None)
            self.db.set_meta('last_discovery_success', now())
            for cid, previous, updated_time in pending:
                self.check_stop()
                cutoff = datetime.now(timezone.utc) - timedelta(hours=self.full_scan_hours)
                recent_full = (previous and previous['last_full_scan']
                    and datetime.fromisoformat(previous['last_full_scan']) > cutoff)
                if (not full and previous and previous['complete'] and recent_full
                        and updated_time and previous['updated_time'] == updated_time):
                    continue
                try:
                    rescan = full or bool(previous and previous['complete'] and not recent_full)
                    self.sync_conversation(cid, previous, rescan)
                except (GraphError, ValueError) as exc:
                    failures += 1
                    self.db.execute('UPDATE conversations SET error=?,complete=0 WHERE id=?', (str(exc), cid))
                    log.warning('Conversation import failed; checkpoint retained. %s', exc)
            self.db.set_meta('last_error', f'{failures} conversation(s) failed' if failures else '')
            if not failures:
                self.db.set_meta('last_sync_success', now())
        except (GraphError, ValueError) as exc:
            self.db.set_meta('last_error', str(exc))
            log.warning('Sync failed; archive remains available. %s', exc)

    def sync_conversation(self, cid, previous=None, full=False):
        # Resume only incomplete runs; every fresh scan starts at the newest page.
        cursor = previous.get('cursor') if previous and not previous['complete'] and not full else None
        incremental = bool(previous and previous['complete'] and not full)
        path = cursor or f'{cid}/messages'
        params = None if cursor else {'fields': MESSAGE_FIELDS, 'limit': 100}
        self.db.execute('UPDATE conversations SET complete=0,error=NULL WHERE id=?', (cid,))
        seen = set()
        while path:
            self.check_stop()
            if path in seen:
                raise GraphError('Repeated message pagination cursor; run sync --full to reset')
            seen.add(path)
            page = self.graph.get(path, params)
            messages = page.get('data', [])
            known_page = incremental and bool(messages) and all(
                self.db.rows('SELECT 1 FROM messages WHERE id=? AND conversation_id=? AND api_imported=1', (message['id'], cid))
                for message in messages)
            for message in messages:
                attachments = message.get('attachments')
                if isinstance(attachments, dict):
                    next_attachment = attachments.get('paging', {}).get('next')
                    attachment_seen = set()
                    while next_attachment:
                        self.check_stop()
                        next_attachment = clean_next(next_attachment)
                        if next_attachment in attachment_seen:
                            raise GraphError('Repeated attachment pagination cursor')
                        attachment_seen.add(next_attachment)
                        extra = self.graph.get(next_attachment)
                        attachments.setdefault('data', []).extend(extra.get('data', []))
                        next_attachment = extra.get('paging', {}).get('next')
            next_url = page.get('paging', {}).get('next')
            next_cursor = clean_next(next_url) if next_url else None
            exhausted = not next_cursor
            if known_page:
                next_cursor = None
            # Commit data and cursor together: a crash cannot skip a fetched page.
            with self.db.connect() as con:
                for message in messages:
                    self.db.save_message(cid, message, con)
                con.execute('''UPDATE conversations SET cursor=?,complete=?,last_synced=?,
                    last_full_scan=CASE WHEN ? THEN ? ELSE last_full_scan END,error=NULL WHERE id=?''',
                    (next_cursor, int(not next_cursor), now(), int(exhausted), now(), cid))
                if cursor and not next_cursor:
                    # New messages may have arrived while we resumed older pages. Scan the head next cycle.
                    con.execute('UPDATE conversations SET updated_time=NULL WHERE id=?', (cid,))
            if messages and self.after_page:
                self.after_page([message['id'] for message in messages])
            path, params = next_cursor, None
