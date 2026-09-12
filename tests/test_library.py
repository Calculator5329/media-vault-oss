"""Verified sources become one timeline entry without modifying originals."""
import json
import unittest
import zipfile
from unittest.mock import patch

from tests import test_imports
from src import imports,metadata
from src.library import Library


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.f=test_imports.ImportTests();self.f.setUp()
        with zipfile.ZipFile(self.f.zip,'a') as z:
            z.writestr('Google Photos/new.jpg',b'new additional image')
            z.writestr('Google Photos/new.jpg.json',json.dumps({'title':'new.jpg',
                'photoTakenTime':{'timestamp':'1767225600'},'geoData':{'latitude':40,'longitude':-90}}))

    def build(self,limit=None):
        with self.f.connect() as conn:
            imports.inventory(conn,self.f.catalog,self.f.exports)
            imports.hash_pending(conn,limit=limit)
        metadata.refresh(self.f.db,self.f.exports,self.f.root/'metadata.db')
        return Library(self.f.catalog,self.f.db,organization=self.f.root/'corrections/organization.jsonl')

    def test_verified_copies_collapse_but_sources_and_metadata_survive(self):
        library=self.build()
        self.assertEqual(len(library.items),3)
        self.assertEqual(library.summary()['source_occurrences'],4)
        original=library.search(query='a.jpg')['items'][0]
        self.assertEqual(original['source_count'],2)
        self.assertEqual(len(library.metadata(original['id'])['sources']),2)
        new=library.search(query='new.jpg')['items'][0]
        self.assertEqual(new['day'],'2026-01-01')
        self.assertEqual(new['location']['lat'],40)
        self.assertEqual(library.slideshow(year='2026')['total'],1)
        self.assertTrue(library.metadata(new['id'])['facts'])
        self.assertEqual((self.f.source/'a.jpg').read_bytes(),b'same image bytes')

    def test_unverified_master_is_not_silently_deduplicated(self):
        library=self.build(limit=1)
        self.assertEqual(library.summary()['unverified_items'],1)
        self.assertEqual(len(library.items),2)

    def test_zip_preview_writes_only_cache_and_reuses_it(self):
        library=self.build()
        item=library.search(query='new.jpg')['items'][0]
        class SyntheticImage:
            def thumbnail(self,size): pass
            def save(self,path,**kwargs): path.write_bytes(b'synthetic jpeg')
            def close(self): pass
        with patch('src.vision.read_image',return_value=SyntheticImage()) as reader:
            path=library.thumbnail(item['id'])
            self.assertEqual(path.parent,library.thumbnails)
            self.assertEqual(library.thumbnail(item['id']),path)
            self.assertEqual(reader.call_count,1)

    def test_visual_filters_apply_before_ranking_and_return_current_items(self):
        library=self.build()
        library.encoder=object()
        library.visual_database.touch()
        item=library.search(year='2026')['items'][0]
        def search(query,limit,allowed):
            self.assertEqual(allowed,{item['content_hash']})
            return {'results':[{'content_hash':item['content_hash'],'similarity':0.5}],
                    'indexed_contents':3,'eligible_contents':1,'meaning':'Synthetic ranking'}
        with patch('src.vision.SearchIndex.search',side_effect=search):
            result=library.visual('red car',year='2026')
        self.assertEqual(result['items'][0]['id'],item['id'])
        self.assertEqual(result['items'][0]['similarity'],0.5)
        self.assertIsNone(result['next_offset'])
        library.visual_lock.acquire()
        try:
            with self.assertRaises(RuntimeError):library.visual('red car')
        finally:library.visual_lock.release()


if __name__=='__main__':
    unittest.main()
