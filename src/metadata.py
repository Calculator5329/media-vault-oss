"""Attach source metadata to verified content, preserving competing evidence.

The import snapshot is read-only. Facts live in a separate private database;
sidecars never establish content identity and old people labels are omitted.
"""
import argparse
from contextlib import closing
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import os
import re
from pathlib import Path, PurePosixPath
import sqlite3
import zipfile

from .exports import family, MAX_JSON
from .imports import database, now
from .kit import create_fact_table, insert_fact

VERSION = 'metadata-1'
SUPPLEMENT = '.supplemental-metadata'  # Takeout's sidecar suffix, often truncated to fit a name limit


def sidecar_values(data):
    """Return source claims, not inferred truths; timestamp meanings stay distinct."""
    yield 'raw_sidecar', {k: v for k, v in data.items() if k != 'people'}
    for key, meaning in [('photoTakenTime', 'capture'), ('creationTime', 'Google Photos creation')]:
        raw = data.get(key)
        value = raw.get('timestamp') if isinstance(raw, dict) else None
        try:
            if not isinstance(value, str) or not value.lstrip('-').isdigit():
                continue
            parsed = datetime.fromtimestamp(int(value), timezone.utc).isoformat()
            yield 'date', {'value': parsed, 'meaning': meaning, 'field': key, 'timezone_known': True}
        except (ValueError, OverflowError, OSError):
            pass
    for key in ('geoData', 'geoDataExif'):
        raw = data.get(key)
        if not isinstance(raw, dict):
            continue
        lat, lon = raw.get('latitude'), raw.get('longitude')
        if (type(lat) in (int,float) and type(lon) in (int,float)
                and math.isfinite(lat) and math.isfinite(lon)
                and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat,lon) != (0,0)):
            yield 'location', {'lat':lat, 'lon':lon, 'field':key}
    if isinstance(data.get('description'), str) and data['description']:
        yield 'description', data['description']


def sidecar_names(json_name, title):
    """The media file names a loose Google Photos sidecar could describe.

Takeout writes ``IMG_1.jpg.json`` or ``IMG_1.jpg.supplemental-metadata.json``, truncates
long names (``...jpg.supplemental-metad.json``), and moves a duplicate's ``(1)`` after the
extension (``IMG_1.jpg(1).json`` for ``IMG_1(1).jpg``). The ``title`` field holds the
original name, so it is a candidate too, with the same ``(n)`` treatment."""
    names=set()
    base=json_name[:-5] if json_name.lower().endswith('.json') else json_name
    copy=re.fullmatch(r'(.*)(\(\d+\))',base)
    if copy:base,tag=copy.groups()
    else:tag=''
    for cut in [i for i,ch in enumerate(base) if ch=='.'][::-1]:
        if SUPPLEMENT.startswith(base[cut:]):
            base=base[:cut];break
    candidates=[base]
    if isinstance(title,str) and title and '/' not in title and '\\' not in title and title not in ('.','..'):candidates.append(title)
    for name in candidates:
        if not name:continue
        if tag:
            stem,dot,ext=name.rpartition('.')
            names.add(f'{stem}{tag}.{ext}' if dot else name+tag)
        else:names.add(name)
    return names


def refresh(import_database, export_root, output):
    """``export_root`` may be ``None`` when the library has no Takeout archives."""
    import_database = Path(import_database).resolve(strict=True)
    if Path(output).resolve() == import_database:
        raise ValueError('Metadata store must be separate from import state')
    with closing(sqlite3.connect(import_database.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute('SELECT * FROM occurrences WHERE present=1')]
        source_roots = json.loads(conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0])
    export_root = Path(export_root).resolve(strict=True) if export_root is not None else None
    if export_root is not None and str(export_root) not in source_roots:
        raise ValueError('Exports must match the inventoried source')
    by_name, archives = defaultdict(list), {}
    for row in rows:
        if row['member']:
            by_name[(family(Path(row['source']).name)[0], row['member'])].append(row)
            archives[row['source']] = (row['source_size'],row['mtime_ns'])
    for source, fingerprint in archives.items():
        path = Path(source)
        info = path.stat()
        if path.resolve(strict=True) != path or (info.st_size,info.st_mtime_ns) != fingerprint:
            raise ValueError('Archive changed since content inventory')
    counts, facts = Counter(), []
    derived = now()

    def add(row, source, span, extractor, when, values, version=VERSION):
        for attribute, value in values:
            facts.append({'content_hash': row['content_hash'], 'attribute':attribute,
                          'value_json': json.dumps(value, sort_keys=True, allow_nan=False),
                          'source_path':source, 'source_span':json.dumps(span, sort_keys=True),
                          'extractor':extractor, 'extractor_version':version, 'confidence':1.0,
                          'derived_at':when, 'tier':'personal'})

    for row in rows:
        if row['member'] or not row['content_hash'] or not row['metadata']:
            continue
        raw = json.loads(row['metadata'])
        values = [('raw_embedded', raw)]
        values += [(key, raw[key]) for key in ('date','location') if raw.get(key)]
        add(row,row['source'],{'kind':'embedded'},raw.get('extractor','catalog'),raw.get('derived_at',derived),values)
        counts['embedded_occurrences_attached'] += 1
        if any(e.get('stage')=='photo-metadata' for e in raw.get('errors',[])):
            from .avif_metadata import inspect
            path=Path(row['source'])
            before=path.stat()
            if path.resolve(strict=True)!=path or (before.st_size,before.st_mtime_ns)!=(row['source_size'],row['mtime_ns']):
                raise ValueError('Source changed before metadata recovery')
            try:
                recovered=inspect(path)
            except (ImportError,OSError,ValueError,TypeError,OverflowError,ZeroDivisionError):
                counts['embedded_recovery_unavailable'] += 1
                continue
            after=path.stat()
            if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
                raise ValueError('Source changed during metadata recovery')
            values=[('raw_embedded',recovered)]
            values += [(key,recovered[key]) for key in ('date','location') if recovered.get(key)]
            add(row,row['source'],{'kind':'embedded','recovery_of':raw.get('extractor','catalog'),
                'basis':'detected_avif'},recovered['extractor'],derived,values,recovered['extractor_version'])
            counts['embedded_occurrences_recovered'] += 1
    # Loose sidecars: an extracted Takeout, or any folder where IMG_1.jpg.json sits beside IMG_1.jpg.
    by_folder=defaultdict(dict)
    for row in rows:
        if not row['member']:by_folder[str(Path(row['source']).parent)][Path(row['source']).name]=row
    for folder,named in sorted(by_folder.items()):
        try:entries=sorted((e for e in os.scandir(folder) if e.name.lower().endswith('.json') and e.is_file(follow_symlinks=False)),key=lambda e:e.name)
        except OSError:continue
        for entry in entries:
            try:
                if entry.stat().st_size>MAX_JSON:
                    counts['sidecar_oversized'] += 1
                    continue
                data=json.loads(Path(entry.path).read_bytes())
                if not isinstance(data,dict) or 'photoTakenTime' not in data:continue
                matches={name:named[name] for name in sidecar_names(entry.name,data.get('title')) if name in named}
                if len(matches)!=1:
                    counts['sidecar_ambiguous' if matches else 'sidecar_unmatched'] += 1
                    continue
                row=next(iter(matches.values()))
                if not row['content_hash']:
                    counts['sidecar_waiting_for_hash'] += 1
                    continue
                values=list(sidecar_values(data))
                json.dumps(values,allow_nan=False)
                add(row,entry.path,{'sidecar':entry.name},'google-photos-sidecar',derived,values)
                counts['sidecar_attached'] += 1
            except (ValueError,UnicodeError,OSError):
                counts['sidecar_unreadable'] += 1
    # Include JSON-only parts too; their sidecars can point to media in other parts.
    for path in sorted(export_root.iterdir()) if export_root is not None else ():
        if path.is_symlink() or not path.is_file() or not path.name.startswith(('takeout-', 'Photos-')) or path.suffix.lower() != '.zip':
            continue
        before = path.stat()
        if str(path) in archives and (before.st_size,before.st_mtime_ns) != archives[str(path)]:
            raise ValueError('Archive changed since content inventory')
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                member = PurePosixPath(info.filename)
                if member.suffix.lower() != '.json' or 'Google Photos' not in member.parts:
                    continue
                if info.file_size > MAX_JSON:
                    counts['sidecar_oversized'] += 1
                    continue
                try:
                    with archive.open(info) as stream:
                        raw = stream.read(MAX_JSON + 1)
                    if len(raw) > MAX_JSON:
                        raise ValueError('Oversized JSON')
                    data = json.loads(raw)
                    if not isinstance(data, dict) or 'photoTakenTime' not in data:
                        continue
                    names = set()
                    title = data.get('title')
                    if isinstance(title,str) and title and '/' not in title and '\\' not in title and title not in ('.','..'):
                        names.add(str(member.parent / title))
                    for suffix in ('.supplemental-metadata.json','.json'):
                        if member.name.endswith(suffix):
                            names.add(str(member)[:-len(suffix)])
                            break
                    candidates = {row['id']:row for name in names for row in by_name.get((family(path.name)[0],name),[])}
                    if len(candidates) != 1:
                        counts['sidecar_ambiguous' if candidates else 'sidecar_unmatched'] += 1
                        continue
                    row = next(iter(candidates.values()))
                    if not row['content_hash']:
                        counts['sidecar_waiting_for_hash'] += 1
                        continue
                    values = list(sidecar_values(data))
                    # Strict JSON serializability is checked before adding any row.
                    json.dumps(values, allow_nan=False)
                    add(row,str(path),{'member':info.filename,'offset':info.header_offset},'google-photos-sidecar',derived,values)
                    counts['sidecar_attached'] += 1
                except (ValueError,UnicodeError,RuntimeError,zipfile.BadZipFile,NotImplementedError):
                    counts['sidecar_unreadable'] += 1
        after = path.stat()
        if (before.st_size,before.st_mtime_ns,before.st_ino) != (after.st_size,after.st_mtime_ns,after.st_ino):
            raise RuntimeError('Archive changed during metadata read')
    with database(output, source_roots) as conn:
        create_fact_table(conn, 'metadata_facts', {'content_hash':'TEXT NOT NULL','attribute':'TEXT NOT NULL','value_json':'TEXT NOT NULL'})
        conn.execute('CREATE INDEX IF NOT EXISTS metadata_by_content ON metadata_facts(content_hash,attribute)')
        with conn:
            conn.execute('DELETE FROM metadata_facts')
            for fact in facts:
                insert_fact(conn,'metadata_facts',fact)
            conn.execute("INSERT OR REPLACE INTO settings VALUES('metadata_at',?)",(derived,))
        counts['facts'] = len(facts)
        counts['contents_with_metadata'] = conn.execute('SELECT count(DISTINCT content_hash) FROM metadata_facts').fetchone()[0]
        counts['contents_with_competing_capture_dates'] = conn.execute('''SELECT count(*) FROM (
          SELECT content_hash FROM metadata_facts WHERE attribute='date' AND json_extract(value_json,'$.meaning')='capture'
          GROUP BY content_hash HAVING count(DISTINCT json_extract(value_json,'$.value'))>1)''').fetchone()[0]
    return {'derived_at':derived, 'extractor':VERSION, **dict(counts)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--imports',type=Path,default=Path('.catalog/imports.db'))
    parser.add_argument('--exports',type=Path,help='Folder of Google Takeout ZIP files (optional)')
    parser.add_argument('--database',type=Path,default=Path('.catalog/metadata.db'))
    args = parser.parse_args()
    print(json.dumps(refresh(args.imports,args.exports,args.database)),flush=True)


if __name__ == '__main__':
    main()
