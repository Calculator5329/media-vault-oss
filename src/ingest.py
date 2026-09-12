"""S2: read-only ingest.

Walks the configured sources, hashes every file, and derives what the media
tools can tell us about photos and videos. Nothing here opens a source file
for writing, renames one, or deletes one. The only paths this module writes
are inside `.index/`, which is disposable by design.

The shape of a run:

1. Walk the sources and content-hash every file. Files are grouped by hash,
   so the same bytes at four paths become one item with four recorded paths.
2. Invalidate any duplicate group whose membership changed since last time,
   so a canonical path that vanished cannot leave a stale item behind.
3. Hand the paths to the kit's `Rebuilder`, which re-derives only the ones
   whose content hash moved and purges the ones that are gone.
4. Recompute near-variant grouping across the whole phash table, and only
   write it back if it actually changed. That is what makes an unchanged
   re-run derive nothing at all, timestamps included.
5. Sweep thumbnails whose item no longer exists.

Run it from the repo root:

    python3 -m src.ingest
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import probe, schema
from .kit import (
    STATE_TABLE,
    Rebuilder,
    content_hash,
    insert_fact,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "vault.config.json"
DEFAULT_INDEX = REPO_ROOT / ".index" / "vault.db"

# Bumped when the derivation logic changes in a way that should invalidate
# previously derived facts. Recorded alongside each tool's own version.
EXTRACTOR_VERSION = "1"

CONFIDENCE = {
    # Read straight off the file by a tool whose whole job is to read it.
    "dimensions": 1.0,
    "duration": 1.0,
    "codec": 1.0,
    # The camera wrote this, but camera clocks are wrong all the time.
    "exif_datetime": 0.95,
    "exif_gps": 0.9,
    # A container creation_time is often the copy date, not the capture date.
    "container_datetime": 0.8,
    "phash": 1.0,
    # A near-flat image hashes to noise. The row is kept, the claim is not.
    "phash_flat": 0.2,
    "walk": 1.0,
    "thumbnail": 1.0,
}


class ConfigError(RuntimeError):
    pass


def load_config(config_path=None):
    """Read vault.config.json. Sources are the only required key."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG
    if not path.exists():
        raise ConfigError(
            f"no config at {path}. Run `python3 -m src.doctor --init --source /path/to/photos` "
            'or copy vault.config.example.json; it needs {"sources": ["..."]}'
        )
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    sources = raw.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ConfigError(f"{path} must set a non-empty 'sources' list")
    resolved = []
    for entry in sources:
        if not isinstance(entry, str):
            raise ConfigError(f"{path}: every source must be a string")
        expanded = Path(os.path.expandvars(os.path.expanduser(entry)))
        if not expanded.is_absolute():
            expanded = (path.parent / expanded).resolve()
        resolved.append(expanded)
    tier = raw.get("tier", "personal")
    index = raw.get("index")
    index_path = (
        Path(os.path.expanduser(index)) if index else DEFAULT_INDEX
    )
    if not index_path.is_absolute():
        index_path = (path.parent / index_path).resolve()
    return {"sources": resolved, "tier": tier, "index_path": index_path}


def walk_sources(sources):
    """Every indexable file under the sources, sorted, read-only.

    Skips dot-files and dot-directories (thumbnail caches, version control,
    trash) and the README this repo places at each source root, which is
    instructions for the owner rather than something they want in the catalogue.
    """
    found = []
    for root in sources:
        root = Path(root)
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            here = Path(dirpath)
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                if here == root and name.lower() == "readme.md":
                    continue
                path = here / name
                if path.is_file() and not path.is_symlink():
                    found.append(path)
    return sorted(set(found))


class Ingest:
    """One ingest run against one index."""

    def __init__(self, sources, index_path, tier="personal", verbose=False):
        self.sources = [Path(s) for s in sources]
        self.index_path = Path(index_path)
        self.thumbs_dir = self.index_path.parent / "thumbs"
        self.tier = tier
        self.verbose = verbose
        # One timestamp for the whole run: rows derived together should agree
        # about when that was.
        self.derived_at = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self.warnings = []
        self.hash_by_path = {}
        self.groups = {}

    # ------------------------------------------------------------ helpers

    def _warn(self, message):
        self.warnings.append(message)
        if self.verbose:
            print(f"warning: {message}", file=sys.stderr)

    def _prov(self, source_path, span, extractor, confidence, tool=None):
        version = EXTRACTOR_VERSION
        if tool:
            version = f"{EXTRACTOR_VERSION}+{tool}{probe.tool_version(tool)}"
        return {
            "source_path": str(source_path),
            "source_span": span,
            "extractor": extractor,
            "extractor_version": version,
            "confidence": confidence,
            "derived_at": self.derived_at,
            "tier": self.tier,
        }

    def _scan(self):
        """Hash every file and group identical bytes together."""
        self.hash_by_path = {}
        self.groups = {}
        for path in walk_sources(self.sources):
            try:
                digest = content_hash(path)
            except OSError as exc:
                self._warn(f"unreadable, skipped: {path} ({exc})")
                continue
            self.hash_by_path[path] = digest
            self.groups.setdefault(digest, []).append(path)
        for digest in self.groups:
            # Canonical path is the lexicographically first, so the choice
            # does not depend on walk order or filesystem ordering.
            self.groups[digest].sort()

    # -------------------------------------------------------- derivation

    def _derive(self, path):
        path = Path(path)
        digest = self.hash_by_path.get(path)
        if digest is None:
            # Hashed by the rebuilder but not by our scan: a file that
            # appeared mid-run. It will be picked up next time.
            return
        group = self.groups[digest]
        canonical = group[0]

        yield schema.ITEM_PATHS, {
            "content_hash": digest,
            "path": str(path),
            "is_canonical": 1 if path == canonical else 0,
            **self._prov(
                path, "file:bytes", "mv.walk", CONFIDENCE["walk"]
            ),
        }
        if path != canonical:
            # An exact duplicate contributes its path and nothing else. The
            # facts belong to the bytes, and the bytes already have an item.
            return

        kind = probe.kind_for(path)
        yield schema.ITEMS, {
            "content_hash": digest,
            "kind": kind,
            "ext": path.suffix.lower(),
            "size_bytes": path.stat().st_size,
            **self._prov(
                path, "file:bytes", "mv.walk", CONFIDENCE["walk"]
            ),
        }
        if kind == "photo":
            yield from self._derive_photo(path, digest)
        elif kind == "video":
            yield from self._derive_video(path, digest)
        # kind "other" gets an item row and no deep facts, by scope.

    def _derive_photo(self, path, digest):
        dimensions = None
        try:
            dimensions = probe.photo_dimensions(path)
        except probe.ToolError as exc:
            self._warn(f"no dimensions for {path}: {exc}")
        if dimensions:
            for attribute, value, span in (
                ("width", dimensions[0], "identify:%w"),
                ("height", dimensions[1], "identify:%h"),
            ):
                yield schema.MEDIA_INFO, {
                    "content_hash": digest,
                    "attribute": attribute,
                    "value_text": None,
                    "value_num": float(value),
                    **self._prov(
                        path, span, "mv.identify",
                        CONFIDENCE["dimensions"], tool="identify",
                    ),
                }

        props = probe.exif_properties(path)
        taken = probe.parse_exif_datetime(props.get("DateTimeOriginal"))
        span = "EXIF:DateTimeOriginal"
        if not taken:
            taken = probe.parse_exif_datetime(props.get("DateTimeDigitized"))
            span = "EXIF:DateTimeDigitized"
        if taken:
            yield schema.TAKEN_AT, {
                "content_hash": digest,
                "taken_at": taken,
                **self._prov(
                    path, span, "mv.exif",
                    CONFIDENCE["exif_datetime"], tool="identify",
                ),
            }
        gps = probe.parse_gps(props)
        if gps:
            yield schema.LOCATION, {
                "content_hash": digest,
                "lat": gps[0],
                "lon": gps[1],
                **self._prov(
                    path, "EXIF:GPSLatitude+GPSLongitude", "mv.exif",
                    CONFIDENCE["exif_gps"], tool="identify",
                ),
            }

        try:
            samples = probe.gray_samples(path)
        except probe.ToolError as exc:
            self._warn(f"no perceptual hash for {path}: {exc}")
        else:
            digest_hex, gray_range = probe.average_hash(samples)
            flat = gray_range < probe.FLAT_RANGE
            yield schema.PHASH, {
                "content_hash": digest,
                "phash": digest_hex,
                "gray_range": gray_range,
                **self._prov(
                    path, probe.GRAY_SAMPLE_CMD_SPAN, "mv.phash",
                    CONFIDENCE["phash_flat"] if flat else CONFIDENCE["phash"],
                    tool="ffmpeg",
                ),
            }

        if dimensions:
            yield from self._derive_thumbnail(
                path, digest, dimensions, "image:frame0"
            )

    def _derive_video(self, path, digest):
        try:
            document = probe.ffprobe_json(path)
        except probe.ToolError as exc:
            self._warn(f"ffprobe failed on {path}: {exc}")
            return
        summary = probe.video_summary(document)
        for attribute in sorted(summary):
            value, span = summary[attribute]
            confidence = (
                CONFIDENCE["duration"] if attribute == "duration_s"
                else CONFIDENCE["codec"] if attribute == "video_codec"
                else CONFIDENCE["dimensions"]
            )
            yield schema.MEDIA_INFO, {
                "content_hash": digest,
                "attribute": attribute,
                "value_text": value if isinstance(value, str) else None,
                "value_num": None if isinstance(value, str) else float(value),
                **self._prov(
                    path, span, "mv.ffprobe", confidence, tool="ffprobe"
                ),
            }
        created = probe.video_creation_time(document)
        if created:
            yield schema.TAKEN_AT, {
                "content_hash": digest,
                "taken_at": created[0],
                **self._prov(
                    path, created[1], "mv.ffprobe",
                    CONFIDENCE["container_datetime"], tool="ffprobe",
                ),
            }
        width = summary.get("width")
        height = summary.get("height")
        if width and height:
            yield from self._derive_thumbnail(
                path, digest,
                (int(width[0]), int(height[0])),
                "video:poster frame0",
            )

    def _derive_thumbnail(self, path, digest, dimensions, span):
        size = probe.thumb_size(*dimensions)
        if not size:
            return
        thumb = self.thumbs_dir / f"{digest}.jpg"
        if not thumb.exists():
            try:
                probe.write_thumbnail(path, thumb, size)
            except probe.ToolError as exc:
                self._warn(f"no thumbnail for {path}: {exc}")
                return
        yield schema.THUMBNAILS, {
            "content_hash": digest,
            # Relative to the index directory on purpose: an absolute path
            # would make two builds of the same sources disagree, and the
            # thumbnail lives with the index it belongs to.
            "thumb_path": f"thumbs/{digest}.jpg",
            "width": size[0],
            "height": size[1],
            **self._prov(
                path, span, "mv.thumbnail",
                CONFIDENCE["thumbnail"], tool="ffmpeg",
            ),
        }

    # ----------------------------------------------------------- overlays

    def _apply_overlays(self, conn):
        """Runs on every rebuild, incremental or full.

        Near-variant grouping is a whole-table question (which images resemble
        which), so it cannot be derived one source at a time. The corrections
        store lands here too when L2 arrives.
        """
        self._group_near_variants(conn)

    def _group_near_variants(self, conn):
        rows = conn.execute(
            f"SELECT content_hash, phash FROM {schema.PHASH} "
            f"WHERE gray_range >= ? ORDER BY content_hash",
            (probe.FLAT_RANGE,),
        ).fetchall()
        canonical = dict(
            conn.execute(
                f"SELECT content_hash, path FROM {schema.ITEM_PATHS} "
                f"WHERE is_canonical = 1"
            )
        )

        parent = {h: h for h, _ in rows}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, (hash_a, phash_a) in enumerate(rows):
            for hash_b, phash_b in rows[i + 1:]:
                if probe.hamming(phash_a, phash_b) <= probe.VARIANT_THRESHOLD:
                    root_a, root_b = find(hash_a), find(hash_b)
                    if root_a != root_b:
                        # Union toward the smaller hash so the group key does
                        # not depend on iteration order.
                        low, high = sorted((root_a, root_b))
                        parent[high] = low

        phash_by_hash = dict(rows)
        members = {}
        for item_hash, _ in rows:
            members.setdefault(find(item_hash), []).append(item_hash)

        desired = []
        for group_key in sorted(members):
            group = sorted(members[group_key])
            if len(group) < 2:
                continue
            for item_hash in group:
                distance = probe.hamming(
                    phash_by_hash[item_hash], phash_by_hash[group_key]
                )
                source_path = canonical.get(item_hash)
                if source_path is None:
                    continue
                desired.append(
                    {
                        "content_hash": item_hash,
                        "group_key": group_key,
                        "distance": distance,
                        **self._prov(
                            source_path,
                            f"phash:hamming<={probe.VARIANT_THRESHOLD}",
                            "mv.variant",
                            round(1.0 - distance / 64.0, 4),
                        ),
                    }
                )

        # Only rewrite when the answer actually changed. Blindly rebuilding
        # this table would stamp a fresh derived_at on every run and make an
        # unchanged re-run look like work happened.
        compare = ("content_hash", "group_key", "distance", "source_path",
                   "source_span", "extractor", "extractor_version",
                   "confidence", "tier")
        existing = conn.execute(
            f"SELECT {', '.join(compare)} FROM {schema.VARIANT_GROUPS} "
            f"ORDER BY group_key, content_hash"
        ).fetchall()
        wanted = [tuple(row[c] for c in compare) for row in desired]
        if existing == wanted:
            return
        conn.execute(f"DELETE FROM {schema.VARIANT_GROUPS}")
        for row in desired:
            insert_fact(conn, schema.VARIANT_GROUPS, row)

    # ------------------------------------------------- group invalidation

    def _invalidate_changed_groups(self, conn):
        """Force full re-derivation of any duplicate group that changed.

        The rebuilder decides per path, by content hash. That is exactly right
        for a file's own facts and blind to one thing: whether a set of
        identical files gained or lost a member. Deleting the canonical of a
        duplicate pair changes nothing about the survivor's bytes, so without
        this the survivor would never be promoted and the item row would
        vanish with the file that happened to sort first.
        """
        stored = dict(
            conn.execute(
                f"SELECT content_hash, member_paths FROM {schema.GROUP_STATE}"
            )
        )
        current = {
            digest: "\n".join(str(p) for p in paths)
            for digest, paths in self.groups.items()
        }
        affected = set()
        for digest, members in current.items():
            if stored.get(digest) != members:
                affected.update(members.split("\n"))
                if digest in stored:
                    affected.update(stored[digest].split("\n"))
        for digest, members in stored.items():
            if digest not in current:
                affected.update(members.split("\n"))
        if not affected:
            return
        placeholders = ", ".join("?" for _ in affected)
        paths = sorted(affected)
        for table in schema.FACT_TABLES:
            conn.execute(
                f"DELETE FROM {table} WHERE source_path IN ({placeholders})",
                paths,
            )
        conn.execute(
            f"DELETE FROM {STATE_TABLE} WHERE source_path IN ({placeholders})",
            paths,
        )

    def _record_group_state(self, conn):
        conn.execute(f"DELETE FROM {schema.GROUP_STATE}")
        for digest, paths in sorted(self.groups.items()):
            conn.execute(
                f"INSERT INTO {schema.GROUP_STATE} VALUES (?, ?)",
                (digest, "\n".join(str(p) for p in paths)),
            )

    def _sweep_thumbnails(self, conn):
        """Drop thumbnails whose item is gone. Only ever inside .index/."""
        if not self.thumbs_dir.is_dir():
            return 0
        live = {
            row[0]
            for row in conn.execute(
                f"SELECT content_hash FROM {schema.ITEMS}"
            )
        }
        removed = 0
        for thumb in sorted(self.thumbs_dir.glob("*.jpg")):
            if thumb.stem not in live:
                thumb.unlink()
                removed += 1
        return removed

    # ---------------------------------------------------------------- run

    def run(self):
        import sqlite3

        self._scan()
        self.index_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.index_path)
        try:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {STATE_TABLE} ("
                "source_path TEXT PRIMARY KEY, content_hash TEXT NOT NULL)"
            )
            schema.setup(conn)
            self._invalidate_changed_groups(conn)
            conn.commit()
        finally:
            conn.close()

        rebuilder = Rebuilder(
            self.index_path,
            setup_fn=schema.setup,
            derive_fn=self._derive,
            apply_corrections=self._apply_overlays,
        )
        rederived = rebuilder.rebuild(list(self.hash_by_path))

        conn = sqlite3.connect(self.index_path)
        try:
            self._record_group_state(conn)
            swept = self._sweep_thumbnails(conn)
            conn.commit()
            items = conn.execute(
                f"SELECT COUNT(*) FROM {schema.ITEMS}"
            ).fetchone()[0]
            paths = conn.execute(
                f"SELECT COUNT(*) FROM {schema.ITEM_PATHS}"
            ).fetchone()[0]
            grouped = conn.execute(
                f"SELECT COUNT(DISTINCT group_key) FROM {schema.VARIANT_GROUPS}"
            ).fetchone()[0]
        finally:
            conn.close()

        return {
            "files_scanned": len(self.hash_by_path),
            "items": items,
            "paths": paths,
            "rederived": rederived,
            "variant_groups": grouped,
            "thumbnails_swept": swept,
            "warnings": list(self.warnings),
        }


def ingest(sources=None, index_path=None, tier=None, config_path=None,
           verbose=False):
    """Run one ingest, resolving anything not given from the config file."""
    if sources is None or index_path is None or tier is None:
        config = load_config(config_path)
        sources = config["sources"] if sources is None else sources
        index_path = (
            config["index_path"] if index_path is None else index_path
        )
        tier = config["tier"] if tier is None else tier
    return Ingest(sources, index_path, tier=tier, verbose=verbose).run()


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python3 -m src.ingest",
        description="Index the configured media sources. Reads only.",
    )
    parser.add_argument("--config", default=None, help="path to vault.config.json")
    parser.add_argument("--index", default=None, help="path to the index database")
    parser.add_argument(
        "--source", action="append", default=None,
        help="source directory, repeatable; overrides the config",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sources = (
        [Path(os.path.expanduser(s)) for s in args.source]
        if args.source else config["sources"]
    )
    index_path = Path(args.index) if args.index else config["index_path"]

    missing = [s for s in sources if not Path(s).is_dir()]
    for source in missing:
        print(f"warning: source directory does not exist: {source}",
              file=sys.stderr)

    summary = Ingest(
        sources, index_path, tier=config["tier"], verbose=not args.quiet
    ).run()

    if not args.quiet:
        print(
            f"scanned {summary['files_scanned']} files, "
            f"{summary['items']} items at {summary['paths']} paths, "
            f"{len(summary['rederived'])} re-derived, "
            f"{summary['variant_groups']} near-variant groups"
        )
        if summary["thumbnails_swept"]:
            print(f"swept {summary['thumbnails_swept']} orphaned thumbnails")
        if summary["warnings"]:
            print(f"{len(summary['warnings'])} warnings (above)")
        print(f"index: {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
