"""S2: the ingest acceptance tests.

The two that matter most are idempotence and rebuild equivalence. Everything
else here exists so that when one of those two goes red, there is something
more specific to look at than "the index changed".

Note on `derived_at`: the idempotence tests compare whole rows, timestamps
included, because an unchanged re-run must not re-derive anything at all. The
rebuild-equivalence queries compare every column except `derived_at`, because
three builds genuinely happen at three different moments. What must match
across builds is the answers, not the clock reading taken while deriving them.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures
from src import ingest as ingest_mod
from src import probe, schema
from src.ingest import Ingest
from src.kit import ProvenanceError, assert_rebuild_equivalent

# Every column except derived_at, per table, ordered deterministically.
STABLE_QUERIES = (
    "SELECT content_hash, kind, ext, size_bytes, source_path, source_span,"
    " extractor, extractor_version, confidence, tier FROM items"
    " ORDER BY content_hash",
    "SELECT content_hash, path, is_canonical, source_path, source_span,"
    " extractor, extractor_version, confidence, tier FROM item_paths"
    " ORDER BY path",
    "SELECT content_hash, attribute, value_text, value_num, source_path,"
    " source_span, extractor, extractor_version, confidence, tier"
    " FROM media_info ORDER BY content_hash, attribute",
    "SELECT content_hash, taken_at, source_path, source_span, extractor,"
    " extractor_version, confidence, tier FROM taken_at"
    " ORDER BY content_hash",
    "SELECT content_hash, lat, lon, source_path, source_span, extractor,"
    " extractor_version, confidence, tier FROM location ORDER BY content_hash",
    "SELECT content_hash, phash, gray_range, source_path, source_span,"
    " extractor, extractor_version, confidence, tier FROM phash"
    " ORDER BY content_hash",
    "SELECT content_hash, group_key, distance, source_path, source_span,"
    " extractor, extractor_version, confidence, tier FROM variant_groups"
    " ORDER BY group_key, content_hash",
    "SELECT content_hash, thumb_path, width, height, source_path, source_span,"
    " extractor, extractor_version, confidence, tier FROM thumbnails"
    " ORDER BY content_hash",
)


def query(index_path, sql, params=()):
    conn = sqlite3.connect(index_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def full_snapshot(index_path):
    """Every fact row, every column, derived_at included."""
    conn = sqlite3.connect(index_path)
    try:
        snapshot = {}
        for table in schema.FACT_TABLES:
            columns = [
                r[1] for r in conn.execute(f"PRAGMA table_info({table})")
            ]
            order = ", ".join(columns)
            snapshot[table] = conn.execute(
                f"SELECT {order} FROM {table} ORDER BY {order}"
            ).fetchall()
        return snapshot
    finally:
        conn.close()


def stable_snapshot(index_path):
    return [query(index_path, sql) for sql in STABLE_QUERIES]


def tree_state(root):
    """Bytes, size, mtime and mode of every file under root."""
    state = {}
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        info = path.stat()
        state[str(path.relative_to(root))] = (
            sha256(path.read_bytes()).hexdigest(),
            info.st_size,
            info.st_mtime_ns,
            info.st_mode,
        )
    return state


class IngestCase(unittest.TestCase):
    """A private corpus and index per test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="media-vault-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sources = self.root / "sources"
        self.sources.mkdir()
        fixtures.fresh_corpus(self.sources)
        self.index = self.root / ".index" / "vault.db"
        self.thumbs = self.index.parent / "thumbs"

    def ingest(self, **kwargs):
        return Ingest([self.sources], self.index, **kwargs).run()

    def hash_of(self, relative):
        rows = query(
            self.index,
            f"SELECT content_hash FROM {schema.ITEM_PATHS} WHERE path = ?",
            (str(self.sources / relative),),
        )
        self.assertEqual(len(rows), 1, f"no item_paths row for {relative}")
        return rows[0][0]


class IdempotenceTests(IngestCase):
    """Deliverable (a)."""

    def test_second_run_derives_nothing_new(self):
        first = self.ingest()
        self.assertEqual(len(first["rederived"]), first["files_scanned"])
        second = self.ingest()
        self.assertEqual(
            second["rederived"], [],
            "a re-run with no changes re-derived sources",
        )

    def test_second_run_answers_are_byte_identical(self):
        self.ingest()
        before = full_snapshot(self.index)
        self.ingest()
        after = full_snapshot(self.index)
        for table in schema.FACT_TABLES:
            with self.subTest(table=table):
                self.assertEqual(
                    before[table], after[table],
                    f"{table} changed across an unchanged re-run",
                )

    def test_second_run_does_not_regenerate_thumbnails(self):
        self.ingest()
        before = tree_state(self.thumbs)
        self.assertTrue(before, "no thumbnails were written at all")
        self.ingest()
        self.assertEqual(before, tree_state(self.thumbs))

    def test_variant_grouping_does_not_restamp_itself(self):
        # The grouping pass runs on every rebuild by design. It must leave the
        # table alone when the answer has not changed, or an unchanged re-run
        # would quietly rewrite derived_at on every grouped row.
        self.ingest()
        before = query(
            self.index,
            f"SELECT derived_at FROM {schema.VARIANT_GROUPS} "
            f"ORDER BY content_hash",
        )
        self.assertTrue(before, "no variant group rows to check")
        self.ingest()
        after = query(
            self.index,
            f"SELECT derived_at FROM {schema.VARIANT_GROUPS} "
            f"ORDER BY content_hash",
        )
        self.assertEqual(before, after)


class RebuildEquivalenceTests(IngestCase):
    """Deliverable (b), through the kit's own gate."""

    def test_rm_and_rebuild_reproduces_the_same_answers(self):
        def build(index_path):
            Ingest([self.sources], Path(index_path)).run()

        assert_rebuild_equivalent(
            build, list(STABLE_QUERIES), runs=3, workdir=str(self.root)
        )

    def test_incremental_index_matches_a_from_scratch_index(self):
        # The stronger version of the same claim: an index grown incrementally
        # over three runs must equal one built in a single pass.
        self.ingest()
        self.ingest()
        self.ingest()
        incremental = stable_snapshot(self.index)

        scratch = self.root / "scratch" / "vault.db"
        Ingest([self.sources], scratch).run()
        self.assertEqual(incremental, stable_snapshot(scratch))


class SourcesUntouchedTests(IngestCase):
    """Deliverable (c), and the repo's first hard rule."""

    def test_ingest_changes_no_byte_and_no_mtime_in_the_sources(self):
        before = tree_state(self.sources)
        self.assertGreater(len(before), 5)
        self.ingest()
        self.assertEqual(before, tree_state(self.sources))

    def test_ingest_adds_and_removes_no_files_in_the_sources(self):
        before = set(tree_state(self.sources))
        self.ingest()
        self.ingest()
        self.assertEqual(before, set(tree_state(self.sources)))

    def test_everything_written_lands_under_the_index_directory(self):
        outside = set(p for p in self.root.rglob("*") if p.is_file())
        self.ingest()
        new = {p for p in self.root.rglob("*") if p.is_file()} - outside
        self.assertTrue(new, "the ingest wrote nothing at all")
        for path in new:
            self.assertTrue(
                self.index.parent in path.parents or path == self.index,
                f"{path} was written outside .index/",
            )


class ProvenanceSurfacingTests(IngestCase):
    """Deliverable (d): the kit's rejection reaches the caller."""

    def test_a_derived_row_missing_a_provenance_column_stops_the_ingest(self):
        class BrokenIngest(Ingest):
            def _prov(self, *args, **kwargs):
                row = super()._prov(*args, **kwargs)
                row.pop("source_span")
                return row

        with self.assertRaises(ProvenanceError) as caught:
            BrokenIngest([self.sources], self.index).run()
        self.assertIn("source_span", str(caught.exception))

    def test_the_failed_ingest_leaves_no_half_written_facts(self):
        class BrokenIngest(Ingest):
            def _prov(self, *args, **kwargs):
                row = super()._prov(*args, **kwargs)
                row.pop("confidence")
                return row

        with self.assertRaises(ProvenanceError):
            BrokenIngest([self.sources], self.index).run()
        for table in schema.FACT_TABLES:
            rows = query(self.index, f"SELECT COUNT(*) FROM {table}")
            self.assertEqual(rows[0][0], 0, f"{table} kept rows after a reject")

    def test_every_derived_row_carries_the_contract_after_a_real_ingest(self):
        self.ingest()
        for table in schema.FACT_TABLES:
            rows = query(
                self.index,
                f"SELECT COUNT(*) FROM {table} WHERE source_path IS NULL"
                " OR source_span IS NULL OR extractor IS NULL"
                " OR extractor_version IS NULL OR confidence IS NULL"
                " OR derived_at IS NULL OR tier IS NULL",
            )
            self.assertEqual(rows[0][0], 0, table)

    def test_tier_defaults_to_personal_on_every_row(self):
        self.ingest()
        for table in schema.FACT_TABLES:
            tiers = {
                r[0] for r in query(self.index, f"SELECT DISTINCT tier FROM {table}")
            }
            self.assertTrue(tiers <= {"personal"}, f"{table}: {tiers}")


class DerivedFactsTests(IngestCase):
    """One ingest, many read-only assertions."""

    def setUp(self):
        super().setUp()
        self.summary = self.ingest()

    def test_corpus_counts(self):
        # Nine files on disk, README skipped, one exact duplicate collapsed.
        self.assertEqual(self.summary["files_scanned"], 8)
        self.assertEqual(self.summary["items"], 7)
        self.assertEqual(self.summary["paths"], 8)

    def test_readme_at_a_source_root_is_skipped(self):
        rows = query(
            self.index,
            f"SELECT COUNT(*) FROM {schema.ITEM_PATHS} WHERE path LIKE ?",
            ("%README.md",),
        )
        self.assertEqual(rows[0][0], 0)

    def test_exact_duplicates_collapse_to_one_item_with_both_paths(self):
        """Deliverable (e)."""
        digest = self.hash_of("bars_c.jpg")
        self.assertEqual(
            self.hash_of(fixtures.DUPLICATE_OF_BARS), digest,
            "identical bytes hashed differently",
        )
        items = query(
            self.index,
            f"SELECT COUNT(*) FROM {schema.ITEMS} WHERE content_hash = ?",
            (digest,),
        )
        self.assertEqual(items[0][0], 1, "duplicate produced a second item")

        paths = query(
            self.index,
            f"SELECT path, is_canonical FROM {schema.ITEM_PATHS}"
            f" WHERE content_hash = ? ORDER BY path",
            (digest,),
        )
        self.assertEqual(
            [Path(p).name for p, _ in paths],
            ["bars_c.jpg", "bars_c_again.jpg"],
        )
        self.assertEqual(
            [flag for _, flag in paths], [1, 0],
            "exactly one path must be canonical",
        )

    def test_duplicate_facts_are_derived_once_not_twice(self):
        digest = self.hash_of("bars_c.jpg")
        rows = query(
            self.index,
            f"SELECT COUNT(*) FROM {schema.MEDIA_INFO}"
            f" WHERE content_hash = ? AND attribute = 'width'",
            (digest,),
        )
        self.assertEqual(rows[0][0], 1)

    def test_resized_copy_groups_with_its_original(self):
        """Deliverable (f)."""
        original, resized = (self.hash_of(n) for n in fixtures.VARIANT_PAIR)
        self.assertNotEqual(original, resized, "fixtures are not distinct")

        groups = dict(
            query(
                self.index,
                f"SELECT content_hash, group_key FROM {schema.VARIANT_GROUPS}",
            )
        )
        self.assertIn(original, groups, "original was not grouped")
        self.assertIn(resized, groups, "resized copy was not grouped")
        self.assertEqual(
            groups[original], groups[resized],
            "the resized copy landed in a different group",
        )

    def test_unrelated_images_are_not_grouped(self):
        original = self.hash_of(fixtures.VARIANT_PAIR[0])
        groups = dict(
            query(
                self.index,
                f"SELECT content_hash, group_key FROM {schema.VARIANT_GROUPS}",
            )
        )
        members = [h for h, k in groups.items() if k == groups[original]]
        self.assertEqual(
            len(members), 2,
            "the variant group swept in an unrelated image",
        )
        for name in ("test_b.jpg", "bars_c.jpg", "exif_d.jpg"):
            self.assertNotIn(self.hash_of(name), members, name)

    def test_variant_rows_record_the_distance_they_grouped_on(self):
        rows = query(
            self.index,
            f"SELECT distance, source_span, extractor FROM"
            f" {schema.VARIANT_GROUPS}",
        )
        self.assertTrue(rows)
        for distance, span, extractor in rows:
            self.assertLessEqual(distance, probe.VARIANT_THRESHOLD)
            self.assertEqual(span, f"phash:hamming<={probe.VARIANT_THRESHOLD}")
            self.assertEqual(extractor, "mv.variant")

    def test_photo_dimensions_recorded_with_the_field_they_came_from(self):
        digest = self.hash_of("grad_a.jpg")
        rows = dict(
            (a, (v, s))
            for a, v, s in query(
                self.index,
                f"SELECT attribute, value_num, source_span FROM"
                f" {schema.MEDIA_INFO} WHERE content_hash = ?",
                (digest,),
            )
        )
        self.assertEqual(rows["width"][0], 640.0)
        self.assertEqual(rows["height"][0], 480.0)
        self.assertEqual(rows["width"][1], "identify:%w")

    def test_exif_datetime_and_gps_land_with_full_provenance(self):
        digest = self.hash_of("exif_d.jpg")
        taken = query(
            self.index,
            f"SELECT taken_at, source_span, extractor, confidence FROM"
            f" {schema.TAKEN_AT} WHERE content_hash = ?",
            (digest,),
        )
        self.assertEqual(len(taken), 1)
        self.assertEqual(taken[0][0], fixtures.EXIF_DATETIME_ISO)
        self.assertEqual(taken[0][1], "EXIF:DateTimeOriginal")
        self.assertEqual(taken[0][2], "mv.exif")
        self.assertLess(taken[0][3], 1.0, "a camera clock is not certainty")

        located = query(
            self.index,
            f"SELECT lat, lon, source_span FROM {schema.LOCATION}"
            f" WHERE content_hash = ?",
            (digest,),
        )
        self.assertEqual(len(located), 1)
        self.assertAlmostEqual(located[0][0], fixtures.EXIF_LAT, places=4)
        self.assertAlmostEqual(located[0][1], fixtures.EXIF_LON, places=4)
        self.assertIn("GPS", located[0][2])

    def test_photos_without_exif_get_no_invented_date_or_place(self):
        digest = self.hash_of("grad_a.jpg")
        for table in (schema.TAKEN_AT, schema.LOCATION):
            rows = query(
                self.index,
                f"SELECT COUNT(*) FROM {table} WHERE content_hash = ?",
                (digest,),
            )
            self.assertEqual(rows[0][0], 0, table)

    def test_video_duration_and_dimensions_from_ffprobe(self):
        digest = self.hash_of(fixtures.VIDEO)
        kind = query(
            self.index,
            f"SELECT kind FROM {schema.ITEMS} WHERE content_hash = ?",
            (digest,),
        )
        self.assertEqual(kind[0][0], "video")
        facts = dict(
            (a, (v, s))
            for a, v, s in query(
                self.index,
                f"SELECT attribute, value_num, source_span FROM"
                f" {schema.MEDIA_INFO} WHERE content_hash = ?",
                (digest,),
            )
        )
        self.assertAlmostEqual(facts["duration_s"][0], 1.0, delta=0.2)
        self.assertEqual(facts["width"][0], 320.0)
        self.assertEqual(facts["height"][0], 240.0)
        self.assertIn("ffprobe:", facts["duration_s"][1])

    def test_video_gets_a_poster_frame(self):
        digest = self.hash_of(fixtures.VIDEO)
        rows = query(
            self.index,
            f"SELECT thumb_path, source_span FROM {schema.THUMBNAILS}"
            f" WHERE content_hash = ?",
            (digest,),
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("poster", rows[0][1])
        self.assertTrue((self.thumbs / f"{digest}.jpg").is_file())

    def test_every_photo_and_video_has_a_thumbnail_inside_the_box(self):
        rows = query(
            self.index,
            f"SELECT i.content_hash, t.width, t.height FROM {schema.ITEMS} i"
            f" LEFT JOIN {schema.THUMBNAILS} t"
            f" ON t.content_hash = i.content_hash"
            f" WHERE i.kind IN ('photo', 'video')",
        )
        self.assertEqual(len(rows), 6)
        for digest, width, height in rows:
            self.assertIsNotNone(width, f"no thumbnail row for {digest}")
            self.assertLessEqual(max(width, height), probe.THUMB_MAX_PX)
            self.assertTrue((self.thumbs / f"{digest}.jpg").is_file())

    def test_thumbnail_paths_are_stored_relative_to_the_index(self):
        rows = query(
            self.index, f"SELECT thumb_path FROM {schema.THUMBNAILS}"
        )
        for (thumb_path,) in rows:
            self.assertFalse(os.path.isabs(thumb_path), thumb_path)
            self.assertTrue(
                (self.index.parent / thumb_path).is_file(), thumb_path
            )

    def test_a_non_media_file_is_an_item_with_no_deep_facts(self):
        digest = self.hash_of(fixtures.OTHER_FILE)
        kind = query(
            self.index,
            f"SELECT kind, ext FROM {schema.ITEMS} WHERE content_hash = ?",
            (digest,),
        )
        self.assertEqual(kind[0][0], "other")
        self.assertEqual(kind[0][1], ".txt")
        for table in (schema.MEDIA_INFO, schema.PHASH, schema.THUMBNAILS,
                      schema.TAKEN_AT, schema.LOCATION):
            rows = query(
                self.index,
                f"SELECT COUNT(*) FROM {table} WHERE content_hash = ?",
                (digest,),
            )
            self.assertEqual(rows[0][0], 0, table)

    def test_videos_are_not_perceptually_hashed(self):
        # Variant grouping is about resized copies of a still image. Hashing
        # one frame of a video would group unrelated clips by their opening
        # frame, which is worse than not grouping them at all.
        digest = self.hash_of(fixtures.VIDEO)
        rows = query(
            self.index,
            f"SELECT COUNT(*) FROM {schema.PHASH} WHERE content_hash = ?",
            (digest,),
        )
        self.assertEqual(rows[0][0], 0)

    def test_no_warnings_on_a_clean_corpus(self):
        self.assertEqual(self.summary["warnings"], [])


class ChangingSourcesTests(IngestCase):
    def test_a_removed_source_loses_its_item_and_its_thumbnail(self):
        self.ingest()
        digest = self.hash_of("test_b.jpg")
        self.assertTrue((self.thumbs / f"{digest}.jpg").is_file())

        (self.sources / "test_b.jpg").unlink()
        summary = self.ingest()

        self.assertEqual(summary["items"], 6)
        rows = query(
            self.index,
            f"SELECT COUNT(*) FROM {schema.ITEMS} WHERE content_hash = ?",
            (digest,),
        )
        self.assertEqual(rows[0][0], 0)
        self.assertFalse((self.thumbs / f"{digest}.jpg").exists())
        self.assertEqual(summary["thumbnails_swept"], 1)

    def test_deleting_the_canonical_copy_promotes_the_survivor(self):
        # The duplicate group's bytes are unchanged, so nothing about the
        # survivor's content hash tells the rebuilder to look again. Without
        # group invalidation the item would vanish with the deleted file.
        self.ingest()
        digest = self.hash_of("bars_c.jpg")
        (self.sources / "bars_c.jpg").unlink()
        summary = self.ingest()

        self.assertEqual(summary["items"], 7, "the surviving copy lost its item")
        paths = query(
            self.index,
            f"SELECT path, is_canonical FROM {schema.ITEM_PATHS}"
            f" WHERE content_hash = ?",
            (digest,),
        )
        self.assertEqual(len(paths), 1)
        self.assertEqual(Path(paths[0][0]).name, "bars_c_again.jpg")
        self.assertEqual(paths[0][1], 1, "the survivor was not promoted")

    def test_a_new_duplicate_path_is_recorded_without_re_deriving_facts(self):
        self.ingest()
        digest = self.hash_of("bars_c.jpg")
        shutil.copy2(
            self.sources / "bars_c.jpg", self.sources / "copies" / "third.jpg"
        )
        self.ingest()

        paths = query(
            self.index,
            f"SELECT COUNT(*) FROM {schema.ITEM_PATHS} WHERE content_hash = ?",
            (digest,),
        )
        self.assertEqual(paths[0][0], 3)
        items = query(
            self.index,
            f"SELECT COUNT(*) FROM {schema.ITEMS} WHERE content_hash = ?",
            (digest,),
        )
        self.assertEqual(items[0][0], 1)

    def test_an_edited_file_is_re_derived(self):
        self.ingest()
        before = self.hash_of("test_b.jpg")
        # New content, not a copy of another fixture: copying one fixture over
        # another would make the two an exact-duplicate pair and correctly
        # re-derive both, which is a different behaviour than the one under
        # test here.
        fixtures.replace_with_new_image(self.sources / "test_b.jpg")
        summary = self.ingest()
        self.assertEqual(
            summary["rederived"], [str(self.sources / "test_b.jpg")],
            "an edit should re-derive exactly the edited file",
        )
        rows = query(
            self.index,
            f"SELECT COUNT(*) FROM {schema.ITEMS} WHERE content_hash = ?",
            (before,),
        )
        self.assertEqual(rows[0][0], 0, "the old item survived an edit")


class ConfigTests(unittest.TestCase):
    """The scaffold acceptance: ingest finds the sources from config alone."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="media-vault-config-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_the_example_config_is_the_template_and_the_real_one_stays_private(self):
        example = ingest_mod.REPO_ROOT / "vault.config.example.json"
        config = ingest_mod.load_config(example)
        self.assertEqual(config["sources"], [Path("/absolute/path/to/your/photos")])
        self.assertEqual(config["tier"], "personal")
        self.assertEqual(config["index_path"], ingest_mod.DEFAULT_INDEX)
        ignored = (ingest_mod.REPO_ROOT / ".gitignore").read_text().splitlines()
        self.assertIn("vault.config.json", ignored)

    def test_index_defaults_under_a_gitignored_directory(self):
        self.assertEqual(ingest_mod.DEFAULT_INDEX.parent.name, ".index")
        ignored = (ingest_mod.REPO_ROOT / ".gitignore").read_text()
        self.assertIn(".index/", ignored)

    def test_ingest_runs_from_a_config_file_alone(self):
        sources = self.root / "vault"
        sources.mkdir()
        fixtures.fresh_corpus(sources)
        config = self.root / "vault.config.json"
        config.write_text(
            '{"sources": ["%s"], "index": "%s"}'
            % (sources, self.root / "idx" / "vault.db")
        )
        summary = ingest_mod.ingest(config_path=config)
        self.assertEqual(summary["items"], 7)

    def test_home_relative_sources_are_expanded(self):
        config = self.root / "vault.config.json"
        config.write_text('{"sources": ["~/MediaVault"]}')
        parsed = ingest_mod.load_config(config)
        self.assertEqual(parsed["sources"][0], Path.home() / "MediaVault")

    def test_a_config_without_sources_is_an_error(self):
        config = self.root / "vault.config.json"
        config.write_text("{}")
        with self.assertRaises(ingest_mod.ConfigError):
            ingest_mod.load_config(config)

    def test_a_missing_config_says_what_to_write(self):
        with self.assertRaises(ingest_mod.ConfigError) as caught:
            ingest_mod.load_config(self.root / "nope.json")
        self.assertIn("sources", str(caught.exception))


class SeedDirectoryTests(unittest.TestCase):
    """The README is the whole interface for the owner, so it is checked."""

    README = Path.home() / "MediaVault" / "README.md"

    @unittest.skipUnless(README.parent.is_dir(), "the seed directory is an owner-local convention")
    def test_the_seed_directory_and_its_readme_exist(self):
        self.assertTrue(self.README.parent.is_dir())
        self.assertTrue(self.README.is_file())

    def test_the_readme_gives_the_rerun_command_and_the_promise(self):
        text = self.README.read_text()
        self.assertIn("python3 -m src.ingest", text)
        self.assertIn("~/projects/personal/media-vault", text)
        for phrase in ("never", "read"):
            self.assertIn(phrase, text.lower())


if __name__ == "__main__":
    unittest.main()
