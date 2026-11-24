import json
import logging
from concurrent.futures import CancelledError
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlsplit

from .database import now
from .graph import GraphError, clean_next


log = logging.getLogger(__name__)
MESSAGE_FIELDS = 'id,created_time,from,to,message,attachments,is_unsupported'
# Full attachment metadata can make larger Graph responses slow or unreliable.
# Pagination still continues until Meta reports no next cursor.
MESSAGE_PAGE_SIZE = 5


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

    @staticmethod
    def _attachment_key(item):
        if isinstance(item, dict) and item.get('id') is not None:
            return ('id', str(item['id']))
        return ('json', json.dumps(item, sort_keys=True, ensure_ascii=False))

    @staticmethod
    def message_request(cid, cursor=None):
        if not cursor:
            return f'{cid}/messages', {'fields': MESSAGE_FIELDS, 'limit': MESSAGE_PAGE_SIZE}
        after = parse_qs(urlsplit(cursor).query).get('after', [''])[0]
        if after:
            # Meta's opaque `next` URLs can become expensive on large threads. Keep
            # the cursor but make the next page request explicitly bounded.
            return f'{cid}/messages?{urlencode({"after": after})}', {
                'fields': MESSAGE_FIELDS, 'limit': MESSAGE_PAGE_SIZE}
        return cursor, None

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
        resuming = bool(cursor)
        path, params = self.message_request(cid, cursor)
        self.db.execute('UPDATE conversations SET complete=0,error=NULL WHERE id=?', (cid,))
        seen = set()
        while path:
            self.check_stop()
            request_key = cursor or f'{cid}:first-page'
            if request_key in seen:
                raise GraphError('Repeated message pagination cursor; run sync --full to reset')
            seen.add(request_key)
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
                    seen_attachment_keys = {
                        self._attachment_key(item) for item in attachments.get('data', [])
                    }
                    while next_attachment:
                        self.check_stop()
                        next_attachment = clean_next(next_attachment)
                        if next_attachment in attachment_seen:
                            raise GraphError('Repeated attachment pagination cursor')
                        attachment_seen.add(next_attachment)
                        extra = self.graph.get(next_attachment)
                        extra_items = extra.get('data', [])
                        if not extra_items:
                            break
                        new_items = [
                            item for item in extra_items
                            if self._attachment_key(item) not in seen_attachment_keys
                        ]
                        if not new_items:
                            # Meta can return the same attachment(s) with a new paging.next cursor indefinitely.
                            break
                        seen_attachment_keys.update(self._attachment_key(item) for item in new_items)
                        attachments.setdefault('data', []).extend(new_items)
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
                if resuming and not next_cursor:
                    # New messages may have arrived while we resumed older pages. Scan the head next cycle.
                    con.execute('UPDATE conversations SET updated_time=NULL WHERE id=?', (cid,))
            if messages and self.after_page:
                self.after_page([message['id'] for message in messages])
            cursor = next_cursor
            path, params = self.message_request(cid, cursor) if cursor else (None, None)
