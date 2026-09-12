"""Recovered fingerprints compare directly with current base inputs, never merge."""
from contextlib import closing
import json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from src import fingerprints,fingerprint_recovery
from tests import test_recovered_previews


class Backend:
    identity='synthetic-fingerprint-base'
    def describe(self,image):return {'gray_hash':'0123456789abcdef','gray_range':100,'color_json':json.dumps([.2]*48),'aspect_ratio':4/3}


class FingerprintRecoveryTests(unittest.TestCase):
    def fixture(self):
        f=test_recovered_previews.RecoveredPreviewTests();f.fixture();self.f=f;self.db=f.f.f.f.root/'fingerprints.db';self.imports=f.f.f.f.db;self.backend=Backend()
        self.other=next(h for h in f.viewer.by_content if h!=f.digest)
        def read(row):
            if row['content_hash']==self.other:return Image.new('RGB',(40,30),'green')
            raise OSError('synthetic original decode failure')
        fingerprints.index(self.imports,self.db,self.backend,limit=3,reader=read)
    def test_both_comparison_directions_and_coverage(self):
        self.fixture();r=fingerprints.index(self.imports,self.db,self.backend,limit=1)
        self.assertEqual((r['indexed'],r['errors'],r['recovered']),(2,1,1))
        v=self.f.viewer;other_key=v.by_content[self.other]['id']
        self.assertEqual([i['content_hash'] for i in v.similar_versions(self.f.key)['items']],[self.other])
        self.assertEqual([i['content_hash'] for i in v.similar_versions(other_key)['items']],[self.f.digest])
        p=v.progress()['fingerprints'];self.assertEqual((p['contents'],p['errors'],p['recovered']),(2,1,1))
        with patch.object(self.backend,'describe',side_effect=AssertionError('Repeated')):self.assertEqual(fingerprints.index(self.imports,self.db,self.backend)['processed_this_run'],0)
        with closing(sqlite3.connect(self.db)) as c:
            recovered=fingerprint_recovery.facts(c,self.backend.identity);fact=recovered[self.f.digest]
            self.assertEqual(json.loads(fact['source_span'])['artifact_sha256'],self.f.fact['artifact_sha256'])
            self.assertNotEqual(fact['model'],self.backend.identity);self.assertEqual(fingerprint_recovery.facts(c,'stale-base'),{})
            self.assertEqual(c.execute("SELECT count(*) FROM fingerprints_work WHERE status='error'").fetchone()[0],2)
            c.execute('INSERT INTO fingerprints_facts SELECT content_hash,?,gray_hash,gray_range,color_json,aspect_ratio,source_path,source_span,extractor,extractor_version,confidence,derived_at,tier FROM fingerprints_facts WHERE model=?',(self.backend.identity,fact['model']));c.commit()
            self.assertEqual(fingerprint_recovery.facts(c,self.backend.identity),{})
    def test_color_threshold_and_flat_exclusion_unchanged(self):
        self.fixture();fingerprints.index(self.imports,self.db,self.backend)
        with closing(sqlite3.connect(self.db)) as c:c.execute('UPDATE fingerprints_facts SET color_json=? WHERE model=?',(json.dumps([.9]*48),self.backend.identity));c.commit()
        self.assertEqual(self.f.viewer.similar_versions(self.f.key)['items'],[])
        with closing(sqlite3.connect(self.db)) as c:c.execute('UPDATE fingerprints_facts SET gray_range=0 WHERE content_hash=?',(self.f.digest,));c.commit()
        self.assertEqual(self.f.viewer.similar_versions(self.f.key)['state'],'flat')
        self.assertEqual(self.f.viewer.progress()['fingerprints']['flat'],1)
    def test_corrupt_input_and_interruption_are_terminal(self):
        self.fixture();Path(self.f.fact['artifact_path']).write_bytes(b'bad')
        with patch.object(self.backend,'describe') as describe:
            self.assertEqual(fingerprints.index(self.imports,self.db,self.backend)['indexed'],1);describe.assert_not_called()
            self.assertEqual(fingerprints.index(self.imports,self.db,self.backend)['processed_this_run'],0)
        self.fixture()
        with patch.object(self.backend,'describe',side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):fingerprints.index(self.imports,self.db,self.backend)
        self.assertEqual(fingerprints.index(self.imports,self.db,self.backend)['processed_this_run'],0)
    def test_invalid_features_do_not_publish(self):
        self.fixture();value=self.backend.describe(None);value['color_json']=json.dumps([float('nan')]*48)
        with patch.object(self.backend,'describe',return_value=value):self.assertEqual(fingerprints.index(self.imports,self.db,self.backend)['indexed'],1)
        with closing(sqlite3.connect(self.db)) as c:self.assertEqual(fingerprint_recovery.facts(c,self.backend.identity),{})


if __name__=='__main__':unittest.main()
