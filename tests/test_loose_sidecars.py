"""Adjacent source metadata stays verified, scoped and separate from identities."""
from contextlib import closing
import json
import os
import sqlite3
import unittest
from src import catalog,imports,metadata
from tests import test_metadata
from src.kit import validate_row


class LooseSidecarTests(unittest.TestCase):
    def setUp(self):
        self.f=test_metadata.MetadataTests();self.f.setUp()

    def sidecar(self,name='a.jpg.json',**changes):
        data={'title':'a.jpg','photoTakenTime':{'timestamp':'1704067200'},'people':[{'name':'DO NOT IMPORT'}],
              'geoData':{'latitude':40,'longitude':-90},'customRawField':{'retained':True},**changes}
        path=self.f.source/name;path.write_text(json.dumps(data));return path

    def index(self,hash_files=True):
        _,conn=catalog.connect(self.f.source,self.f.catalog)
        with closing(conn):catalog.inventory(self.f.source,conn)
        self.f.index(hash_files)

    def facts(self):
        with closing(sqlite3.connect(self.f.output)) as conn:
            conn.row_factory=sqlite3.Row
            return [dict(r) for r in conn.execute('SELECT * FROM metadata_facts')]

    def test_same_folder_claims_keep_raw_fields_provenance_and_competing_dates(self):
        path=self.sidecar();self.index()
        result=metadata.refresh(self.f.imports,self.f.exports,self.f.output)
        self.assertEqual(result['loose_sidecar_attached'],1)
        facts=[r for r in self.facts() if r['extractor']=='google-photos-loose-sidecar']
        self.assertEqual(len(facts),3)
        self.assertNotIn('DO NOT IMPORT',json.dumps(facts))
        self.assertTrue(json.loads(next(r['value_json'] for r in facts if r['attribute']=='raw_sidecar'))['customRawField']['retained'])
        for fact in facts:
            validate_row('metadata_facts',fact)
            self.assertEqual(fact['source_path'],str(path))
            self.assertEqual(len(json.loads(fact['source_span'])['sidecar_hash']),64)
        self.assertEqual(result['contents_with_competing_capture_dates'],1)
        with closing(sqlite3.connect(self.f.output)) as conn:
            counts=json.loads(conn.execute("SELECT value FROM settings WHERE key='metadata_counts'").fetchone()[0])
        self.assertEqual(counts['loose_sidecar_attached'],1)

    def test_sidecars_remain_in_imports_without_becoming_photo_tiles(self):
        from src.library import Library
        self.sidecar();self.index();metadata.refresh(self.f.imports,self.f.exports,self.f.output)
        viewer=Library(self.f.catalog,self.f.imports,organization=self.f.root/'organization.jsonl')
        self.assertEqual(viewer.search()['total'],1)
        self.assertEqual(viewer.search(query='.json')['total'],0)
        self.assertTrue(any(r['attribute']=='raw_sidecar' for r in viewer.metadata(viewer.items[0]['id'])['facts']))
        with closing(sqlite3.connect(self.f.imports)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM occurrences WHERE source LIKE '%.json'").fetchone()[0],1)

    def test_ambiguous_and_cross_directory_names_are_not_guessed(self):
        (self.f.source/'b.jpg').write_bytes(b'other synthetic picture')
        self.sidecar(title='b.jpg')
        self.sidecar('unmatched.json',title='../a.jpg')
        nested=self.f.source/'nested';nested.mkdir()
        (nested/'missing.jpg.json').write_text(json.dumps({'title':'a.jpg','photoTakenTime':{'timestamp':'1704067200'}}))
        self.index();result=metadata.refresh(self.f.imports,self.f.exports,self.f.output)
        self.assertEqual(result['loose_sidecar_ambiguous'],1)
        self.assertEqual(result['loose_sidecar_unmatched'],2)
        self.assertEqual(result.get('loose_sidecar_attached',0),0)

    def test_unverified_and_unrecognized_json_remain_explicit(self):
        self.sidecar();self.index(False)
        result=metadata.refresh(self.f.imports,self.f.exports,self.f.output)
        self.assertEqual(result['loose_sidecar_waiting_for_hash'],1)
        self.assertEqual(result.get('loose_sidecar_attached',0),0)
        (self.f.source/'settings.json').write_text('{"not":"photo metadata"}')
        (self.f.source/'broken.json').write_text('{broken')
        self.index();result=metadata.refresh(self.f.imports,self.f.exports,self.f.output)
        self.assertEqual(result['loose_json_unrecognized'],1)
        self.assertEqual(result['loose_sidecar_unreadable'],1)

    def test_changed_json_with_preserved_timestamp_keeps_prior_metadata(self):
        path=self.sidecar();self.index();metadata.refresh(self.f.imports,self.f.exports,self.f.output)
        before=self.facts();info=path.stat()
        path.write_text(path.read_text().replace('1704067200','1704153600'))
        os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns))
        with self.assertRaisesRegex(ValueError,'Sidecar changed'):
            metadata.refresh(self.f.imports,self.f.exports,self.f.output)
        self.assertEqual(self.facts(),before)
