"""Recovered vectors share current encoder space and invalidate cached matrices."""
from contextlib import closing
import json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from src import vision,visual_recovery,highlights
from tests import test_recovered_previews


class Encoder:
    identity='synthetic-current-encoder'
    def image(self,image):return [1,0]
    def text(self,text):return [1,0]


class VisualRecoveryTests(unittest.TestCase):
    def fixture(self):
        f=test_recovered_previews.RecoveredPreviewTests();f.fixture();self.f=f;self.db=f.f.f.f.root/'vision.db';self.imports=f.f.f.f.db;self.encoder=Encoder();self.other=next(h for h in f.viewer.by_content if h!=f.digest)
        def read(row):
            if row['content_hash']==self.other:return Image.new('RGB',(40,30),'green')
            raise OSError('synthetic original decode failure')
        with patch('src.visual_recovery.run',return_value={'processed':0,'remaining':0}):vision.index(self.imports,self.db,self.encoder,reader=read)
    def test_cached_and_reference_search_refresh_after_variant_only_change(self):
        self.fixture();cache=vision.SearchIndex(self.db,self.encoder,refresh_after=0)
        self.assertEqual(cache.search('red car')['indexed_contents'],1)
        result=vision.index(self.imports,self.db,self.encoder,limit=1);self.assertEqual(result['indexed'],2);self.assertEqual(result['recovered'],1)
        cached=cache.search('red car');reference=vision.search(self.db,self.encoder,'red car')
        self.assertEqual(cached['results'],reference['results']);self.assertEqual(cached['indexed_contents'],2)
        self.assertEqual(cache.search('red car',allowed={self.f.digest})['results'][0]['content_hash'],self.f.digest)
        self.assertEqual(cache.search('red car',allowed=set())['results'],[])
        with patch.object(self.encoder,'image',side_effect=AssertionError('Repeated')):self.assertEqual(vision.index(self.imports,self.db,self.encoder)['processed_this_run'],0)
        draft=highlights.select([self.f.viewer.by_id[self.f.key]],1,self.db,self.encoder.identity)
        self.assertEqual(draft['selection']['indexed_candidates'],1)
        with closing(sqlite3.connect(self.db)) as c:
            fact=visual_recovery.facts(c,self.encoder.identity)[self.f.digest]
            self.assertEqual(json.loads(fact['source_span'])['artifact_sha256'],self.f.fact['artifact_sha256'])
            self.assertEqual(c.execute('SELECT count(*) FROM visual_errors').fetchone()[0],2)
            self.assertEqual(visual_recovery.facts(c,'other-model'),{})
            c.execute('INSERT INTO visual_facts SELECT content_hash,?,vector_json,source_path,source_span,extractor,extractor_version,confidence,derived_at,tier FROM visual_facts WHERE model=?',(self.encoder.identity,fact['model']));c.commit()
            self.assertEqual(visual_recovery.facts(c,self.encoder.identity),{})
        self.assertEqual(cache.search('red car')['indexed_contents'],2)
    def test_wrong_dimension_or_nonfinite_vectors_never_publish(self):
        for vector in ([1,0,0],[float('nan'),0]):
            self.fixture()
            with patch.object(self.encoder,'image',return_value=vector):self.assertEqual(vision.index(self.imports,self.db,self.encoder)['indexed'],1)
            with closing(sqlite3.connect(self.db)) as c:self.assertEqual(visual_recovery.facts(c,self.encoder.identity),{})
    def test_corruption_and_interruption_are_terminal(self):
        self.fixture();Path(self.f.fact['artifact_path']).write_bytes(b'bad')
        with patch.object(self.encoder,'image') as image,patch.object(self.encoder,'text') as text:
            self.assertEqual(vision.index(self.imports,self.db,self.encoder)['indexed'],1);image.assert_not_called();text.assert_not_called()
            self.assertEqual(vision.index(self.imports,self.db,self.encoder)['processed_this_run'],0)
        self.fixture()
        with patch.object(self.encoder,'image',side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):vision.index(self.imports,self.db,self.encoder)
        self.assertEqual(vision.index(self.imports,self.db,self.encoder)['processed_this_run'],0)
    def test_cached_model_switch_with_equal_counts_and_timestamps(self):
        self.fixture();cache=vision.SearchIndex(self.db,self.encoder,refresh_after=0)
        self.assertEqual(cache.search('red car')['results'][0]['similarity'],1.)
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('INSERT INTO visual_facts SELECT content_hash,?, ?,source_path,source_span,extractor,extractor_version,confidence,derived_at,tier FROM visual_facts',('new-space',json.dumps([0,1])));c.commit()
        self.encoder.identity='new-space'
        self.assertEqual(cache.search('red car')['results'][0]['similarity'],0.)

    def test_old_model_vectors_are_not_retrievable(self):
        self.fixture();vision.index(self.imports,self.db,self.encoder)
        self.encoder.identity='different-encoder'
        self.assertEqual(vision.search(self.db,self.encoder,'red car')['results'],[])
        self.assertEqual(vision.SearchIndex(self.db,self.encoder).search('red car')['results'],[])


if __name__=='__main__':unittest.main()
