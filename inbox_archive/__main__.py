import argparse
import hashlib
import json
import logging
import secrets
import sqlite3
import sys
import threading
from contextlib import ExitStack, closing
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .database import Database, now
from .export import export_archive
from .graph import GraphClient
from .media import download_pending
from .server import make_server
from .sync import SyncEngine


def initialize():
    env = Path('.env')
    if not env.exists():
        template = Path(__file__).resolve().parent.parent / '.env.example'
        text = template.read_text(encoding='utf-8').replace('generate-a-random-value', secrets.token_urlsafe(32))
        with env.open('x', encoding='utf-8') as output:
            output.write(text)
        print('Created .env with a random webhook verification token. Fill in the Meta credentials.')
    else:
        print('.env already exists; kept unchanged.')


def demo(root):
    db = Database(root, 'demo-page')
    db.save_conversation({'id': 'demo-conversation', 'participants': {'data': [
        {'id': 'demo-customer', 'name': 'Sample Customer'}, {'id': 'demo-page', 'name': 'Sample Shop'}]}})
    examples = [('demo-1', 'demo-customer', 'Sample Customer', 'Magkano po ang product?', '2026-11-20T09:00:00+0800'),
                ('demo-2', 'demo-page', 'Sample Shop', '₱500 po. Due sa November 25.', '2026-11-20T09:01:00+0800'),
                ('demo-3', 'demo-customer', 'Sample Customer', 'Paid na po, attached ang receipt.', '2026-11-24T10:30:12+0800')]
    for mid, sender, name, text, stamp in examples:
        db.save_message('demo-conversation', {'id': mid, 'from': {'id': sender, 'name': name},
            'message': text, 'created_time': stamp})
    # A real local SVG fixture, clearly labelled sample; no Facebook requests.
    fixture = b'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="200"><rect width="480" height="200" fill="#e8eef6"/><text x="24" y="80" font-size="24">DEMO RECEIPT - NOT REAL</text><text x="24" y="125" font-size="24">PHP 500</text></svg>'
    db.save_message('demo-conversation', {'id': 'demo-3', 'from': {'id': 'demo-customer', 'name': 'Sample Customer'},
        'message': examples[-1][3], 'created_time': examples[-1][4],
        'attachments': {'data': [{'id': 'demo-receipt', 'name': 'sample-receipt.svg'}]}})
    (db.root / 'attachments').mkdir(exist_ok=True)
    (db.root / 'attachments/sample-receipt.svg').write_bytes(fixture)
    db.execute("UPDATE attachments SET local_path=?,status='downloaded',bytes=?,sha256=? WHERE message_id='demo-3'",
        ('attachments/sample-receipt.svg', len(fixture), hashlib.sha256(fixture).hexdigest()))
    db.execute('UPDATE conversations SET complete=1,last_synced=?', (now(),))
    print(json.dumps(db.status(), indent=2))
    print(f'Demo archive: {db.path}')


def run_service(db, settings):
    wake = threading.Event()
    stop = threading.Event()
    engine = make_engine(db, settings)

    def worker():
        while not stop.is_set():
            wake.clear()
            try:
                engine.run()
                download_pending(db, settings.max_attachment_mb)
            except Exception:
                db.set_meta('last_error', 'Unexpected worker failure; check application version and database/disk access')
                logging.error('Worker encountered an unexpected error; will retry next cycle.')
            # Coalesce webhook bursts; avoid hitting Graph for each individual message.
            if stop.wait(2):
                break
            wake.wait(settings.poll_seconds)

    server = make_server(db, settings, wake)
    thread = threading.Thread(target=worker, name='archive-sync', daemon=True)
    thread.start()
    print(f'Webhook listener: http://127.0.0.1:{settings.port}/webhook', flush=True)
    print(f'Archive: {db.path}\nPress Ctrl+C to stop. Use another terminal for status/export.', flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print('\nStopping webhook listener. Committed messages and checkpoints are safe.')
    finally:
        server.server_close()
        stop.set()
        wake.set()
        thread.join(timeout=5)


def make_engine(db, settings):
    return SyncEngine(db, GraphClient(settings.page_token, settings.api_version, settings.app_secret),
        settings.full_scan_hours, after_page=lambda ids: download_pending(db, settings.max_attachment_mb, message_ids=ids))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Facebook Page inbox archive — local storage, automatic sync, file exports.')
    parser.add_argument('--data-dir', help='Override DATA_DIR (default: data)')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init', help='Create a local .env without overwriting existing settings')
    commands.add_parser('serve', help='Webhook listener plus startup/periodic sync and downloads')
    sync = commands.add_parser('sync', help='One import and attachment download pass')
    sync.add_argument('--full', action='store_true', help='Restart pagination for all discovered conversations')
    commands.add_parser('status', help='Show archive counts, sync errors and missing downloads')
    commands.add_parser('download', help='Retry pending/failed attachment downloads')
    export = commands.add_parser('export', help='Consistent database snapshot, JSONL, text and actual files')
    export.add_argument('--output', help='New output directory (never overwrites an existing export)')
    commands.add_parser('demo', help='Create synthetic examples in demo-data; no credentials needed')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        with ExitStack() as resources:
            return run_command(args, resources)
    except (ValueError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


def run_command(args, resources):
    if args.command == 'init':
        initialize()
        return 0
    if args.command == 'demo':
        demo(Path(args.data_dir or 'demo-data'))
        return 0
    settings = Settings.from_env(args.data_dir)
    if args.command in ('serve', 'sync'):
        settings.require_meta()
    # Existing archives remain readable/exportable without live credentials.
    page_id = settings.page_id
    existing_path = settings.data_dir / 'inbox.sqlite3'
    if not page_id and existing_path.exists():
        with closing(sqlite3.connect(existing_path)) as con:
            page_id = con.execute("SELECT value FROM metadata WHERE key='page_id'").fetchone()[0]
    if not page_id:
        raise ValueError('No Page configured or existing archive. Run init, configure .env, then sync; or run demo.')
    db = Database(settings.data_dir, page_id)
    if args.command in ('serve', 'sync', 'download'):
        resources.enter_context(db.writer_lock())
    if args.command == 'serve':
        run_service(db, settings)
    elif args.command == 'status':
        print(json.dumps(db.status(), ensure_ascii=False, indent=2))
    elif args.command == 'sync':
        engine = make_engine(db, settings)
        engine.run(full=args.full)
        download_pending(db, settings.max_attachment_mb)
        status = db.status()
        print(json.dumps(status, ensure_ascii=False, indent=2))
        incomplete = (status['sync'].get('last_error') or status['incomplete_conversations']
            or status['failed_attachments'] or status['unavailable_attachments'])
        return 1 if incomplete else 0
    elif args.command == 'download':
        download_pending(db, settings.max_attachment_mb)
        print(json.dumps(db.status(), ensure_ascii=False, indent=2))
    elif args.command == 'export':
        destination = args.output or ('exports/' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        print(export_archive(db, destination))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
