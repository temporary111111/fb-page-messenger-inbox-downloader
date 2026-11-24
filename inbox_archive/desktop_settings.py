"""Portable desktop configuration, independent of the CLI's process environment."""
import os
import re
import secrets
import sqlite3
import shutil
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings


DEFAULTS = {'META_PAGE_ID': '', 'META_PAGE_ACCESS_TOKEN': '', 'META_APP_SECRET': '',
    'WEBHOOK_VERIFY_TOKEN': '', 'META_API_VERSION': 'v27.0', 'DATA_DIR': 'data',
    'PORT': '8787', 'POLL_SECONDS': '300', 'FULL_SCAN_HOURS': '24',
    'MAX_ATTACHMENT_MB': '100', 'CLOUDFLARED_PATH': ''}
SECRET_KEYS = ('META_PAGE_ACCESS_TOKEN', 'META_APP_SECRET', 'WEBHOOK_VERIFY_TOKEN')


def parse_env(text):
    values = {}
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('\"\'')
    return values


def validate_binding(settings):
    database = settings.data_dir / 'inbox.sqlite3'
    if database.exists():
        try:
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as con:
                row = con.execute("SELECT value FROM metadata WHERE key='page_id'").fetchone()
        except sqlite3.Error:
            raise ValueError('That folder does not contain a readable Inbox Archive database.') from None
        if not row or row[0] != settings.page_id:
            raise ValueError('This archive belongs to another Page. Select a different archive folder.')


class ConfigStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / '.env'
        self.generated_token = secrets.token_urlsafe(32)

    def read(self):
        try:
            stored = parse_env(self.path.read_text(encoding='utf-8-sig')) if self.path.exists() else {}
        except UnicodeError:
            raise ValueError('Settings could not be decoded. Enter the configuration again; the old file will be kept as a backup.') from None
        values = {**DEFAULTS, **stored}
        if not values['WEBHOOK_VERIFY_TOKEN'] or values['WEBHOOK_VERIFY_TOKEN'] == 'generate-a-random-value':
            values['WEBHOOK_VERIFY_TOKEN'] = self.generated_token
        return values

    def ensure_verify_token(self):
        if not self.path.exists():
            return
        original = parse_env(self.path.read_text(encoding='utf-8-sig'))
        if not original.get('WEBHOOK_VERIFY_TOKEN') or original['WEBHOOK_VERIFY_TOKEN'] == 'generate-a-random-value':
            values = self.read()
            self.validate(values)
            self.save(values)

    def settings(self, values=None):
        return Settings.from_values(self.read() if values is None else values, self.root)

    def validate(self, values):
        for key, value in values.items():
            if not isinstance(value, str) or any(char in value for char in ('\n', '\r', '\x00')):
                raise ValueError(f'Invalid value for {key}. Use a single line.')
        settings = self.settings(values)
        settings.require_meta()
        if not re.fullmatch(r'\d+', settings.page_id):
            raise ValueError('Page ID must contain numbers only.')
        validate_binding(settings)
        return settings

    def save(self, values):
        try:
            stored = self.read()
        except ValueError:
            stored = {**DEFAULTS, 'WEBHOOK_VERIFY_TOKEN': self.generated_token}
        combined = {**stored, **values}
        self.validate(combined)
        try:
            original = self.path.read_text(encoding='utf-8-sig') if self.path.exists() else ''
        except UnicodeError:
            backup = self.root / ('.env.backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
            shutil.copy2(self.path, backup)
            original = ''
        lines, written = [], set()
        for line in original.splitlines():
            key = line.split('=', 1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else None
            if key in DEFAULTS:
                if key not in written:
                    lines.append(key + '=' + combined[key])
                    written.add(key)
            else:
                lines.append(line)
        lines.extend(key + '=' + combined[key] for key in DEFAULTS if key not in written)
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.root,
                    prefix='.settings-', suffix='.tmp', delete=False) as output:
                temporary = Path(output.name)
                output.write('\n'.join(lines) + '\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        return self.settings(combined)
