import json
import shutil
import hashlib
from pathlib import Path

from .database import Database, now


def export_archive(db, destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Choose a new export directory; existing exports are never overwritten')
    destination.mkdir(parents=True)
    # A consistent SQLite snapshot while live sync continues. Local files are immutable.
    db.backup(destination / 'inbox.sqlite3')
    snapshot = Database(destination, db.page_id)
    conversations = destination / 'conversations'
    conversations.mkdir()
    try:
        with snapshot.connect() as con:
            for conversation in con.execute('SELECT * FROM conversations ORDER BY id'):
                stem = hashlib.sha256(conversation['id'].encode()).hexdigest()[:24]
                with (conversations / (stem + '.jsonl')).open('w', encoding='utf-8') as structured, \
                     (conversations / (stem + '.txt')).open('w', encoding='utf-8') as readable:
                    readable.write(f"Conversation: {conversation['id']}\nComplete API traversal: {bool(conversation['complete'])}\n\n")
                    structured.write(json.dumps({'record_type': 'conversation', **dict(conversation)}, ensure_ascii=False) + '\n')
                    for message in con.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY sort_time,id', (conversation['id'],)):
                        item = dict(message)
                        item['raw'] = json.loads(item.pop('raw_json'))
                        item['record_type'] = 'message'
                        item['attachments'] = []
                        readable.write(f"[{item['created_time'] or 'Unknown time'}] {item['sender_name'] or item['sender_id'] or 'Unknown sender'}: {item['text']}\n")
                        for attachment in con.execute('SELECT * FROM attachments WHERE message_id=? ORDER BY id', (message['id'],)):
                            media = dict(attachment)
                            media['raw'] = json.loads(media.pop('raw_json'))
                            if media['local_path']:
                                source = db.root / media['local_path']
                                target = destination / media['local_path']
                                target.parent.mkdir(exist_ok=True)
                                if source.is_file():
                                    shutil.copy2(source, target)
                                else:
                                    raise FileNotFoundError('An archived attachment file is missing. Run download to recover it, then export again.')
                            item['attachments'].append(media)
                            readable.write(f"  Attachment: {media['local_path'] or media['name'] or media['id']} [{media['status']}]\n")
                        structured.write(json.dumps(item, ensure_ascii=False) + '\n')
            with (destination / 'webhook-events.jsonl').open('w', encoding='utf-8') as output:
                for event in con.execute('SELECT * FROM webhook_events ORDER BY received_at,id'):
                    output.write(json.dumps(dict(event), ensure_ascii=False) + '\n')
        manifest = {'exported_at': now(), 'page_id': db.page_id, 'schema_version': 2,
            'counts': snapshot.status(),
            'timestamps': 'Original API timestamp strings retained; webhook timestamps converted to UTC with milliseconds.',
            'completeness': 'Complete means API pagination exhausted, not proof of all historical Facebook data. Deleted, unavailable or unsupported content may be absent.',
            'files': 'Attachment paths are relative to this export. The SQLite snapshot includes raw API records.'}
        (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        (destination / 'EXPORT_COMPLETE').write_text(now(), encoding='utf-8')
    except BaseException:
        (destination / 'EXPORT_INCOMPLETE').write_text('Export interrupted; do not treat this directory as a complete export.', encoding='utf-8')
        raise
    return destination
