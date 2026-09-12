"""Recovered descriptions preserve provenance and object-color relationships."""
from contextlib import closing
import json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from src import descriptions,description_recovery
from tests import test_recovered_previews


class Backend:
    identity='synthetic-current-description'
    compatible_models=['synthetic-compatible-description']
    def describe(self,image):
        return {'caption':'A red car beside a green tree.','category':'photograph','objects':[{'name':'car','color':'red'},{'name':'tree','color':'green'}]}


class DescriptionRecoveryTests(unittest.TestCase):
    def fixture(self):
        f=test_recovered_previews.RecoveredPreviewTests();f.fixture();self.f=f;self.db=f.f.f.f.root/'descriptions.db';self.imports=f.f.f.f.db;self.backend=Backend()
        def read(row):raise OSError('synthetic original decode failure')
        with patch('src.description_recovery.run',return_value={'processed':0,'remaining':0}):descriptions.index(self.imports,self.db,self.backend,reader=read)
    def test_recovered_description_reaches_search_and_metadata_once(self):
        self.fixture();result=descriptions.index(self.imports,self.db,self.backend,limit=1)
        self.assertEqual(result['indexed'],1);self.assertEqual(result['recovered'],1)
        selected=self.f.viewer.descriptions();fact=selected[self.f.digest]
        self.assertEqual(fact['extractor_version'],'description-recovery-1')
        self.assertEqual(json.loads(fact['source_span'])['artifact_sha256'],self.f.fact['artifact_sha256'])
        self.assertTrue(descriptions.matches(fact['description'],'red car'))
        self.assertFalse(descriptions.matches(fact['description'],'green car'))
        self.assertEqual(self.f.viewer.described('red car')['items'][0]['content_hash'],self.f.digest)
        self.assertEqual(self.f.viewer.described('green car')['total'],0)
        self.assertEqual(self.f.viewer.metadata(self.f.key)['ai']['description'],fact['description'])
        with patch.object(self.backend,'describe',side_effect=AssertionError('Repeated')):
            self.assertEqual(descriptions.index(self.imports,self.db,self.backend)['processed_this_run'],0)
        with closing(sqlite3.connect(self.db)) as c:
            self.assertGreater(c.execute('SELECT count(*) FROM description_errors').fetchone()[0],0)
            self.assertEqual(description_recovery.facts(c,'other-base',self.backend.compatible_models),{})
    def test_compatible_original_precedes_recovered_and_blocks_retry(self):
        self.fixture();descriptions.index(self.imports,self.db,self.backend)
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('INSERT INTO description_facts SELECT content_hash,?,value_json,source_path,source_span,extractor,extractor_version,confidence,derived_at,tier FROM description_facts',(self.backend.compatible_models[0],));c.commit()
            self.assertEqual(descriptions.facts(c)[self.f.digest]['model'],self.backend.compatible_models[0])
            self.assertEqual(description_recovery.facts(c,self.backend.identity,[self.backend.identity,*self.backend.compatible_models]),{})
        with patch.object(self.backend,'describe',side_effect=AssertionError('Repeated')):
            self.assertEqual(descriptions.index(self.imports,self.db,self.backend)['processed_this_run'],0)
    def test_invalid_description_corruption_and_interruption_are_terminal(self):
        self.fixture()
        with patch.object(self.backend,'describe',return_value={'caption':'invalid fields'}):
            self.assertEqual(descriptions.index(self.imports,self.db,self.backend)['indexed'],0)
        self.assertEqual(descriptions.index(self.imports,self.db,self.backend)['processed_this_run'],0)
        self.fixture();Path(self.f.fact['artifact_path']).write_bytes(b'bad')
        with patch.object(self.backend,'describe') as describe:
            self.assertEqual(descriptions.index(self.imports,self.db,self.backend)['indexed'],0);describe.assert_not_called()
        self.fixture()
        with patch.object(self.backend,'describe',side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):descriptions.index(self.imports,self.db,self.backend)
        self.assertEqual(descriptions.index(self.imports,self.db,self.backend)['processed_this_run'],0)
    def test_coverage_uses_search_models_and_retains_historical_failures(self):
        self.fixture();descriptions.index(self.imports,self.db,self.backend)
        progress=self.f.viewer.progress()['descriptions']
        self.assertEqual(progress['contents'],len(self.f.viewer.descriptions()))
        self.assertEqual(progress['recovered'],1)
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("UPDATE settings SET value=? WHERE key='descriptions_current'",('new-current',))
            c.execute("UPDATE settings SET value=? WHERE key='descriptions_models'",(json.dumps(['new-current']),))
            c.execute('INSERT INTO description_errors VALUES(?,?,?,?)',(self.f.digest,'new-current','ValueError','synthetic-time'));c.commit()
            self.assertEqual(descriptions.facts(c),{})
        progress=self.f.viewer.progress()['descriptions']
        self.assertEqual(progress['contents'],0);self.assertEqual(progress['errors'],1)
        self.assertEqual(progress['recovered'],0);self.assertGreater(progress['retained_error_records'],progress['errors'])
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("UPDATE settings SET value='[]' WHERE key='descriptions_models'");c.commit()
        self.assertEqual(self.f.viewer.progress()['descriptions'],{'available':False})

    def test_no_accepted_models_clears_cached_descriptions(self):
        self.fixture();descriptions.index(self.imports,self.db,self.backend)
        self.assertTrue(self.f.viewer.descriptions())
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("UPDATE settings SET value='[]' WHERE key='descriptions_models'");c.commit()
        self.f.viewer.description_checked=0
        self.assertEqual(self.f.viewer.descriptions(),{})


if __name__=='__main__':unittest.main()
