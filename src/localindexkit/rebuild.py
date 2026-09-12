"""Incremental rebuild keyed on content hash.

Full rebuild is `rm index && rebuild`. Incremental rebuild re-derives only
the sources whose content hash changed, then re-applies the corrections
overlay. Corrections are always re-applied in full: they are authoritative
and cheap, and partially-applied corrections are how indexes drift.
"""

import hashlib
import sqlite3
from pathlib import Path

STATE_TABLE = "_lik_source_state"


def content_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Rebuilder:
    """Drives derivation of an index from sources plus corrections.

    derive_fn(source_path) -> iterable of (table, row) fact tuples. Rows must
    carry the provenance contract; they are validated at insert.
    apply_corrections(conn) re-applies the whole corrections overlay; it runs
    on every rebuild, incremental or full.
    setup_fn(conn) creates fact tables; it must be idempotent.
    """

    def __init__(self, index_path, setup_fn, derive_fn, apply_corrections=None):
        self.index_path = Path(index_path)
        self.setup_fn = setup_fn
        self.derive_fn = derive_fn
        self.apply_corrections = apply_corrections
        self.fact_tables = set()

    def _connect(self):
        conn = sqlite3.connect(self.index_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {STATE_TABLE} ("
            "source_path TEXT PRIMARY KEY, content_hash TEXT NOT NULL)"
        )
        self.setup_fn(conn)
        return conn

    def rebuild(self, sources):
        """Incremental rebuild over the given source paths.

        Returns the list of source paths that were (re)derived. Sources
        recorded in the index but absent from the list are purged, so the
        index never outlives its sources.
        """
        from .provenance import insert_fact

        conn = self._connect()
        rederived = []
        try:
            sources = [Path(s) for s in sources]
            known = dict(
                conn.execute(
                    f"SELECT source_path, content_hash FROM {STATE_TABLE}"
                )
            )
            live_keys = set()
            for src in sorted(sources):
                key = str(src)
                live_keys.add(key)
                digest = content_hash(src)
                if known.get(key) == digest:
                    continue
                self._purge_source(conn, key)
                for table, row in self.derive_fn(src):
                    self.fact_tables.add(table)
                    insert_fact(conn, table, row)
                conn.execute(
                    f"INSERT OR REPLACE INTO {STATE_TABLE} VALUES (?, ?)",
                    (key, digest),
                )
                rederived.append(key)
            for gone in set(known) - live_keys:
                self._purge_source(conn, gone)
                conn.execute(
                    f"DELETE FROM {STATE_TABLE} WHERE source_path = ?", (gone,)
                )
            if self.apply_corrections is not None:
                self.apply_corrections(conn)
            conn.commit()
        finally:
            conn.close()
        return rederived

    def _purge_source(self, conn, source_path):
        for table in self._all_fact_tables(conn):
            conn.execute(
                f"DELETE FROM {table} WHERE source_path = ?", (source_path,)
            )

    def _all_fact_tables(self, conn):
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE '_lik_%' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        named = {r[0] for r in rows}
        # Only tables carrying the contract are fact tables; a consumer may
        # keep auxiliary tables (FTS shadow tables etc.) beside them.
        fact = set()
        for t in named:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
            if {"source_path", "extractor", "derived_at"} <= cols:
                fact.add(t)
        return fact
