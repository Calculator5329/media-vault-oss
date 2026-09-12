"""Owner query judgments survive rebuilds and remain scoped and reversible."""
import unittest
from src.library import Library
from src import descriptions
from tests import test_library
from tests.test_descriptions import scene

class RelevanceTests(unittest.TestCase):
    def setUp(self):
        self.f=test_library.LibraryTests();self.f.setUp();self.v=self.f.build()

    def test_feedback_reorders_without_hiding_and_reset_survives_rebuild(self):
        rows=self.v.items;first=rows[0]['content_hash'];last=rows[-1]['content_hash']
        data={'mode':'visual','query':'  RED   car ','item':first,'verdict':'mismatch'}
        self.v.organize('search_review',data)
        self.v.organize('search_review',{**data,'item':last,'verdict':'match'})
        rebuilt=Library(self.f.f.catalog,self.f.f.db,organization=self.v.organization.path)
        ranked=rebuilt.reviewed_search(rows,'red car','visual')
        self.assertEqual(len(ranked),len(rows));self.assertEqual(ranked[0]['content_hash'],last);self.assertEqual(ranked[-1]['content_hash'],first)
        self.assertTrue(all(i['search_verdict'] is None for i in rebuilt.reviewed_search(rows,'blue car','visual')))
        self.assertTrue(all(i['search_verdict'] is None for i in rebuilt.reviewed_search(rows,'red car','descriptions')))
        rebuilt.organize('search_review',{**data,'verdict':'reset'})
        self.assertEqual(rebuilt.reviewed_search(rows,'red car','visual')[1]['content_hash'],first)
        self.assertEqual(len(rebuilt.organization.path.read_text().splitlines()),3)
        self.assertEqual(rebuilt.metadata(rows[0]['id'])['facts'],self.v.metadata(rows[0]['id'])['facts'])

    def test_description_pagination_uses_owner_order(self):
        class Backend:
            identity='synthetic-reviewed-descriptions'
            def describe(self,image):return scene()
        descriptions.index(self.f.f.db,self.f.f.root/'descriptions.db',Backend(),reader=lambda row:'synthetic')
        initial=self.v.described('red car')['items'];last=initial[-1]
        self.v.organize('search_review',{'mode':'descriptions','query':'red car','item':last['content_hash'],'verdict':'match'})
        page=self.v.described('red car',limit=1)
        self.assertEqual(page['total'],3);self.assertEqual(page['items'][0]['content_hash'],last['content_hash']);self.assertEqual(page['items'][0]['search_verdict'],'match')

    def test_unknown_items_and_invalid_review_modes_are_rejected(self):
        data={'mode':'visual','query':'red car','item':self.v.items[0]['content_hash'],'verdict':'match'}
        for invalid in ({**data,'item':'f'*64},{**data,'query':' '},{**data,'mode':'invented'},{**data,'verdict':'maybe'},{**data,'mode':'video_images'}):
            with self.assertRaises(ValueError):self.v.organize('search_review',invalid)
        self.assertFalse(self.v.organization.path.exists())

    def test_video_judgments_are_per_sample_not_whole_video(self):
        from tests.test_video_moments import VideoMomentTests,SampleBackend,Encoder,reader
        from src import frames,scenes
        fixture=VideoMomentTests();fixture.setUp();root=fixture.f.f.root
        frames.index(fixture.f.f.db,root/'frames.db',SampleBackend(),reader=reader)
        scenes.index(fixture.f.f.db,root/'scenes.db',Encoder())
        before=fixture.v.video_moments('red car')['items']
        fixture.v.organize('search_review',{'mode':'video_images','query':'red car','item':before[0]['frame_id'],'verdict':'mismatch'})
        after=fixture.v.video_moments('red car')['items']
        self.assertEqual(after[0]['frame_id'],before[1]['frame_id']);self.assertIsNone(after[0]['search_verdict'])
        self.assertEqual(after[1]['search_verdict'],'mismatch')
        self.assertEqual(after[0]['content_hash'],after[1]['content_hash'])
