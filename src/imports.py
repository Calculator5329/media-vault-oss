"""Resumable, read-only source reconciliation with SHA-256 content identities.

The work cache tracks occurrences, never rewrites originals, and publishes
identities only after a complete stable read. ZIP members are streamed, never
extracted to disk. Repeated runs resume missing hashes; changed sources invalidate
old identities. Only one writer may work on a database at once.
"""
import argparse
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from .portable import lock as flock, on_external_root
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import time
import zipfile

from .exports import MEDIA
from .kit import create_fact_table, insert_fact

VERSION = 'imports-1'


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def database(path, sources):
    path = Path(path).resolve()
    roots = [Path(p).resolve() for p in sources]
    if on_external_root(path) or any(path == p or p in path.parents for p in roots):
        raise ValueError('Derived state must stay on the local drive, outside every source and removable media')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        try:
            flock(lock, blocking=False)
        except BlockingIOError as exc:
            raise RuntimeError('An import worker already owns this database') from exc
        conn = sqlite3.connect(path)
        path.chmod(0o600)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript('''
          CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS occurrences(
            id TEXT PRIMARY KEY, source TEXT NOT NULL, member TEXT NOT NULL,
            offset INTEGER NOT NULL, size INTEGER NOT NULL, source_size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL, crc INTEGER, kind TEXT NOT NULL,
            present INTEGER NOT NULL, content_hash TEXT, hashed_at TEXT,
            metadata TEXT, error TEXT);
          CREATE INDEX IF NOT EXISTS occurrence_hash ON occurrences(content_hash);
        ''')
        create_fact_table(conn, 'identity_facts', {'occurrence_id': 'TEXT NOT NULL', 'content_hash': 'TEXT NOT NULL'})
        configured = json.dumps([str(r) for r in roots])
        recorded = conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()
        if recorded and not set(json.loads(recorded[0])).issubset(str(r) for r in roots):
            conn.close()
            raise ValueError('Database belongs to different sources')
        conn.execute("INSERT OR REPLACE INTO settings VALUES('sources',?)", (configured,))
        conn.commit()
        try:
            yield conn
        finally:
            conn.close()


def _record(source, member, offset, size, source_size, mtime, crc, kind, metadata=None, digest=None, hashed_at=None):
    identity = hashlib.sha256(json.dumps([str(source), member, offset]).encode()).hexdigest()
    return (identity, str(source), member, offset, size, source_size, mtime, crc, kind, metadata, digest, hashed_at)


def inventory(conn, catalog, exports=None, additional_catalogs=()):
    """Atomically adopt a complete source listing; unavailable roots abort it.

    ``exports`` is an optional folder of Google Takeout ZIP files; ``None`` means
    the library has no export archives.
    """
    rows = [];inventoried_roots=[]
    for catalog_path in (catalog,*additional_catalogs):
        with closing(sqlite3.connect(Path(catalog_path).resolve(strict=True).as_uri() + '?mode=ro', uri=True)) as master:
            master.row_factory = sqlite3.Row
            root = Path(master.execute("SELECT value FROM settings WHERE key='source'").fetchone()[0]).resolve(strict=True)
            inventoried_roots.append(str(root))
            if not root.is_dir():
                raise FileNotFoundError('Master source unavailable')
            for item in master.execute('SELECT * FROM files WHERE present=1'):
                path = root / item['path']
                resolved = path.resolve(strict=True)
                info = path.lstat()
                if root not in resolved.parents or not stat.S_ISREG(info.st_mode):
                    raise ValueError('Source is no longer a regular file below its root')
                if (info.st_size, info.st_mtime_ns) != (item['size'], item['mtime_ns']):
                    raise ValueError('Master changed; refresh its catalog before importing')
                rows.append(_record(resolved, '', -1, info.st_size, info.st_size, info.st_mtime_ns, None,
                                    item['kind'], item['metadata'], item['content_hash'], item['hash_derived_at']))
    export_root = Path(exports).resolve(strict=True) if exports is not None else None
    if export_root is not None and not export_root.is_dir():
        raise ValueError('Exports source must be a directory')
    configured=json.loads(conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0])
    if set(configured)!=set([*inventoried_roots,*([str(export_root)] if export_root is not None else [])]):
        raise ValueError('Inventory must include every registered source')
    for path in sorted(export_root.iterdir()) if export_root is not None else ():
        if path.is_symlink() or not path.is_file() or not path.name.startswith(('takeout-', 'Photos-')) or path.suffix.lower() != '.zip':
            continue
        before = path.stat()
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                ext = PurePosixPath(info.filename).suffix.lower()
                if info.is_dir() or ext not in MEDIA:
                    continue
                kind = 'video' if ext in {'.mp4','.mov','.avi','.mkv','.m4v','.3gp','.webm','.mpg','.mpeg'} else 'photo'
                rows.append(_record(path, info.filename, info.header_offset, info.file_size,
                                    before.st_size, before.st_mtime_ns, info.CRC, kind))
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise RuntimeError('ZIP changed during inventory')
    with conn:
        conn.execute('UPDATE occurrences SET present=0')
        for row in rows:
            old = conn.execute('SELECT * FROM occurrences WHERE id=?', (row[0],)).fetchone()
            unchanged = old and (old['size'],old['source_size'],old['mtime_ns'],old['crc']) == (row[4],row[5],row[6],row[7])
            digest, hashed_at = (old['content_hash'], old['hashed_at']) if unchanged else (row[10], row[11])
            # Cached hashes without derivation times cannot publish identities.
            if not hashed_at:
                digest = None
            conn.execute('''INSERT OR REPLACE INTO occurrences VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?,?)''',
                         row[:9] + (digest, hashed_at, row[9], old['error'] if unchanged else None))
        from .media_types import apply as apply_types
        apply_types(conn)
        conn.execute("INSERT OR REPLACE INTO settings VALUES('inventory_at',?)", (now(),))
        # Never leave old publication live after a changed/removed occurrence.
        conn.execute('''DELETE FROM identity_facts WHERE NOT EXISTS (SELECT 1 FROM occurrences
                        WHERE present=1 AND id=occurrence_id AND occurrences.content_hash=identity_facts.content_hash)''')
    return len(rows)


def _source_stat(row):
    path = Path(row['source'])
    info = path.lstat()
    if path.resolve(strict=True) != path or not stat.S_ISREG(info.st_mode) or (info.st_size, info.st_mtime_ns) != (row['source_size'], row['mtime_ns']):
        raise ValueError('Source changed since inventory')
    return info


def _digest(stream, size, deadline):
    digest, total = hashlib.sha256(), 0
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('Checkpoint deadline reached')
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > size:
            raise ValueError('Source exceeded inventoried size')
        digest.update(chunk)
    if total != size:
        raise ValueError('Incomplete source read')
    return digest.hexdigest()


def hash_pending(conn, seconds=600, limit=None):
    """Checkpoint each complete read; stop at deadline without partial identities.

    Failed occurrences are retained and skipped on resume until explicitly
    investigated. ZIP CRC errors are extraction failures, never successful hashes.
    """
    deadline = time.monotonic() + seconds
    processed = 0
    rows = conn.execute('''SELECT * FROM occurrences WHERE present=1 AND content_hash IS NULL
                           AND error IS NULL ORDER BY member='',source,offset''').fetchall()
    archive, archive_path, zip_entries = None, None, None
    try:
        for row in rows:
            if time.monotonic() >= deadline or (limit is not None and processed >= limit):
                break
            try:
                before = _source_stat(row)
                if row['member']:
                    if archive_path != row['source']:
                        if archive:
                            archive.close()
                        archive = zipfile.ZipFile(row['source'])
                        archive_path = row['source']
                        zip_entries = {info.header_offset: info for info in archive.infolist()}
                    info = zip_entries[row['offset']]
                    if (info.filename,info.file_size,info.CRC) != (row['member'],row['size'],row['crc']):
                        raise ValueError('ZIP member changed')
                    with archive.open(info) as stream:
                        digest = _digest(stream, row['size'], deadline)
                else:
                    with Path(row['source']).open('rb') as stream:
                        digest = _digest(stream, row['size'], deadline)
                after = _source_stat(row)
                if before.st_ino != after.st_ino:
                    raise ValueError('Source replaced during read')
                with conn:
                    conn.execute('UPDATE occurrences SET content_hash=?,hashed_at=? WHERE id=?', (digest,now(),row['id']))
                processed += 1
                if processed % 100 == 0:
                    print(json.dumps({'hashed_this_run': processed, **summary(conn)}), flush=True)
            except TimeoutError:
                break
            except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
                with conn:
                    conn.execute('UPDATE occurrences SET error=? WHERE id=?', (type(exc).__name__,row['id']))
    finally:
        if archive:
            archive.close()
    from .media_types import apply as apply_types
    with conn:apply_types(conn)
    publish(conn)
    return processed


def publish(conn):
    """Rebuild disposable identity facts, preserving original derivation times."""
    with conn:
        conn.execute('DELETE FROM identity_facts')
        for row in conn.execute('SELECT * FROM occurrences WHERE present=1 AND content_hash IS NOT NULL'):
            insert_fact(conn, 'identity_facts', {
                'occurrence_id': row['id'], 'content_hash': row['content_hash'],
                'source_path': row['source'], 'source_span': json.dumps({'member': row['member'], 'offset': row['offset']}),
                'extractor': 'sha256', 'extractor_version': VERSION, 'confidence': 1.0,
                'derived_at': row['hashed_at'], 'tier': 'personal',
            })


def summary(conn):
    row = conn.execute('''SELECT count(*) occurrences, sum(size) bytes,
        count(content_hash) verified_occurrences, count(DISTINCT content_hash) verified_contents,
        sum(CASE WHEN content_hash IS NOT NULL THEN size ELSE 0 END) verified_bytes,
        sum(CASE WHEN content_hash IS NULL AND error IS NULL THEN 1 ELSE 0 END) pending,
        count(error) errors FROM occurrences WHERE present=1''').fetchone()
    result = dict(row)
    result['verified_duplicate_occurrences'] = result['verified_occurrences'] - result['verified_contents']
    result['verified_cross_source_contents'] = conn.execute('''SELECT count(*) FROM (
        SELECT content_hash FROM occurrences WHERE present=1 AND content_hash IS NOT NULL
        GROUP BY content_hash HAVING max(member='')=1 AND min(member='')=0)''').fetchone()[0]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path('.catalog/catalog.db'))
    parser.add_argument('--exports', type=Path, help='Folder of Google Takeout ZIP files (optional)')
    parser.add_argument('--database', type=Path, default=Path('.catalog/imports.db'))
    parser.add_argument('--seconds', type=int, default=600)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--resume', action='store_true', help='Skip inventory; hash only the retained source snapshot')
    args = parser.parse_args()
    if args.seconds < 1 or (args.limit is not None and args.limit < 1):
        parser.error('Work bounds must be positive')
    if args.database.resolve() == args.catalog.resolve():
        parser.error('Import database must be separate from the metadata catalog')
    with closing(sqlite3.connect(args.catalog.resolve(strict=True).as_uri() + '?mode=ro', uri=True)) as master:
        source = master.execute("SELECT value FROM settings WHERE key='source'").fetchone()[0]
    with database(args.database, [source, *([args.exports] if args.exports else [])]) as conn:
        if not args.resume:
            inventory(conn, args.catalog, args.exports)
        print(json.dumps({'phase': 'start', **summary(conn)}), flush=True)
        hash_pending(conn, args.seconds, args.limit)
        print(json.dumps({'phase': 'checkpoint', **summary(conn)}), flush=True)


if __name__ == '__main__':
    main()
