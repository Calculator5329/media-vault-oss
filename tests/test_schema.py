"""S1: the schema carries the provenance contract, and enforces it.

The repo's hard rule is that every derived row carries the seven columns and
a row missing any of them is rejected rather than defaulted. The kit does the
enforcing; these tests prove it is actually wired up here, on this schema,
rather than merely available.
"""

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import schema
from src.kit import PROVENANCE_COLUMNS, ProvenanceError, insert_fact


def good_item_row(**over):
    row = {
        "content_hash": "a" * 64,
        "kind": "photo",
        "ext": ".jpg",
        "size_bytes": 1234,
        "source_path": "/vault/a.jpg",
        "source_span": "file:bytes",
        "extractor": "mv.walk",
        "extractor_version": "1",
        "confidence": 1.0,
        "derived_at": "2026-08-25T00:00:00Z",
        "tier": "personal",
    }
    row.update(over)
    return row


class SchemaShapeTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        schema.setup(self.conn)

    def tearDown(self):
        self.conn.close()

    def _columns(self, table):
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def test_every_fact_table_carries_all_seven_provenance_columns(self):
        for table in schema.FACT_TABLES:
            with self.subTest(table=table):
                self.assertTrue(
                    set(PROVENANCE_COLUMNS) <= self._columns(table),
                    f"{table} is missing provenance columns",
                )

    def test_items_is_itself_a_fact_table(self):
        # An item row is a claim derived by walking, and it carries its
        # origin like every other claim.
        self.assertIn(schema.ITEMS, schema.FACT_TABLES)
        self.assertTrue(set(PROVENANCE_COLUMNS) <= self._columns(schema.ITEMS))

    def test_items_is_keyed_on_content_hash(self):
        self.conn.execute(
            f"INSERT INTO {schema.ITEMS} (content_hash, kind, ext, size_bytes,"
            " source_path, source_span, extractor, extractor_version,"
            " confidence, derived_at, tier)"
            " VALUES ('h', 'photo', '.jpg', 1, 'p', 's', 'e', '1', 1.0, 'd',"
            " 'personal')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                f"INSERT INTO {schema.ITEMS} (content_hash, kind, ext,"
                " size_bytes, source_path, source_span, extractor,"
                " extractor_version, confidence, derived_at, tier)"
                " VALUES ('h', 'video', '.mp4', 2, 'q', 's', 'e', '1', 1.0,"
                " 'd', 'personal')"
            )

    def test_items_kind_is_type_open(self):
        # Nothing constrains kind to photo/video: an "other" file gets a row
        # and no deep facts, rather than being silently dropped.
        for kind in ("photo", "video", "other"):
            insert_fact(
                self.conn, schema.ITEMS,
                good_item_row(content_hash=kind, kind=kind),
            )
        kinds = {
            r[0] for r in self.conn.execute(
                f"SELECT kind FROM {schema.ITEMS}"
            )
        }
        self.assertEqual(kinds, {"photo", "video", "other"})

    def test_setup_is_idempotent(self):
        schema.setup(self.conn)
        schema.setup(self.conn)
        self.assertTrue(set(PROVENANCE_COLUMNS) <= self._columns(schema.ITEMS))

    def test_group_state_is_not_mistaken_for_a_fact_table(self):
        # The rebuilder identifies fact tables by these columns and purges
        # them by source_path. Bookkeeping must stay out of that set.
        columns = self._columns(schema.GROUP_STATE)
        self.assertNotIn("source_path", columns)
        self.assertNotIn("extractor", columns)
        self.assertNotIn(schema.GROUP_STATE, schema.FACT_TABLES)


class ProvenanceEnforcementTests(unittest.TestCase):
    """Deliverable (d), at the schema level."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        schema.setup(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_each_provenance_column_is_individually_required(self):
        for column in PROVENANCE_COLUMNS:
            row = good_item_row()
            del row[column]
            with self.subTest(column=column):
                with self.assertRaises(ProvenanceError) as caught:
                    insert_fact(self.conn, schema.ITEMS, row)
                self.assertIn(column, str(caught.exception))

    def test_a_null_provenance_value_is_rejected_not_defaulted(self):
        with self.assertRaises(ProvenanceError):
            insert_fact(
                self.conn, schema.ITEMS, good_item_row(extractor=None)
            )

    def test_rejected_row_leaves_no_trace(self):
        row = good_item_row()
        del row["source_span"]
        with self.assertRaises(ProvenanceError):
            insert_fact(self.conn, schema.ITEMS, row)
        count = self.conn.execute(
            f"SELECT COUNT(*) FROM {schema.ITEMS}"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_bad_tier_and_confidence_rejected(self):
        with self.assertRaises(ProvenanceError):
            insert_fact(self.conn, schema.ITEMS, good_item_row(tier="public"))
        with self.assertRaises(ProvenanceError):
            insert_fact(self.conn, schema.ITEMS, good_item_row(confidence=2.0))


if __name__ == "__main__":
    unittest.main()
