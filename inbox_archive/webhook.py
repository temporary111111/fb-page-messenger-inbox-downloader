import hashlib
import hmac
import json
from datetime import datetime, timezone

from .database import encode, now


def accept_webhook(db, body, signature, app_secret):
    expected = 'sha256=' + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    if not app_secret or not hmac.compare_digest(expected, signature or ''):
        raise PermissionError('Invalid webhook signature')
    payload = json.loads(body)
    if not isinstance(payload, dict) or payload.get('object') != 'page':
        raise ValueError('Expected a Page webhook')
    entries = payload.get('entry', [])
    if not isinstance(entries, list):
        raise ValueError('Invalid webhook entries')
    with db.connect() as con:
        for entry in entries:
            if not isinstance(entry, dict) or str(entry.get('id')) != db.page_id:
                continue
            for event in entry.get('messaging', []) + entry.get('standby', []) + entry.get('changes', []):
                raw = encode(event)
                key = hashlib.sha256(raw.encode()).hexdigest()
                inserted = con.execute('INSERT OR IGNORE INTO webhook_events VALUES(?,?,?)', (key, now(), raw))
                if inserted.rowcount == 0:
                    continue
                message = event.get('message')
                if message and message.get('mid'):
                    sender = event.get('sender') or {}
                    recipient = event.get('recipient') or {}
                    customer = recipient.get('id') if str(sender.get('id')) == db.page_id else sender.get('id')
                    if not customer:
                        continue
                    # Prefer the discovered canonical thread; otherwise merge on the next API scan.
                    existing = con.execute('''SELECT conversation_id FROM messages
                        WHERE sender_id=? AND conversation_id NOT LIKE 'webhook:%' LIMIT 1''', (customer,)).fetchone()
                    cid = existing[0] if existing else 'webhook:' + str(customer)
                    if not con.execute('SELECT 1 FROM conversations WHERE id=?', (cid,)).fetchone():
                        db.save_conversation({'id': cid, 'provisional': True}, con)
                    timestamp = event.get('timestamp')
                    created = datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat(timespec='milliseconds') if isinstance(timestamp, (int, float)) else None
                    normalized = {'id': message['mid'], 'from': sender, 'to': {'data': [recipient]},
                        'created_time': created, 'message': message.get('text', ''),
                        'attachments': message.get('attachments', []),
                        'is_unsupported': message.get('is_unsupported', False)}
                    db.save_message(cid, normalized, con, partial=True)
                    con.execute('UPDATE conversations SET updated_time=NULL WHERE id=?', (cid,))
                edit = event.get('message_edit') or (event.get('value') or {}).get('message_edit')
                if edit and edit.get('mid') and 'text' in edit:
                    con.execute('UPDATE messages SET text=?,last_seen=? WHERE id=?',
                        (edit['text'], now(), edit['mid']))
                    con.execute('''UPDATE conversations SET updated_time=NULL WHERE id IN
                        (SELECT conversation_id FROM messages WHERE id=?)''', (edit['mid'],))
        con.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('last_webhook', now()))
