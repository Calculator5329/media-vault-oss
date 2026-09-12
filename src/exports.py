"""Read-only ZIP/sidecar audit; aggregate evidence, never an import decision.

Names and sizes establish candidates only. No media payloads are read, no
people labels are retained, and no extracted files are written anywhere.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path, PurePosixPath
import re
import sqlite3
import zipfile

VERSION = 'exports-audit-1'
MAX_JSON = 2_000_000
MEDIA = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.heic', '.heif',
         '.avif', '.tif', '.tiff', '.dng', '.mp4', '.mov', '.avi', '.mkv',
         '.m4v', '.3gp', '.webm', '.mpg', '.mpeg', '.jpg_large', '.png_dip_staged'}


def family(name):
    match = re.fullmatch(r'(takeout-.+)-(\d{3})\.zip', name)
    return (match[1], int(match[2])) if match else (name, None)


def _coverage(data):
    result = Counter()
    value = data.get('photoTakenTime')
    try:
        raw = value.get('timestamp') if isinstance(value, dict) else None
        if not isinstance(raw, str) or not re.fullmatch(r'-?\d+', raw):
            raise ValueError()
        date = datetime.fromtimestamp(int(raw), timezone.utc)
        result['capture_timestamp'] = 1
        result['capture_year_' + str(date.year)] = 1
    except (ValueError, OverflowError, OSError):
        pass
    for field in ('geoData', 'geoDataExif'):
        point = data.get(field)
        if not isinstance(point, dict):
            continue
        lat, lon = point.get('latitude'), point.get('longitude')
        if (type(lat) in (int, float) and type(lon) in (int, float)
                and math.isfinite(lat) and math.isfinite(lon)
                and -90 <= lat <= 90 and -180 <= lon <= 180
                and (lat, lon) != (0, 0)):
            result[field + '_nonzero_location'] = 1
    if data.get('description') and isinstance(data['description'], str):
        result['nonempty_description'] = 1
    return result


def audit(archives, catalog_rows):
    """Return private-content-free counts. Fail if an archive changes mid-read.

    catalog_rows has path/size only. Sidecars associate within one exact
    export family/directory; conflicting names or multiple entries stay
    ambiguous. The report does not establish byte equality or ZIP integrity.
    """
    media, sidecars, fingerprints = [], [], []
    by_name = defaultdict(list)
    counts, shapes, parts = Counter(), Counter(), defaultdict(set)
    master_pairs = Counter((PurePosixPath(r['path']).name, r['size']) for r in catalog_rows)
    master_sizes = {size for _, size in master_pairs}
    for archive in sorted(map(Path, archives)):
        before = archive.stat()
        fam, part = family(archive.name)
        if part is not None:
            parts[fam].add(part)
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                path = PurePosixPath(info.filename)
                if path.suffix.lower() in MEDIA:
                    identity = len(media)
                    media.append((fam, str(path), info.file_size, info.CRC))
                    by_name[(fam, str(path))].append(identity)
                elif path.suffix.lower() == '.json' and 'Google Photos' in path.parts:
                    counts['json_entries'] += 1
                    if info.file_size > MAX_JSON:
                        counts['json_oversized'] += 1
                        continue
                    try:
                        with z.open(info) as stream:
                            raw = stream.read(MAX_JSON + 1)
                        if len(raw) > MAX_JSON:
                            counts['json_oversized'] += 1
                            continue
                        data = json.loads(raw)
                    except (ValueError, UnicodeError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
                        counts['json_unreadable'] += 1
                        continue
                    if not isinstance(data, dict):
                        counts['json_not_object'] += 1
                        continue
                    # Only field names and selected aggregate coverage survive.
                    shapes[tuple(sorted(data))] += 1
                    if 'photoTakenTime' not in data:
                        counts['json_without_photo_taken_time'] += 1
                        continue
                    title = data.get('title')
                    targets = set()
                    if isinstance(title, str) and title and '/' not in title and '\\' not in title and title not in ('.', '..'):
                        targets.add(str(path.parent / title))
                    for suffix in ('.supplemental-metadata.json', '.json'):
                        if path.name.endswith(suffix):
                            targets.add(str(path)[:-len(suffix)])
                            break
                    sidecars.append((fam, targets, _coverage(data)))
        after = archive.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise RuntimeError('Archive changed during audit; discard this run')
        fingerprints.append({'archive': archive.name, 'size': before.st_size, 'mtime_ns': before.st_mtime_ns})
    matched = defaultdict(list)
    all_coverage, linked_coverage = Counter(), Counter()
    for fam, targets, coverage in sidecars:
        all_coverage.update(coverage)
        candidates = {i for name in targets for i in by_name.get((fam, name), [])}
        state = 'unique' if len(candidates) == 1 else 'ambiguous' if candidates else 'unmatched'
        counts['sidecar_' + state] += 1
        if state == 'unique':
            linked_coverage.update(coverage)
            matched[next(iter(candidates))].append(coverage)
    overlap, year_folders, extensions = Counter(), Counter(), Counter()
    signatures = Counter()
    for fam, name, size, crc in media:
        extensions[PurePosixPath(name).suffix.lower()] += 1
        pair = (PurePosixPath(name).name, size)
        state = ('name_size_candidate' if pair in master_pairs else
                 'size_only_candidate' if size in master_sizes else 'no_equal_size_in_catalog')
        overlap[state] += 1
        overlap[state + '_bytes'] += size
        signatures[(size, crc)] += 1
        for component in PurePosixPath(name).parts:
            if re.fullmatch(r'Photos from \d{4}', component):
                year_folders[component[-4:]] += 1
                break
    return {
        'extractor': VERSION, 'derived_at': datetime.now(timezone.utc).isoformat(),
        'method': 'ZIP directories and bounded JSON reads only; media payloads not read. Counts are entries, not unique assets. Name/size and CRC are candidates, not content verification. Sidecar matches do not attach facts to the live catalog.',
        'archives': fingerprints, 'media_entries': len(media),
        'media_bytes': sum(m[2] for m in media), 'json_counts': dict(counts),
        'sidecar_photo_entries': len(sidecars), 'media_with_unique_sidecar_candidate': len(matched),
        'media_with_multiple_sidecars': sum(len(v) > 1 for v in matched.values()),
        'sidecar_coverage_all': dict(sorted(all_coverage.items())),
        'sidecar_coverage_unique_candidates': dict(sorted(linked_coverage.items())),
        'catalog_candidate_overlap': dict(overlap), 'year_folder_entries': dict(sorted(year_folders.items())),
        'media_extensions': dict(sorted(extensions.items())),
        'repeated_size_crc_extra_entries': sum(n - 1 for n in signatures.values()),
        'internal_part_gaps': {k: sorted(set(range(min(v), max(v) + 1)) - v) for k, v in parts.items() if len(v) > 1 and set(range(min(v), max(v) + 1)) - v},
        'json_key_shapes': [{'keys': list(k), 'count': v} for k, v in sorted(shapes.items())],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exports', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, default=Path('.catalog/catalog.db'))
    args = parser.parse_args()
    source = args.exports.resolve(strict=True)
    archives = [p for p in source.iterdir() if p.is_file() and not p.is_symlink()
                and p.name.startswith(('takeout-', 'Photos-')) and p.suffix.lower() == '.zip']
    if not archives:
        parser.error('No matching ZIP exports found')
    with sqlite3.connect(args.catalog.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute('SELECT path, size FROM files WHERE present=1').fetchall()
    print(json.dumps(audit(archives, rows), indent=2))


if __name__ == '__main__':
    main()
