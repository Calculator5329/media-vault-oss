"""ZIP audit acceptance cases; all content is fabricated."""
import json
from pathlib import Path
import tempfile
import unittest
import warnings
import zipfile

from src.exports import audit, MAX_JSON
from tests.scratch import scratch


class ExportAuditTests(unittest.TestCase):
    def setUp(self):
        self.root = scratch()
        self.prefix = 'Takeout/Google Photos/Photos from 2026/'

    def archive(self, number, entries):
        path = self.root / f'takeout-test-2-{number:03}.zip'
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(path, 'w') as z:
                for name, value in entries:
                    z.writestr(self.prefix + name, json.dumps(value) if isinstance(value, dict) else value)
        return path

    def sidecar(self, title='photo.jpg'):
        return {'title': title, 'photoTakenTime': {'timestamp': '1767225600'},
                'people': [{'name': 'NEVER RETAIN THIS'}],
                'geoData': {'latitude': 0, 'longitude': 0},
                'geoDataExif': {'latitude': 30, 'longitude': -90}}

    def test_cross_part_truncated_name_title_match_and_field_coverage(self):
        a = self.archive(1, [('photo.jpg', b'abc')])
        b = self.archive(3, [('trunc.supplemental-metadata.json', self.sidecar())])
        result = audit([a, b], [{'path': 'elsewhere/photo.jpg', 'size': 3}])
        self.assertEqual(result['json_counts']['sidecar_unique'], 1)
        self.assertEqual(result['internal_part_gaps'], {'takeout-test-2': [2]})
        self.assertEqual(result['sidecar_coverage_all']['capture_year_2026'], 1)
        self.assertEqual(result['sidecar_coverage_all']['geoDataExif_nonzero_location'], 1)
        self.assertNotIn('geoData_nonzero_location', result['sidecar_coverage_all'])
        self.assertNotIn('NEVER RETAIN THIS', json.dumps(result))
        self.assertEqual(result['catalog_candidate_overlap']['name_size_candidate'], 1)

    def test_conflicting_title_and_filename_stays_ambiguous(self):
        a = self.archive(1, [('photo.jpg', b'abc'), ('other.jpg', b'xyz'),
                             ('other.jpg.json', self.sidecar())])
        result = audit([a], [])
        self.assertEqual(result['json_counts']['sidecar_ambiguous'], 1)
        self.assertEqual(result['media_with_unique_sidecar_candidate'], 0)

    def test_duplicate_zip_member_names_are_distinct_candidates(self):
        a = self.archive(1, [('photo.jpg', b'abc'), ('photo.jpg', b'xyz'),
                             ('photo.jpg.json', self.sidecar())])
        result = audit([a], [])
        self.assertEqual(result['media_entries'], 2)
        self.assertEqual(result['json_counts']['sidecar_ambiguous'], 1)

    def test_other_export_family_does_not_match(self):
        a = self.archive(1, [('photo.jpg', b'abc')])
        b = self.archive(2, [('photo.jpg.json', self.sidecar())])
        other = b.with_name('takeout-another-2-002.zip')
        b.rename(other)
        self.assertEqual(audit([a, other], [])['json_counts']['sidecar_unmatched'], 1)

    def test_bad_json_album_metadata_and_invalid_values_are_not_media_facts(self):
        data = self.sidecar()
        data['photoTakenTime'] = {'timestamp': 'nan'}
        data['geoDataExif'] = {'latitude': 100, 'longitude': 10}
        a = self.archive(1, [('bad.json', b'{'), ('huge.json', b' ' * (MAX_JSON + 1)),
                             ('album.json', {'title': 'Album'}), ('photo.jpg.json', data)])
        result = audit([a], [])
        self.assertEqual(result['json_counts']['json_unreadable'], 1)
        self.assertEqual(result['json_counts']['json_oversized'], 1)
        self.assertEqual(result['json_counts']['json_without_photo_taken_time'], 1)
        self.assertEqual(result['sidecar_coverage_all'], {})

    def test_size_and_filename_matches_are_not_claimed_as_duplicates(self):
        a = self.archive(1, [('photo.jpg', b'abc'), ('renamed.jpg', b'xyz'), ('new.jpg', b'longer')])
        result = audit([a], [{'path': 'photo.jpg', 'size': 3}])
        overlap = result['catalog_candidate_overlap']
        self.assertEqual(overlap['name_size_candidate'], 1)
        self.assertEqual(overlap['size_only_candidate'], 1)
        self.assertEqual(overlap['no_equal_size_in_catalog'], 1)
        self.assertEqual(result['repeated_size_crc_extra_entries'], 0)


if __name__ == '__main__':
    unittest.main()
