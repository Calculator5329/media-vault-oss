"""Trip corrections survive rebuilding and constrain retrieval before pagination."""
import unittest
from src.library import Library
from src.query import execute,parser
from tests import test_library

class TripReviewTests(unittest.TestCase):
    def build(self):
        f=test_library.LibraryTests();f.setUp();v=f.build()
        trip=v.organize('trip',{'trip':'','name':'Example journey','after':'2026-01-01','before':'2026-01-02','place':''})['data']['trip']
        return f,v,trip

    def test_exclude_rebuild_search_slideshow_and_restore(self):
        f,v,trip=self.build();item=v.search(trip=trip)['items'][0];digest=item['content_hash']
        total=v.search()['total']
        v.organize('exclude_trip',{'trip':trip,'contents':[digest]})
        v=Library(f.f.catalog,f.f.db,organization=v.organization.path)
        self.assertEqual(v.search()['total'],total)
        self.assertEqual(v.search(trip=trip)['total'],0)
        self.assertEqual(v.search(query='Example journey')['total'],0)
        self.assertEqual(v.slideshow(trip=trip)['total'],0)
        self.assertEqual(v.highlights(trip=trip)['total'],0)
        self.assertEqual(v.trips()['trips'][0]['count'],0)
        self.assertEqual(v.trip_exclusions(trip)['items'][0]['content_hash'],digest)
        v.organize('restore_trip_items',{'trip':trip,'contents':[digest]})
        self.assertEqual(v.search(trip=trip)['total'],1)
        self.assertEqual(v.slideshow(trip=trip)['total'],1)
        self.assertEqual(v.trip_exclusions(trip)['total'],0)
        self.assertEqual(len(v.organization.path.read_text().splitlines()),3)

    def test_trip_scope_is_explicit_and_videos_can_be_removed(self):
        f,v,trip=self.build();item=v.search(trip=trip)['items'][0];item=v.by_content[item['content_hash']];item['kind']='video'
        v.organize('exclude_trip',{'trip':trip,'contents':[item['content_hash']]})
        self.assertEqual(v.search(trip=trip,kind='video')['total'],0)
        self.assertEqual(v.search(kind='video')['total'],1)
        other=next(i['content_hash'] for i in v.items if i['content_hash']!=item['content_hash'])
        for op,data in [('exclude_trip',{'trip':trip,'contents':[other]}),('exclude_trip',{'trip':'f'*32,'contents':[other]}),('restore_trip_items',{'trip':'f'*32,'contents':[other]})]:
            with self.assertRaises(ValueError):v.organize(op,data)
        with self.assertRaises(ValueError):v.search(trip='f'*32)

    def test_assistant_passes_identity_to_apply_corrections(self):
        f,v,trip=self.build();item=v.search(trip=trip)['items'][0]
        v.organize('exclude_trip',{'trip':trip,'contents':[item['content_hash']]})
        def request(endpoint,params):
            if endpoint=='trips':return v.trips()
            data=dict(params);data['query']=data.pop('q')
            return v.search(**data)
        self.assertEqual(execute(parser().parse_args(['--trip','Example journey','--count']),request),{'total':0})
