"""Recovered OCR participates in current-model text search without losing errors."""
from contextlib import closing
import json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
from src import ocr,ocr_recovery
from tests import test_recovered_previews


class Backend:
    identity='synthetic-current-ocr'
    def extract(self,image):
        return {'text':'recovery-token','words':[{'text':'recovery-token','score':.9,'box':[0,0,1,1]}],'language':'eng','minimum_search_score':.45}


class OCRRecoveryTests(unittest.TestCase):
    def fixture(self):
        f=test_recovered_previews.RecoveredPreviewTests();f.fixture();self.f=f;self.db=f.f.f.f.root/'ocr.db';self.imports=f.f.f.f.db;self.backend=Backend()
        def fail(row):raise OSError('synthetic original read failure')
        ocr.index(self.imports,self.db,self.backend,limit=3,reader=fail)
    def test_current_search_details_coverage_and_retained_errors(self):
        self.fixture();r=ocr.index(self.imports,self.db,self.backend,limit=1);self.assertEqual(r['recovered'],1);self.assertEqual(r['errors'],2)
        self.assertEqual(self.f.viewer.search(query='recovery-token')['total'],1)
        d=self.f.viewer.ocr_details(self.f.digest);self.assertNotEqual(d['model'],self.backend.identity);self.assertEqual(json.loads(d['source_span'])['artifact_sha256'],self.f.fact['artifact_sha256'])
        p=self.f.viewer.progress()['ocr'];self.assertEqual((p['contents'],p['with_text'],p['errors'],p['recovered']),(1,1,2,1))
        with patch.object(self.backend,'extract',side_effect=AssertionError('Repeated')):self.assertEqual(ocr.index(self.imports,self.db,self.backend)['processed_this_run'],0)
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM ocr_errors').fetchone()[0],3)
            self.assertEqual(ocr_recovery.facts(c,'different-model'),{})
            c.execute('INSERT INTO ocr_facts SELECT content_hash,?,text,words_json,source_path,source_span,extractor,extractor_version,confidence,derived_at,tier FROM ocr_facts',(self.backend.identity,));c.commit()
            self.assertEqual(ocr_recovery.facts(c,self.backend.identity),{})
    def test_empty_recognition_is_complete_not_read_failure(self):
        self.fixture()
        with patch.object(self.backend,'extract',return_value={'text':'','words':[],'language':'eng'}):r=ocr.index(self.imports,self.db,self.backend)
        self.assertEqual(r['recovered'],1);self.assertEqual(self.f.viewer.progress()['ocr']['with_text'],0)
        self.assertEqual(self.f.viewer.ocr_details(self.f.digest)['text'],'')
    def test_corrupt_artifact_and_interruption_are_terminal(self):
        self.fixture();Path(self.f.fact['artifact_path']).write_bytes(b'bad')
        with patch.object(self.backend,'extract') as extract:
            self.assertEqual(ocr.index(self.imports,self.db,self.backend)['indexed'],0);extract.assert_not_called()
            self.assertEqual(ocr.index(self.imports,self.db,self.backend)['processed_this_run'],0)
        self.fixture()
        with patch.object(self.backend,'extract',side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):ocr.index(self.imports,self.db,self.backend)
        self.assertEqual(ocr.index(self.imports,self.db,self.backend)['processed_this_run'],0)
        with closing(sqlite3.connect(self.db)) as c:self.assertEqual(c.execute('SELECT error FROM ocr_recovery_work').fetchone()[0],'InterruptedAttempt')


if __name__=='__main__':unittest.main()
