"""Equivalent timestamps are not conflicts; owner choices survive rebuilding."""
import unittest
from src.dates import evidence
from src.library import Library
from tests import test_library


def fact(value,meaning='capture'):
    return {'attribute':'date','value':{'value':value,'meaning':meaning},'source_path':'synthetic','source_span':'embedded','extractor':'fixture'}


class DateTests(unittest.TestCase):
    def test_explicit_offsets_compare_as_instants(self):
        result=evidence([fact('2026-01-01T00:00:00Z'),fact('2025-12-31T18:00:00-06:00')])
        self.assertEqual(result['status'],'equivalent')
        self.assertEqual(len(result['choices']),2)
        self.assertEqual(evidence([fact('2026-01-01T00:00:00Z'),fact('2026-01-01T01:00:00Z')])['status'],'conflict')

    def test_unknown_timezone_is_never_assumed_and_creation_is_separate(self):
        self.assertEqual(evidence([fact('2026-01-01T00:00:00'),fact('2026-01-01T00:00:00Z')])['status'],'timezone_unknown')
        self.assertEqual(evidence([fact('2026-01-01T00:00:00Z'),fact('2025-01-01T00:00:00Z','Google Photos creation')])['status'],'single')
        self.assertEqual(evidence([fact('2026-02-30T00:00:00Z')])['status'],'invalid')

    def test_owner_choice_changes_filters_and_survives_rebuild_then_reset(self):
        f=test_library.LibraryTests();f.setUp();viewer=f.build();photo=viewer.search(query='new.jpg')['items'][0];digest=photo['content_hash']
        original=viewer.facts[digest][:]
        extra=fact('2024-06-15T12:00:00Z');viewer.facts[digest].append(extra)
        viewer.by_id[photo['id']]['date_status']='conflict'
        self.assertEqual(viewer.search(kind='date_review')['total'],1)
        choice=next(c for c in viewer.date_review(photo['id'])['choices'] if c['date']['value'].startswith('2024'))
        viewer.organize('date',{'content_hash':digest,'choice':choice['id']})
        self.assertEqual(viewer.search(year='2024')['total'],1)
        self.assertEqual(viewer.search(kind='date_review')['total'],0)
        self.assertEqual(viewer.slideshow(year='2024')['total'],1)
        rebuilt=Library(f.f.catalog,f.f.db,organization=viewer.organization.path)
        self.assertEqual(rebuilt.search(year='2024')['total'],1)
        self.assertEqual(rebuilt.metadata(photo['id'])['dates']['selected']['date']['value'],'2024-06-15T12:00:00Z')
        rebuilt.organize('reset_date',{'content_hash':digest})
        self.assertEqual(rebuilt.search(year='2026')['total'],1)
        self.assertEqual(len(viewer.organization.path.read_text().splitlines()),2)
        self.assertEqual(rebuilt.facts[digest],original)
        with self.assertRaises(ValueError):rebuilt.organize('date',{'content_hash':digest,'choice':'nonexistent'})
        self.assertEqual(len(viewer.organization.path.read_text().splitlines()),2)
