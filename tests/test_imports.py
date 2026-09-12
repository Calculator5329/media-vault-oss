"""Source reconciliation and resumability using synthetic bytes only."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
import zipfile

from src import catalog, imports
from src.kit import validate_row


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.source = self.root / 'originals'
        self.source.mkdir()
        self.exports = self.root / 'exports'
        self.exports.mkdir()
        self.catalog = self.root / 'catalog.db'
        self.db = self.root / 'imports.db'
        (self.source / 'a.jpg').write_bytes(b'same image bytes')
        self.refresh()
        self.zip = self.exports / 'takeout-test-001.zip'
        with zipfile.ZipFile(self.zip, 'w') as z:
            z.writestr('Google Photos/renamed.jpg', b'same image bytes')
            z.writestr('../../never-extract.jpg', b'new image bytes')

    def refresh(self):
        source, conn = catalog.connect(self.source, self.catalog)
        catalog.inventory(source, conn)
        conn.close()

    def connect(self):
        return imports.database(self.db, [self.source, self.exports])

    def test_resume_cross_source_identity_and_provenance(self):
        with self.connect() as conn:
            self.assertEqual(imports.inventory(conn, self.catalog, self.exports), 3)
            self.assertEqual(imports.hash_pending(conn, limit=1), 1)
            self.assertEqual(imports.summary(conn)['pending'], 2)
        with self.connect() as conn:
            imports.inventory(conn, self.catalog, self.exports)
            self.assertEqual(imports.hash_pending(conn), 2)
            result = imports.summary(conn)
            self.assertEqual(result['verified_contents'], 2)
            self.assertEqual(result['verified_cross_source_contents'], 1)
            self.assertEqual(result['verified_duplicate_occurrences'], 1)
            self.assertEqual(imports.hash_pending(conn), 0)
            rows = [dict(r) for r in conn.execute('SELECT * FROM identity_facts')]
            for row in rows:
                validate_row('identity_facts', row)
            imports.publish(conn)
            self.assertEqual(rows, [dict(r) for r in conn.execute('SELECT * FROM identity_facts')])
        self.assertFalse((self.root / 'never-extract.jpg').exists())

    def test_changed_archive_invalidates_hashes_and_facts(self):
        with self.connect() as conn:
            imports.inventory(conn, self.catalog, self.exports)
            imports.hash_pending(conn)
            archived = self.root / 'retained.zip'
            self.zip.rename(archived)
            with zipfile.ZipFile(self.zip, 'w') as z:
                z.writestr('Google Photos/renamed.jpg', b'changed content')
            imports.inventory(conn, self.catalog, self.exports)
            self.assertEqual(imports.summary(conn)['pending'], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM identity_facts').fetchone()[0], 1)
            imports.hash_pending(conn)
            self.assertEqual(imports.summary(conn)['verified_cross_source_contents'], 0)

    def test_unavailable_or_changed_master_does_not_replace_inventory(self):
        with self.connect() as conn:
            imports.inventory(conn, self.catalog, self.exports)
            (self.source / 'a.jpg').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                imports.inventory(conn, self.catalog, self.exports)
            self.assertEqual(imports.summary(conn)['occurrences'], 3)
            self.source.rename(self.root / 'offline')
            with self.assertRaises(FileNotFoundError):
                imports.inventory(conn, self.catalog, self.exports)
            self.assertEqual(imports.summary(conn)['occurrences'], 3)

    def test_changed_file_during_resume_never_gets_an_identity(self):
        with self.connect() as conn:
            imports.inventory(conn, self.catalog, self.exports)
            (self.source / 'a.jpg').write_bytes(b'changed')
            imports.hash_pending(conn)
            self.assertEqual(imports.summary(conn)['errors'], 1)
            self.assertEqual(imports.summary(conn)['verified_occurrences'], 2)

    def test_partial_reads_and_deadlines_never_produce_a_hash(self):
        with self.assertRaises(ValueError):
            imports._digest(io.BytesIO(b'abc'), 4, None)
        with self.assertRaises(ValueError):
            imports._digest(io.BytesIO(b'abc'), 2, None)
        with self.assertRaises(TimeoutError):
            imports._digest(io.BytesIO(b'abc'), 3, time.monotonic() - 1)

    def test_database_is_single_writer_and_source_outputs_refuse(self):
        with self.connect():
            with self.assertRaises(RuntimeError):
                with self.connect():
                    pass
        with self.assertRaises(ValueError):
            with imports.database(self.source / 'bad.db', [self.source]):
                pass
        with self.assertRaises(ValueError):
            with imports.database(self.db, [self.root / 'other']):
                pass

    def test_duplicate_zip_names_hash_distinct_members(self):
        self.zip.rename(self.root / 'old.zip')
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.zip, 'w') as z:
                z.writestr('duplicate.jpg', b'one')
                z.writestr('duplicate.jpg', b'two')
        with self.connect() as conn:
            imports.inventory(conn, self.catalog, self.exports)
            imports.hash_pending(conn)
            hashes = {r[0] for r in conn.execute("SELECT content_hash FROM occurrences WHERE member='duplicate.jpg'")}
            self.assertEqual(hashes, {hashlib.sha256(v).hexdigest() for v in (b'one', b'two')})


if __name__ == '__main__':
    unittest.main()
