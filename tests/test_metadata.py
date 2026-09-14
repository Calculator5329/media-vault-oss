"""Sidecar facts never invent identity or erase conflicting evidence."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from src import catalog, imports, metadata
from src.kit import validate_row
from tests.scratch import scratch


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.root = scratch()
        self.source, self.exports = self.root/'source', self.root/'exports'
        self.source.mkdir()
        self.exports.mkdir()
        self.catalog, self.imports, self.output = [self.root/name for name in ('catalog.db','imports.db','metadata.db')]
        (self.source/'a.jpg').write_bytes(b'image')
        source, conn = catalog.connect(self.source,self.catalog)
        catalog.inventory(source,conn)
        conn.close()
        self.zip = self.exports/'takeout-test-001.zip'
        with zipfile.ZipFile(self.zip,'w') as z:
            z.writestr('Google Photos/a.jpg',b'image')
            for name,stamp in [('a.jpg.json','1767225600'),('a.jpg.supplemental-metadata.json','1767312000')]:
                z.writestr('Google Photos/'+name,json.dumps({'title':'a.jpg','photoTakenTime':{'timestamp':stamp},
                    'creationTime':{'timestamp':'1770000000'},'people':[{'name':'DO NOT IMPORT'}],
                    'geoData':{'latitude':0,'longitude':0},'description':'Synthetic description'}))

    def index(self, hash_files=True):
        with imports.database(self.imports,[self.source,self.exports]) as conn:
            imports.inventory(conn,self.catalog,self.exports)
            if hash_files:
                imports.hash_pending(conn)

    def test_unhashed_candidates_do_not_publish_facts(self):
        self.index(False)
        result = metadata.refresh(self.imports,self.exports,self.output)
        self.assertEqual(result['sidecar_waiting_for_hash'],2)
        self.assertEqual(result['facts'],0)

    def test_all_competing_dates_retained_with_provenance_and_no_people(self):
        self.index()
        result = metadata.refresh(self.imports,self.exports,self.output)
        self.assertEqual(result['sidecar_attached'],2)
        self.assertEqual(result['contents_with_competing_capture_dates'],1)
        with imports.database(self.output,[self.source,self.exports]) as conn:
            facts = [dict(r) for r in conn.execute('SELECT * FROM metadata_facts')]
            self.assertEqual(len([r for r in facts if r['attribute']=='date']),4)
            self.assertFalse(any(r['attribute']=='location' for r in facts))
            self.assertNotIn('DO NOT IMPORT',json.dumps(facts))
            for row in facts:
                validate_row('metadata_facts',row)

    def test_loose_sidecars_beside_files_attach_including_truncated_and_copy_names(self):
        """An extracted Takeout has IMG.jpg.json next to IMG.jpg; nobody keeps the zip."""
        for name in ('b.jpg','b(1).jpg','c.jpg','longname_2020.jpg'):
            (self.source/name).write_bytes(b'image-'+name.encode())
        sidecars={'b.jpg.json':('b.jpg','1600000000'),'b.jpg(1).json':('b.jpg','1600000100'),
                  'c.jpg.supplemental-metadata.json':('c.jpg','1600000200'),
                  'longname_2020.jpg.supplemental-metad.json':('longname_2020.jpg','1600000300')}
        for name,(title,stamp) in sidecars.items():
            (self.source/name).write_text(json.dumps({'title':title,'photoTakenTime':{'timestamp':stamp},'people':[{'name':'DO NOT IMPORT'}]}))
        (self.source/'orphan.jpg.json').write_text(json.dumps({'title':'orphan.jpg','photoTakenTime':{'timestamp':'1600000400'}}))
        (self.source/'notes.json').write_text(json.dumps({'unrelated':True}))
        source,conn=catalog.connect(self.source,self.catalog);catalog.inventory(source,conn);conn.close()
        self.index()
        result=metadata.refresh(self.imports,self.exports,self.output)
        self.assertEqual((result['sidecar_attached'],result['sidecar_unmatched']),(2+4,1))
        with imports.database(self.output,[self.source,self.exports]) as conn:
            facts=[dict(r) for r in conn.execute("SELECT * FROM metadata_facts WHERE attribute='date'")]
        by_file={json.loads(r['source_span']).get('sidecar'):json.loads(r['value_json'])['value'] for r in facts if 'sidecar' in r['source_span']}
        self.assertEqual(by_file['b.jpg(1).json'],'2020-09-13T12:28:20+00:00')
        self.assertEqual(sorted(by_file),sorted(sidecars))
        self.assertNotIn('DO NOT IMPORT',json.dumps(facts))
        self.assertEqual(metadata.sidecar_names('IMG_1.jpg(2).json','IMG_1.jpg'),{'IMG_1(2).jpg'})

    def test_changed_zip_does_not_replace_previously_published_metadata(self):
        self.index()
        first = metadata.refresh(self.imports,self.exports,self.output)
        self.zip.rename(self.root/'retained.zip')
        with zipfile.ZipFile(self.zip,'w') as z:
            z.writestr('Google Photos/a.jpg',b'changed image')
        with self.assertRaises(ValueError):
            metadata.refresh(self.imports,self.exports,self.output)
        with imports.database(self.output,[self.source,self.exports]) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM metadata_facts').fetchone()[0],first['facts'])


if __name__ == '__main__':
    unittest.main()
