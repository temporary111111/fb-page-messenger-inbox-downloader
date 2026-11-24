"""Source and PyInstaller entry point. Internal workers use the same executable."""
import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Inbox Archive desktop app')
    parser.add_argument('--app-dir', type=Path)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--no-autostart', action='store_true', help='Open the window without starting collection')
    args = parser.parse_args()
    default = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    root = (args.app_dir or default).resolve()
    if args.worker:
        from inbox_archive.desktop_worker import run_worker
        return run_worker(root)
    from inbox_archive.desktop import run_desktop
    run_desktop(root, autostart=not args.no_autostart)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
