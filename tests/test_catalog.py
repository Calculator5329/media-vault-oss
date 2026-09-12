"""Fresh archive behavior, exercised without personal media."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from src import catalog
from tests.scratch import scratch, base as scratch_base


class FreshCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=scratch_base())
        self.root = Path(self.tmp.name)
        self.source = self.root / 'originals'
        self.source.mkdir()
        self.source, self.conn = catalog.connect(self.source, self.root / 'catalog.db')

    def tearDown(self):
        self.conn.close()
        # Retain test artifacts under the workspace retention convention.
        self.tmp._finalizer.detach()

    def test_restarts_skip_unchanged_and_rederive_changed_without_legacy_import(self):
        image = self.source / 'photo.jpg'
        image.write_bytes(b'first')
        catalog.inventory(self.source, self.conn)
        with patch.object(catalog, 'inspect', return_value={'errors': [], 'date': None, 'location': None}) as inspect:
            catalog.enrich(self.source, self.conn)
            catalog.inventory(self.source, self.conn)
            catalog.enrich(self.source, self.conn)
            self.assertEqual(inspect.call_count, 1)
            image.write_bytes(b'changed bytes')
            catalog.inventory(self.source, self.conn)
            catalog.enrich(self.source, self.conn)
            self.assertEqual(inspect.call_count, 2)
        tables = {r[0] for r in self.conn.execute("select name from sqlite_master where type='table'")}
        self.assertNotIn('person', tables)
        self.assertEqual(image.read_bytes(), b'changed bytes')

    def test_duplicate_content_is_verified_not_inferred_from_size(self):
        for name, content in [('a.jpg', b'ABC'), ('b.jpg', b'ABC'), ('c.jpg', b'XYZ'), ('d.jpg', b'unique-size')]:
            (self.source / name).write_bytes(content)
        catalog.inventory(self.source, self.conn)
        self.assertEqual(catalog.hash_candidates(self.source, self.conn), 3)
        report = catalog.summary(self.conn)
        self.assertEqual(report['exact_duplicate_groups'], 1)
        self.assertEqual(report['exact_duplicate_extra_files'], 1)
        self.assertEqual(report['exact_duplicate_extra_bytes'], 3)
        self.assertEqual(report['hashed_files'], 3)

    def test_published_facts_have_provenance_and_rebuild_to_same_answers(self):
        from src.kit import validate_row
        (self.source / 'a.jpg').write_bytes(b'ABC')
        (self.source / 'b.jpg').write_bytes(b'ABC')
        catalog.inventory(self.source, self.conn)
        catalog.hash_candidates(self.source, self.conn)
        catalog.publish_facts(self.source, self.conn)
        before = [dict(row) for row in self.conn.execute('select * from catalog_facts')]
        for row in before:
            validate_row('catalog_facts', row)
            self.assertEqual(row['tier'], 'personal')
        self.assertEqual(len(before), 4)
        catalog.publish_facts(self.source, self.conn)
        after = [dict(row) for row in self.conn.execute('select * from catalog_facts')]
        self.assertEqual(before, after)

    def test_failed_walk_preserves_previous_inventory(self):
        (self.source / 'photo.jpg').write_bytes(b'image')
        catalog.inventory(self.source, self.conn)
        with patch.object(catalog.os, 'walk', side_effect=PermissionError('offline')):
            with self.assertRaises(PermissionError):
                catalog.inventory(self.source, self.conn)
        self.assertEqual(catalog.summary(self.conn)['files'], 1)

    def test_inventory_retains_missing_rows_and_excludes_symlinks(self):
        image = self.source / 'photo.jpg'
        image.write_bytes(b'image')
        (self.source / 'link.jpg').symlink_to(image)
        (self.source / '.hidden').write_bytes(b'not media')
        catalog.inventory(self.source, self.conn)
        self.assertEqual(catalog.summary(self.conn)['files'], 1)
        image.rename(self.root / 'archived.jpg')
        catalog.inventory(self.source, self.conn)
        self.assertEqual(catalog.summary(self.conn)['files'], 0)
        self.assertEqual(self.conn.execute('select count(*) from files').fetchone()[0], 1)

    def test_refuses_source_outputs_and_reusing_another_archive(self):
        with self.assertRaises(ValueError):
            catalog.connect(self.source, self.source / 'catalog.db')
        other = self.root / 'other'
        other.mkdir()
        with self.assertRaises(ValueError):
            catalog.connect(other, self.root / 'catalog.db')

    def test_changed_during_probe_is_not_published(self):
        image = self.source / 'photo.jpg'
        image.write_bytes(b'image')
        catalog.inventory(self.source, self.conn)
        def inspect(path):
            path.write_bytes(b'changed while probing')
            return {'date': {'value': '2020-01-01'}}
        with patch.object(catalog, 'inspect', side_effect=inspect):
            catalog.enrich(self.source, self.conn)
        row = self.conn.execute('select * from files').fetchone()
        self.assertIsNone(row['metadata'])
        self.assertEqual(row['error'], 'source_changed')


class MetadataTests(unittest.TestCase):
    def test_offsets_survive_and_impossible_dates_are_rejected(self):
        self.assertEqual(catalog.timestamp('2024:02:29 12:30:40', '-05:00', True), '2024-02-29T12:30:40-05:00')
        self.assertEqual(catalog.timestamp('2024-02-29T12:30:40.123Z'), '2024-02-29T12:30:40.123000+00:00')
        self.assertIsNone(catalog.timestamp('2023-02-29T12:30:40Z'))
        self.assertIsNone(catalog.timestamp('2024-02-29T25:30:40Z'))
        self.assertEqual(catalog.timestamp('2024:02:29 12:30:40', '+99:99', True), '2024-02-29T12:30:40')

    def test_video_location_and_missing_hemispheres(self):
        data = {'format': {'tags': {'location': '+41.5000-087.6000+020.0/'}}}
        self.assertEqual(catalog.video_location(data)['lon'], -87.6)
        data['format']['tags']['location'] = '+99.00-087.00/'
        self.assertIsNone(catalog.video_location(data))
        self.assertIsNone(catalog.gps({'GPSLatitude': '41,0,0', 'GPSLongitude': '87,0,0'}))

    def test_photo_empty_metadata_differs_from_probe_failure(self):
        with patch.object(catalog.probe, '_run', return_value='100\n200\n'):
            result = catalog.inspect(Path('test.jpg'))
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['exif'], {})
        with patch.object(catalog.probe, '_run', side_effect=catalog.probe.ToolError('failed')):
            failed = catalog.inspect(Path('test.jpg'))
        self.assertEqual(failed['errors'][0]['stage'], 'photo-metadata')

    def test_modification_date_is_not_called_capture_date(self):
        with patch.object(catalog.probe, '_run', return_value='100\n200\nexif:DateTime=2024:01:02 03:04:05\n'):
            result = catalog.inspect(Path('test.jpg'))
        self.assertEqual(result['date']['meaning'], 'modification')
        self.assertFalse(result['date']['timezone_known'])


if __name__ == '__main__':
    unittest.main()
