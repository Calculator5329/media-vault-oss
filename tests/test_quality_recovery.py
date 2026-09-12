"""Recovered quality uses distinct input identity and current-base consumers."""
from contextlib import closing
import json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
from src import quality,quality_recovery,highlights
from tests import test_recovered_previews,test_quality


class QualityRecoveryTests(unittest.TestCase):
    def fixture(self):
        f=test_recovered_previews.RecoveredPreviewTests();f.fixture();self.f=f;self.q=f.f.f.output;self.imports=f.f.f.f.db;self.backend=test_quality.Backend()
    def test_score_provenance_idempotence_and_consumers(self):
        self.fixture();f=self.f
        result=quality.index(self.imports,self.q,self.backend,limit=1)
        self.assertEqual(result['recovered'],1);self.assertEqual(result['indexed'],1);self.assertEqual(result['errors'],2)
        with patch.object(self.backend,'score',side_effect=AssertionError('Repeated')):self.assertEqual(quality.index(self.imports,self.q,self.backend)['processed_this_run'],0)
        detail=f.viewer.quality_details(f.key);self.assertEqual(detail['status'],'complete');self.assertNotEqual(detail['model'],self.backend.identity)
        span=json.loads(detail['fact']['source_span']);self.assertEqual(span['base_model'],self.backend.identity);self.assertEqual(span['artifact_sha256'],f.fact['artifact_sha256'])
        draft=highlights.select([f.viewer.by_id[f.key]],1,f.f.f.f.root/'absent-vision.db',style='quality',quality_database=self.q)
        self.assertEqual(draft['selection']['quality_candidates'],1)
        progress=f.viewer.progress()['quality'];self.assertEqual(progress['contents'],1);self.assertEqual(progress['errors'],2);self.assertEqual(progress['recovered'],1)
        with closing(sqlite3.connect(self.q)) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM quality_work WHERE status='error'").fetchone()[0],3)
            self.assertEqual(len(quality_recovery.facts(c,self.backend.identity)),1)
            self.assertEqual(quality_recovery.facts(c,'new-base'),{})
            # A later original-path success takes precedence over its variant.
            c.execute('INSERT INTO quality_facts SELECT content_hash,?,score,source_path,source_span,extractor,extractor_version,confidence,derived_at,tier FROM quality_facts',(self.backend.identity,));c.commit()
            self.assertEqual(quality_recovery.facts(c,self.backend.identity),{})
    def test_corrupt_input_never_reaches_model_and_is_terminal(self):
        self.fixture();Path(self.f.fact['artifact_path']).write_bytes(b'corrupt')
        with patch.object(self.backend,'score',side_effect=AssertionError('Unverified input')) as score:
            result=quality.index(self.imports,self.q,self.backend);score.assert_not_called()
            self.assertEqual(result['indexed'],0);self.assertEqual(result['remaining'],0)
            self.assertEqual(quality.index(self.imports,self.q,self.backend)['processed_this_run'],0)
    def test_interrupted_inference_is_retained_without_repeating(self):
        self.fixture()
        with patch.object(self.backend,'score',side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):quality.index(self.imports,self.q,self.backend)
        self.assertEqual(quality.index(self.imports,self.q,self.backend)['processed_this_run'],0)
        with closing(sqlite3.connect(self.q)) as c:self.assertEqual(c.execute('SELECT error FROM quality_recovery_work').fetchone()[0],'InterruptedAttempt')
    def test_nonfinite_score_not_published(self):
        self.fixture()
        with patch.object(self.backend,'score',return_value=float('nan')):self.assertEqual(quality.index(self.imports,self.q,self.backend)['indexed'],0)
        with closing(sqlite3.connect(self.q)) as c:self.assertEqual(c.execute('SELECT count(*) FROM quality_facts').fetchone()[0],0)


if __name__=='__main__':unittest.main()
