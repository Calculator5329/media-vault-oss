import os,unittest
from unittest.mock import patch
from tests.test_library import LibraryTests
from src import previews


class PreviewWarmerTests(unittest.TestCase):
    def test_warmer_generates_every_missing_preview_once_and_reads_originals_only(self):
        f=LibraryTests();f.setUp();library=f.build()
        before=[(p,p.stat().st_mtime_ns) for p in f.f.source.rglob('*') if p.is_file()]
        todo=previews.missing(library)
        self.assertEqual(len(todo),len(library.items),'fixture starts without previews')
        self.assertEqual(todo[-1]['id'],library.search(query='new.jpg')['items'][0]['id'],'ZIP members come after plain files')
        lucky=todo[0]['id'];calls=[]
        def fake(key):
            calls.append(key)
            if key!=lucky:raise RuntimeError('Preview could not be generated')
            library.thumbnails.mkdir(parents=True,exist_ok=True);target=library.thumbnails/(key+'.jpg');target.write_bytes(b'synthetic jpeg');return target
        with patch.object(library,'thumbnail',side_effect=fake):
            result=previews.warm(library,workers=2)
            self.assertEqual((result['generated'],result['failed'],result['candidates']),(1,len(todo)-1,len(todo)))
            self.assertEqual([i['id'] for i in previews.missing(library)],[i['id'] for i in todo if i['id']!=lucky])
            again=previews.warm(library,workers=2)
        self.assertEqual(again['generated'],0,'the generated preview is not redone')
        self.assertEqual(calls.count(lucky),1)
        self.assertEqual(before,[(p,p.stat().st_mtime_ns) for p in f.f.source.rglob('*') if p.is_file()],'originals untouched')
