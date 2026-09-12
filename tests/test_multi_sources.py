"""Additional synthetic folders retain shared identities and owner corrections."""
from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch
from src import catalog,imports,jobs
from tests import test_jobs


class MultiSourceTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_jobs.JobTests();self.fixture.setUp()
        self.root=self.fixture.f.f.root
        self.source=self.fixture.f.f.source
        self.exports=self.fixture.f.f.exports
        self.extra=self.root/'additional-folder';self.extra.mkdir()
        self.config=self.fixture.config
        self.viewer=self.fixture.viewer

    def cycle(self):
        with patch.object(catalog,'inspect',return_value={'errors':[],'date':{'value':'2025-04-03T12:00:00','meaning':'capture','source':'synthetic-exif','timezone_known':False},'width':640,'height':480,'exif':{'Model':'Synthetic Camera'}}):
            return jobs.cycle(self.config,self.root,self.exports,seconds=2,limit=20)

    def add_source(self):
        self.config.write_text(json.dumps({'sources':[str(self.source),str(self.extra)]}))

    def test_addition_connects_metadata_deduplicates_and_preserves_owner_corrections(self):
        self.cycle();v=self.viewer.snapshot()
        original=v.search(query='a.jpg')['items'][0]
        person=v.organize('person',{'person':'','name':'Synthetic Owner'})['data']['person']
        v.organize('add',{'person':person,'contents':[original['content_hash']]})
        (self.extra/'copy.jpg').write_bytes((self.source/'a.jpg').read_bytes())
        (self.extra/'extra-photo.jpg').write_bytes(b'new unique synthetic photo')
        self.add_source();result=self.cycle()
        self.assertEqual(result['state'],'complete')
        current=self.viewer.snapshot()
        added=current.search(query='extra-photo.jpg')['items'][0]
        self.assertEqual(added['day'],'2025-04-03')
        self.assertEqual(added['camera'],'Synthetic Camera')
        self.assertEqual(added['width'],640)
        self.assertEqual(added['source_type'],'file')
        self.assertEqual(current.search(person=person)['total'],1)
        self.assertEqual(current.by_content[original['content_hash']]['source_count'],3)
        self.assertTrue(current.metadata(added['id'])['facts'])
        self.assertEqual(current.summary()['files'],4)
        # Preview is routed through the same verified source reader as ZIP media.
        class Image:
            def thumbnail(self,size):pass
            def save(self,path,**kwargs):path.write_bytes(b'synthetic preview')
            def close(self):pass
        with patch('src.vision.read_image',return_value=Image()) as read:
            current.thumbnail(added['id'])
            self.assertEqual(read.call_args.args[0]['source'],str(self.extra/'extra-photo.jpg'))
        again=self.cycle()
        self.assertEqual(again['imports']['verified_contents'],result['imports']['verified_contents'])
        with imports.database(self.root/'imports.db',[self.source,self.extra,self.exports]) as conn:
            with self.assertRaisesRegex(ValueError,'every registered source'):
                imports.inventory(conn,self.root/'catalog.db',self.exports)
            self.assertEqual(imports.summary(conn)['verified_contents'],result['imports']['verified_contents'])

    def test_offline_extra_keeps_previous_snapshot_and_removal_is_refused(self):
        self.cycle();self.add_source();self.cycle()
        before=self.viewer.snapshot()
        self.extra.rename(self.root/'retained-offline')
        self.assertEqual(self.cycle()['state'],'waiting_for_sources')
        self.assertIs(self.viewer.snapshot(),before)
        self.config.write_text(json.dumps({'sources':[str(self.source)]}))
        with self.assertRaisesRegex(ValueError,'different sources'):self.cycle()
        self.assertIs(self.viewer.snapshot(),before)

    def test_existing_derived_store_accepts_expansion_but_not_replacement(self):
        path=self.root/'existing-derived.db'
        with imports.database(path,[self.source,self.exports]) as c:
            c.execute('CREATE TABLE retained(value TEXT)')
            c.execute("INSERT INTO retained VALUES('synthetic fact')");c.commit()
        with imports.database(path,[self.source,self.extra,self.exports]) as c:
            self.assertEqual(c.execute('SELECT value FROM retained').fetchone()[0],'synthetic fact')
            self.assertEqual(len(json.loads(c.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0])),3)
        with self.assertRaises(ValueError):
            with imports.database(path,[self.extra,self.exports]):pass
        with closing(sqlite3.connect(path)) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM retained').fetchone()[0],1)
