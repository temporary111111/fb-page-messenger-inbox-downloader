import os
import re
from dataclasses import dataclass, field
from pathlib import Path


def load_env(path=Path('.env')):
    """Small .env reader; existing process environment takes priority."""
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    page_id: str
    page_token: str = field(repr=False)
    app_secret: str = field(repr=False)
    verify_token: str = field(repr=False)
    api_version: str = 'v27.0'
    port: int = 8787
    poll_seconds: int = 300
    full_scan_hours: int = 24
    max_attachment_mb: int = 100

    @classmethod
    def from_env(cls, data_dir=None):
        load_env()
        settings = cls(
            Path(data_dir or os.getenv('DATA_DIR', 'data')).resolve(),
            os.getenv('META_PAGE_ID', ''), os.getenv('META_PAGE_ACCESS_TOKEN', ''),
            os.getenv('META_APP_SECRET', ''), os.getenv('WEBHOOK_VERIFY_TOKEN', ''),
            os.getenv('META_API_VERSION', 'v27.0'), int(os.getenv('PORT', '8787')),
            int(os.getenv('POLL_SECONDS', '300')), int(os.getenv('FULL_SCAN_HOURS', '24')),
            int(os.getenv('MAX_ATTACHMENT_MB', '100')))
        if not re.fullmatch(r'v\d+\.\d+', settings.api_version):
            raise ValueError('META_API_VERSION must look like v27.0')
        if not 1 <= settings.port <= 65535 or settings.poll_seconds < 10:
            raise ValueError('Invalid PORT or POLL_SECONDS (minimum 10)')
        if settings.full_scan_hours < 1 or settings.max_attachment_mb < 1:
            raise ValueError('Scan hours and attachment limit must be positive')
        return settings

    def require_meta(self):
        if not all((self.page_id, self.page_token, self.app_secret, self.verify_token)):
            raise ValueError('Fill META_PAGE_ID, META_PAGE_ACCESS_TOKEN, META_APP_SECRET and WEBHOOK_VERIFY_TOKEN in .env first. See README.md.')
