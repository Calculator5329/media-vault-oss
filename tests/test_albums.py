"""Saved order and review choices survive index rebuilds and archiving."""
import unittest
from unittest.mock import patch
from src.library import Library
from tests import test_library

class AlbumTests(unittest.TestCase):
    def setUp(self):
        self.f=test_library.LibraryTests();self.f.setUp();self.v=self.f.build()

    def test_order_edits_archive_and_restore_survive_rebuild(self):
        contents=[i['content_hash'] for i in self.v.items][::-1]
        saved=self.v.organize('album',{'album':'','name':'Synthetic highlights','contents':contents})['data']
        rebuilt=Library(self.f.f.catalog,self.f.f.db,organization=self.v.organization.path)
        self.assertEqual([i['content_hash'] for i in rebuilt.album_items(saved['album'])['items']],contents)
        rebuilt.organize('album',{**saved,'contents':contents[1:]})
        self.assertEqual(rebuilt.albums()['albums'][0]['count'],2)
        rebuilt.organize('archive_album',{'album':saved['album']})
        self.assertEqual(rebuilt.albums()['albums'],[])
        self.assertEqual(len(rebuilt.albums(archived=True)['albums']),1)
        rebuilt.organize('restore_album',{'album':saved['album']})
        self.assertEqual(len(rebuilt.albums()['albums']),1)
        self.assertEqual(len(rebuilt.organization.path.read_text().splitlines()),4)
        with self.assertRaises(ValueError):rebuilt.organize('album',{**saved,'contents':['a'*64]})
        with self.assertRaises(ValueError):rebuilt.organize('album',{**saved,'contents':[]})
        with self.assertRaises(ValueError):rebuilt.organize('archive_album',{'album':'a'*32})
        self.assertEqual(len(rebuilt.organization.path.read_text().splitlines()),4)

    def test_highlights_respect_scope_and_keep_timeline_endpoints(self):
        self.assertEqual(self.v.highlights(year='2026')['total'],1)
        selected=self.v.highlights(limit=2)
        self.assertEqual(len(selected['items']),2)
        self.assertEqual(selected['items'][0]['day'],'2026-01-01')
        with patch.object(self.v,'described',return_value={'items':self.v.items[:1]}) as described:
            result=self.v.highlights(mode='descriptions',query='red car',category='photograph')
            self.assertEqual(result['total'],1)
            self.assertEqual(described.call_args.kwargs['category'],'photograph')
        with self.assertRaises(ValueError):self.v.highlights(mode='moments')
        with self.assertRaises(ValueError):self.v.highlights(limit=0)

    def test_unavailable_content_is_retained_and_description_coverage_is_photos(self):
        item=self.v.items[0]
        saved=self.v.organize('album',{'album':'','name':'Keep unavailable','contents':[item['content_hash']]})['data']
        self.v.by_content.pop(item['content_hash'])
        self.assertEqual(self.v.album_items(saved['album'])['missing'],1)
        self.assertEqual(self.v.albums()['albums'][0]['contents'],saved['contents'])
        with patch.object(self.v,'_select',return_value=[{**item,'kind':'video'},self.v.items[1]]),patch.object(self.v,'descriptions',return_value={}):
            self.assertEqual(self.v.described()['eligible_contents'],1)

    def test_assistant_album_lookup_is_typed_and_rejects_extra_filters(self):
        from src.query import parser,execute
        album={'id':'a'*32,'name':'Synthetic weekend'}
        item=self.v.items[0]
        def request(endpoint,params):
            if endpoint=='albums':return {'albums':[album]}
            self.assertEqual(endpoint,'album/'+album['id']);self.assertEqual(params,{})
            return {'items':[item],'total':1,'missing':0}
        result=execute(parser().parse_args(['--album','synthetic weekend','--ids']),request)
        self.assertEqual(result['items'][0]['content_hash'],item['content_hash'])
        with self.assertRaises(ValueError):execute(parser().parse_args(['--album','Synthetic weekend','--year','2026']),request)
