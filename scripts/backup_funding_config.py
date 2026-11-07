"""Create or verify a matched config/SQLite snapshot without restarting workers."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from filelock import FileLock
from core import funding_store, storage


def verify(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    names = ('config.json', 'config.json.funding.sqlite')
    if set(manifest['sha256']) != set(names):
        raise ValueError('Invalid funding backup manifest')
    for name in names:
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != manifest['sha256'][name]:
            raise ValueError('Funding backup checksum mismatch')
    config = json.loads((directory / 'config.json').read_text(encoding='utf-8'))
    uri = (directory / names[1]).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Funding database integrity failure')
        if db.execute('SELECT COUNT(*) FROM pending').fetchone()[0]:
            raise ValueError('Funding backup has a pending projection')
        checkpoint = funding_store.validate_checkpoint(config, db)
        if config.get('funding_reserve_checkpoint') != checkpoint['token']:
            raise ValueError('Funding backup checkpoint missing')
    return manifest


def backup(config_path, directory):
    config_path, directory = Path(config_path).resolve(), Path(directory).resolve()
    with FileLock(str(config_path) + '.lock', timeout=30):
        if not Path(funding_store.database_path(config_path)).is_file():
            raise ValueError('Funding database must exist before a paired backup')
        funding_store.recover_projection(str(config_path))
        config = json.loads(config_path.read_text(encoding='utf-8'))
        with closing(funding_store.connect(config_path)) as source:
            checkpoint = funding_store.validate_checkpoint(config, source)
            directory.mkdir(parents=True, exist_ok=False)
            config['funding_reserve_checkpoint'] = checkpoint['token']
            storage._atomic_save_json(str(directory / 'config.json'), config)
            with closing(sqlite3.connect(directory / 'config.json.funding.sqlite')) as destination:
                source.backup(destination)
        manifest = dict(schema=1, checkpoint=checkpoint['token'], sha256={
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ('config.json', 'config.json.funding.sqlite')})
        storage._atomic_save_json(str(directory / 'manifest.json'), manifest)
    verify(directory)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('backup', 'verify'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--config', type=Path, default=Path('config.json'))
    args = parser.parse_args()
    (backup(args.config, args.directory) if args.operation == 'backup' else verify(args.directory))
    print('Verified funding config/SQLite backup')
