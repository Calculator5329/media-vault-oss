"""Fresh, resumable archive inventory. Never imports the legacy sorter.

SQLite contains private paths and metadata. Keep it on Linux, outside Git.
Inventory IDs identify a source/path, not a face or a content identity.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat

from . import probe
from .kit import create_fact_table, insert_fact
from .portable import on_external_root

VERSION = 'catalog-1'
PHOTO_EXTS = probe.PHOTO_EXTS | {'.avif', '.jpg_large', '.png_dip_staged'}


def kind(path):
    if path.suffix.lower() in PHOTO_EXTS:
        return 'photo'
    return probe.kind_for(path)


def timestamp(raw, offset=None, exif=False):
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if exif:
        if not re.fullmatch(r'\d{4}:\d{2}:\d{2}[ T]\d{2}:\d{2}:\d{2}', value):
            return None
        value = value[:10].replace(':', '-') + 'T' + value[11:]
        if isinstance(offset, str) and re.fullmatch(r'[+-]\d{2}:\d{2}', offset):
            hours, minutes = map(int, offset[1:].split(':'))
            if hours <= 14 and minutes < 60 and (hours < 14 or minutes == 0):
                value += offset
    elif not re.fullmatch(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?', value):
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).isoformat()
    except ValueError:
        return None


def gps(props):
    if props.get('GPSLatitudeRef', '').upper() not in ('N', 'S') or props.get('GPSLongitudeRef', '').upper() not in ('E', 'W'):
        return None
    try:
        point = probe.parse_gps(props)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    return point if point and all(math.isfinite(v) for v in point) else None


def video_location(data):
    scopes = [('format', (data.get('format') or {}).get('tags') or {})]
    scopes += [(f'streams[{i}]', stream.get('tags') or {}) for i, stream in enumerate(data.get('streams') or [])]
    for scope, tags in scopes:
        for name in ('com.apple.quicktime.location.ISO6709', 'location', 'location-eng'):
            value = tags.get(name)
            if not isinstance(value, str):
                continue
            match = re.fullmatch(r'([+-]\d{2}(?:\.\d+)?)([+-]\d{3}(?:\.\d+)?)(?:[+-]\d+(?:\.\d+)?)?/?', value)
            if match:
                lat, lon = map(float, match.groups())
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return {'lat': lat, 'lon': lon, 'source': f'ffprobe:{scope}.tags.{name}'}
    return None


def inspect(path):
    """Return raw metadata plus selected facts and explicit extraction errors."""
    result = {'extractor': VERSION, 'derived_at': datetime.now(timezone.utc).isoformat(), 'errors': [], 'date': None, 'location': None}
    if kind(path) == 'photo':
        try:
            # One metadata-only invocation. Failure is distinct from missing EXIF.
            raw = probe._run(probe.identify_command() + ['-ping', '-format', '%w\n%h\n%[EXIF:*]', f'{path}[0]'])
            lines = raw.splitlines()
            result['width'], result['height'] = int(lines[0]), int(lines[1])
            props = {}
            for line in lines[2:]:
                if line.startswith('exif:') and '=' in line:
                    key, value = line[5:].split('=', 1)
                    props[key.strip()] = value.strip()
            result['exif'] = props
            for key, offset in [('DateTimeOriginal', 'OffsetTimeOriginal'), ('DateTimeDigitized', 'OffsetTimeDigitized'), ('DateTime', 'OffsetTime')]:
                parsed = timestamp(props.get(key), props.get(offset), exif=True)
                if parsed:
                    result['date'] = {'value': parsed, 'source': f'exif:{key}', 'meaning': 'capture' if key != 'DateTime' else 'modification', 'timezone_known': datetime.fromisoformat(parsed).tzinfo is not None}
                    break
            point = gps(props)
            if point:
                result['location'] = {'lat': point[0], 'lon': point[1], 'source': 'exif:GPSLatitude/GPSLongitude'}
        except (probe.ToolError, ValueError, IndexError) as exc:
            result['errors'].append({'stage': 'photo-metadata', 'type': type(exc).__name__})
    elif kind(path) == 'video':
        try:
            data = probe.ffprobe_json(path)
            result['container'] = data
            result['video'] = probe.video_summary(data)
            scopes = [('format', (data.get('format') or {}).get('tags') or {})]
            scopes += [(f'streams[{i}]', s.get('tags') or {}) for i, s in enumerate(data.get('streams') or [])]
            for scope, tags in scopes:
                parsed = timestamp(tags.get('creation_time'))
                if parsed:
                    result['date'] = {'value': parsed, 'source': f'ffprobe:{scope}.tags.creation_time', 'meaning': 'container creation', 'timezone_known': datetime.fromisoformat(parsed).tzinfo is not None}
                    break
            result['location'] = video_location(data)
        except (probe.ToolError, ValueError, TypeError) as exc:
            result['errors'].append({'stage': 'video-metadata', 'type': type(exc).__name__})
    return result


def connect(source, database):
    source = Path(source).expanduser().resolve(strict=True)
    database = Path(database).expanduser().resolve()
    if not source.is_dir():
        raise ValueError('Source must be an available directory')
    if database == source or source in database.parents:
        raise ValueError('Catalog must be outside the source')
    if on_external_root(database):
        raise ValueError('Catalog must stay on the main Linux drive')
    database.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database)
    database.chmod(0o600)
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY, kind TEXT NOT NULL, extension TEXT NOT NULL,
            size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, present INTEGER NOT NULL,
            metadata TEXT, metadata_version TEXT, content_hash TEXT, hash_derived_at TEXT, error TEXT
        );
        CREATE INDEX IF NOT EXISTS by_size ON files(size);
    ''')
    if 'hash_derived_at' not in {r[1] for r in conn.execute('PRAGMA table_info(files)')}:
        conn.execute('ALTER TABLE files ADD COLUMN hash_derived_at TEXT')
        # Earlier cache has no hash derivation time; rehash candidates rather than invent one.
        conn.execute('UPDATE files SET content_hash=NULL')
    create_fact_table(conn, 'catalog_facts', {'path': 'TEXT NOT NULL', 'attribute': 'TEXT NOT NULL', 'value_json': 'TEXT NOT NULL'})
    conn.execute('CREATE INDEX IF NOT EXISTS catalog_facts_attribute ON catalog_facts(attribute)')
    recorded = conn.execute("SELECT value FROM settings WHERE key='source'").fetchone()
    if recorded and recorded[0] != str(source):
        conn.close()
        raise ValueError('This catalog belongs to another source; choose a fresh database')
    conn.execute("INSERT OR IGNORE INTO settings VALUES ('source', ?)", (str(source),))
    conn.commit()
    return source, conn


def inventory(source, conn):
    """Commit only a complete walk; a missing source never empties the catalog."""
    rows = []
    skipped = Counter()
    def walk_error(error):
        raise error
    for base, dirs, files in os.walk(source, followlinks=False, onerror=walk_error):
        kept = []
        for directory in sorted(dirs):
            p = Path(base) / directory
            if directory.startswith('.') or p.is_symlink():
                skipped['hidden_or_symlink_directories'] += 1
            else:
                kept.append(directory)
        dirs[:] = kept
        for name in sorted(files):
            path = Path(base) / name
            info = path.lstat()
            if name.startswith('.') or not stat.S_ISREG(info.st_mode):
                skipped['hidden_or_nonregular_files'] += 1
                continue
            rows.append((str(path.relative_to(source)), kind(path), path.suffix.lower(), info.st_size, info.st_mtime_ns))
    with conn:
        conn.execute('UPDATE files SET present=0')
        for row in rows:
            conn.execute('''INSERT INTO files(path,kind,extension,size,mtime_ns,present) VALUES(?,?,?,?,?,1)
                ON CONFLICT(path) DO UPDATE SET
                kind=excluded.kind,extension=excluded.extension,present=1,
                metadata=CASE WHEN files.size=excluded.size AND files.mtime_ns=excluded.mtime_ns THEN files.metadata ELSE NULL END,
                metadata_version=CASE WHEN files.size=excluded.size AND files.mtime_ns=excluded.mtime_ns THEN files.metadata_version ELSE NULL END,
                content_hash=CASE WHEN files.size=excluded.size AND files.mtime_ns=excluded.mtime_ns THEN files.content_hash ELSE NULL END,
                error=CASE WHEN files.size=excluded.size AND files.mtime_ns=excluded.mtime_ns THEN files.error ELSE NULL END,
                size=excluded.size,mtime_ns=excluded.mtime_ns''', row)
        conn.execute("INSERT OR REPLACE INTO settings VALUES ('inventory_at', ?)", (datetime.now(timezone.utc).isoformat(),))
        conn.execute("INSERT OR REPLACE INTO settings VALUES ('skipped', ?)", (json.dumps(skipped),))
    return len(rows)


def unchanged(path, row):
    try:
        info = path.lstat()
        return stat.S_ISREG(info.st_mode) and (info.st_size, info.st_mtime_ns) == (row['size'], row['mtime_ns'])
    except OSError:
        return False


def enrich(source, conn, limit=None, workers=4):
    if workers not in range(1, 9) or (limit is not None and limit < 0):
        raise ValueError('Use 1–8 workers and a nonnegative limit')
    rows = conn.execute("SELECT * FROM files WHERE present=1 AND kind IN ('photo','video') AND (metadata_version IS NULL OR metadata_version != ?) ORDER BY path LIMIT ?", (VERSION, -1 if limit is None else limit)).fetchall()
    def read(row):
        path = source / row['path']
        if not unchanged(path, row):
            return row, None, 'source_changed'
        try:
            data = inspect(path)
        except (OSError, ValueError, TypeError) as exc:
            return row, None, type(exc).__name__
        return (row, data, None) if unchanged(path, row) else (row, None, 'source_changed')
    processed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(rows), 64):
            for row, data, error in pool.map(read, rows[start:start+64]):
                with conn:
                    conn.execute('UPDATE files SET metadata=?,metadata_version=?,error=? WHERE path=?', (json.dumps(data) if data else None, VERSION if data else None, error, row['path']))
                processed += 1
            print(json.dumps({'metadata_processed_this_run': processed, 'selected': len(rows)}), flush=True)
    return processed


def hash_candidates(source, conn):
    """Hash same-size candidates only. Other files remain explicitly unhashed."""
    rows = conn.execute('''SELECT * FROM files WHERE present=1 AND content_hash IS NULL
        AND size IN (SELECT size FROM files WHERE present=1 GROUP BY size HAVING count(*)>1)
        ORDER BY size,path''').fetchall()
    count = 0
    for row in rows:
        path = source / row['path']
        if not unchanged(path, row):
            continue
        digest = hashlib.sha256()
        try:
            with path.open('rb') as handle:
                for block in iter(lambda: handle.read(1024*1024), b''):
                    digest.update(block)
            if unchanged(path, row):
                with conn:
                    conn.execute('UPDATE files SET content_hash=?,hash_derived_at=? WHERE path=?', (digest.hexdigest(), datetime.now(timezone.utc).isoformat(), row['path']))
                count += 1
        except OSError:
            with conn:
                conn.execute("UPDATE files SET error='hash_read_error' WHERE path=?", (row['path'],))
    return count


def publish_facts(source, conn):
    """Publish provenance-carrying facts from retained private work records.

    Files is the resumable work cache. catalog_facts is the queryable projection.
    Raw extraction output is retained in the cache, not promoted as truth.
    """
    count = 0
    inventory_at = conn.execute("SELECT value FROM settings WHERE key='inventory_at'").fetchone()[0]
    with conn:
        conn.execute('DELETE FROM catalog_facts')
        for row in conn.execute('SELECT * FROM files WHERE present=1').fetchall():
            base = {'path': row['path'], 'source_path': str(source / row['path']),
                    'extractor_version': VERSION, 'tier': 'personal'}
            values = [('file', {'kind': row['kind'], 'size': row['size'], 'extension': row['extension']}, 'filesystem:stat', 'mv.catalog.walk', 1.0, inventory_at)]
            data = json.loads(row['metadata']) if row['metadata'] else {}
            derived_at = data.get('derived_at', inventory_at)
            if data.get('date'):
                date = data['date']
                values.append(('date', date, date['source'], 'mv.catalog.metadata', 0.95 if date['meaning']=='capture' else 0.7, derived_at))
            if data.get('location'):
                location = data['location']
                values.append(('location', location, location['source'], 'mv.catalog.metadata', 0.9, derived_at))
            model = data.get('exif', {}).get('Model')
            if model:
                values.append(('camera_model', model, 'exif:Model', 'mv.catalog.metadata', 0.95, derived_at))
            if row['content_hash']:
                # Hash values are cached only after matching before/after stat checks.
                values.append(('content_hash', row['content_hash'], 'file:bytes:sha256', 'mv.catalog.hash', 1.0, row['hash_derived_at']))
            for attribute, value, span, extractor, confidence, at in values:
                insert_fact(conn, 'catalog_facts', {**base, 'attribute': attribute,
                    'value_json': json.dumps(value), 'source_span': span, 'extractor': extractor,
                    'confidence': confidence, 'derived_at': at})
                count += 1
    return count


def summary(conn):
    rows = conn.execute('SELECT * FROM files WHERE present=1').fetchall()
    coverage = Counter()
    devices = Counter()
    years = Counter()
    types = Counter()
    byte_types = Counter()
    for row in rows:
        types[row['extension']] += 1
        byte_types[row['extension']] += row['size']
        if row['metadata']:
            data = json.loads(row['metadata'])
            coverage['metadata_processed'] += 1
            if data.get('errors'):
                coverage['metadata_with_errors'] += 1
            date = data.get('date')
            if date:
                coverage['date_present'] += 1
                coverage['timezone_known'] += date['timezone_known']
                years[date['value'][:4]] += 1
            if data.get('location'):
                coverage['location_present'] += 1
            model = data.get('exif', {}).get('Model')
            if model:
                devices[model] += 1
        if row['error']:
            coverage['source_or_hash_errors'] += 1
    duplicates = conn.execute('SELECT count(*) AS n,sum(size) AS bytes FROM files WHERE present=1 AND content_hash IS NOT NULL GROUP BY content_hash HAVING count(*)>1').fetchall()
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'files': len(rows), 'bytes': sum(r['size'] for r in rows), 'zero_bytes': sum(r['size']==0 for r in rows), 'extensions': dict(types), 'bytes_by_extension': dict(byte_types), 'coverage': dict(coverage), 'camera_models': dict(devices), 'years': dict(sorted(years.items())), 'exact_duplicate_groups': len(duplicates), 'exact_duplicate_extra_files': sum(r['n']-1 for r in duplicates), 'exact_duplicate_extra_bytes': sum(r['bytes']*(r['n']-1)//r['n'] for r in duplicates), 'hashed_files': sum(r['content_hash'] is not None for r in rows), 'notes': ['Fresh catalog; no legacy labels, clusters or embeddings imported.', 'Only same-size candidates are hashed; unhashed items have no content identity yet.', 'Raw metadata and paths remain in the private local database.', 'Dates retain their source and timezone; container creation and modification dates are not certified capture times.', 'No guesses, person recognition, reverse geocoding, thumbnails or cloud calls performed.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('vault.config.json'))
    parser.add_argument('--database', type=Path, default=Path('.catalog/catalog.db'))
    parser.add_argument('--enrich', action='store_true')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--hash-duplicates', action='store_true')
    parser.add_argument('--summary', type=Path)
    args = parser.parse_args()
    sources = json.loads(args.config.read_text())['sources']
    if len(sources) != 1:
        parser.error('Use exactly one source for each fresh catalog')
    source, conn = connect(sources[0], args.database)
    try:
        if args.summary:
            output = args.summary.expanduser().resolve()
            if output == source or source in output.parents or on_external_root(output):
                parser.error('Summary must stay on the local drive outside the source')
            if output.exists():
                parser.error('Choose a fresh summary path to retain previous evidence')
        print(json.dumps({'inventoried': inventory(source, conn)}), flush=True)
        if args.enrich:
            enrich(source, conn, args.limit, args.workers)
        if args.hash_duplicates:
            print(json.dumps({'hashed_this_run': hash_candidates(source, conn)}), flush=True)
        print(json.dumps({'published_facts': publish_facts(source, conn)}), flush=True)
        report = summary(conn)
        if args.summary:
            args.summary.parent.mkdir(parents=True, exist_ok=True)
            args.summary.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
