"""Publish a new portable folder without replacing an existing installation."""
import argparse
from datetime import datetime, timezone
from pathlib import Path


def publish_bundle(source, dist):
    source, dist = Path(source).resolve(), Path(dist).resolve()
    if not (source / 'InboxArchive.exe').is_file():
        raise ValueError('The staged desktop executable is missing.')
    if any((source / name).exists() for name in ('.env', 'data', 'exports')):
        raise ValueError('Refusing to publish a bundle containing private runtime data.')
    dist.mkdir(parents=True, exist_ok=True)
    release = dist / ('InboxArchive-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    source.rename(release)
    return release


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--dist', required=True)
    args = parser.parse_args()
    print(publish_bundle(args.source, args.dist))
