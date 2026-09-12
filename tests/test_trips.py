"""Visits remain proposals; saved trip filters and edits survive rebuilding."""
import unittest
from src.trips import proposals
from src.library import Library
from tests import test_library


class TripTests(unittest.TestCase):
    def test_visits_split_on_gaps_and_exclude_unresolved_dates(self):
        def item(n,day,status='single'):
            return {'id':str(n),'content_hash':str(n),'day':day,'location':{'lat':40,'lon':-90},'date':{'meaning':'capture'},'date_status':status}
        items=[item(i,'2026-01-01') for i in range(3)]+[item(i+3,'2026-02-01') for i in range(3)]+[item(9,'2026-01-02','conflict')]
        result=proposals(items,lambda i:'400:-900',{'400:-900':'Synthetic Place'})
        self.assertEqual(len(result),2);self.assertEqual([r['count'] for r in result],[3,3])
        self.assertEqual(result[1]['before'],'2026-01-01')

    def test_trip_name_search_and_edit_survive_rebuild(self):
        f=test_library.LibraryTests();f.setUp();v=f.build();place=v.places()['places'][0]['id']
        data={'trip':'','name':'Synthetic winter visit','after':'2026-01-01','before':'2026-01-02','place':place}
        saved=v.organize('trip',data)['data'];self.assertEqual(v.trips()['trips'][0]['count'],1)
        self.assertEqual(v.search(query='winter visit')['total'],1)
        rebuilt=Library(f.f.catalog,f.f.db,organization=v.organization.path)
        self.assertEqual(rebuilt.trips()['trips'][0]['name'],data['name'])
        rebuilt.organize('trip',{**saved,'name':'Renamed visit','after':'2025-01-01','before':'2025-01-02'})
        self.assertEqual(rebuilt.trips()['trips'][0]['count'],0)
        self.assertEqual(rebuilt.search(query='winter visit')['total'],0)
        self.assertEqual(len(v.organization.path.read_text().splitlines()),2)
        for invalid in ({**saved,'before':'2024-01-01'},{**saved,'place':'0:0'},{**saved,'name':''}):
            with self.assertRaises(ValueError):rebuilt.organize('trip',invalid)
        self.assertEqual(len(v.organization.path.read_text().splitlines()),2)
