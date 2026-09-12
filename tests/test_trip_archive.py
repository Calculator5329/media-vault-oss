"""Archived trips retain membership decisions and restore across rebuilds."""
import unittest
from src.library import Library
from tests import test_trip_review

class TripArchiveTests(unittest.TestCase):
    def test_archive_restore_preserves_exclusions_and_general_library(self):
        f,v,trip=test_trip_review.TripReviewTests().build()
        digest=v.search(trip=trip)['items'][0]['content_hash'];total=v.search()['total']
        v.organize('exclude_trip',{'trip':trip,'contents':[digest]})
        v.organize('archive_trip',{'trip':trip})
        v=Library(f.f.catalog,f.f.db,organization=v.organization.path)
        self.assertEqual(v.trips()['trips'],[])
        self.assertEqual(v.trips(archived=True)['trips'][0]['id'],trip)
        self.assertEqual(v.trips(archived=True)['proposals'],[])
        self.assertEqual(v.search()['total'],total)
        self.assertEqual(v.trip_exclusions(trip)['total'],1)
        v.organize('restore_trip',{'trip':trip})
        self.assertEqual(v.trips()['trips'][0]['count'],0)
        self.assertEqual(v.trips(archived=True)['trips'],[])
        v.organize('restore_trip_items',{'trip':trip,'contents':[digest]})
        self.assertEqual(v.trips()['trips'][0]['count'],1)

    def test_archived_names_are_not_active_search_terms(self):
        f,v,trip=test_trip_review.TripReviewTests().build()
        self.assertEqual(v.search(query='Example journey')['total'],1)
        v.organize('archive_trip',{'trip':trip})
        self.assertEqual(v.search(query='Example journey')['total'],0)
        self.assertEqual(v.search(trip=trip)['total'],1)
        for op in ('archive_trip','restore_trip'):
            with self.assertRaises(ValueError):v.organize(op,{'trip':'f'*32})
        v.organize('restore_trip',{'trip':trip})
        self.assertEqual(v.search(query='Example journey')['total'],1)
