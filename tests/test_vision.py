"""Embedding persistence/retrieval contract; no model download or private image."""
import math
import unittest

from tests import test_imports
from src import imports, vision
from src.kit import validate_row


class FakeEncoder:
    identity='synthetic-model-1'
    def image(self,value):
        return [1,0] if value=='first' else [0,1]
    def text(self,value):
        return [1,0]


class VisionTests(unittest.TestCase):
    def setUp(self):
        fixture=test_imports.ImportTests();fixture.setUp()
        self.fixture=fixture
        self.output=fixture.root/'vision.db'
        with fixture.connect() as conn:
            imports.inventory(conn,fixture.catalog,fixture.exports)
            imports.hash_pending(conn)
            self.first=conn.execute("SELECT content_hash FROM occurrences WHERE member='' LIMIT 1").fetchone()[0]
        self.encoder=FakeEncoder()

    def reader(self,row):
        return 'first' if row['content_hash']==self.first else 'second'

    def test_resume_deduplicates_and_search_uses_same_model(self):
        f=self.fixture
        result=vision.index(f.db,self.output,self.encoder,reader=self.reader,limit=1)
        self.assertEqual(result['indexed'],1)
        result=vision.index(f.db,self.output,self.encoder,reader=self.reader)
        self.assertEqual(result['indexed'],2)
        self.assertEqual(vision.index(f.db,self.output,self.encoder,reader=self.reader)['processed_this_run'],0)
        found=vision.search(self.output,self.encoder,'first')
        self.assertEqual(found['results'][0]['content_hash'],self.first)
        self.assertAlmostEqual(found['results'][0]['similarity'],1)
        filtered=vision.search(self.output,self.encoder,'first',allowed=set())
        self.assertEqual(filtered['indexed_contents'],2)
        self.assertEqual(filtered['eligible_contents'],0)
        self.assertEqual(filtered['results'],[])
        self.encoder.identity='another-model'
        self.assertEqual(vision.search(self.output,self.encoder,'first')['indexed_contents'],0)

    def test_cached_matrix_refreshes_and_matches_reference_ranking(self):
        f=self.fixture
        vision.index(f.db,self.output,self.encoder,reader=self.reader,limit=1)
        index=vision.SearchIndex(self.output,self.encoder,refresh_after=0)
        self.assertEqual(index.search('first')['indexed_contents'],1)
        vision.index(f.db,self.output,self.encoder,reader=self.reader)
        actual=index.search('first');expected=vision.search(self.output,self.encoder,'first')
        self.assertEqual(actual['results'],expected['results'])
        self.assertEqual(actual['indexed_contents'],2)
        self.assertEqual(index.search('first',allowed=set())['results'],[])

    def test_active_indexing_does_not_reload_every_warm_query(self):
        import importlib.util
        if importlib.util.find_spec('numpy') is None:self.skipTest('NumPy runtime required')
        f=self.fixture
        vision.index(f.db,self.output,self.encoder,reader=self.reader,limit=1)
        index=vision.SearchIndex(self.output,self.encoder)
        self.assertEqual(index.search('first')['indexed_contents'],1)
        vision.index(f.db,self.output,self.encoder,reader=self.reader)
        self.assertEqual(index.search('second')['indexed_contents'],1)
        index.checked_at=0
        self.assertEqual(index.search('second')['indexed_contents'],2)

    def test_failed_decode_is_recorded_and_not_retried_implicitly(self):
        def unreadable(row):
            raise ValueError('synthetic invalid image')
        f=self.fixture
        result=vision.index(f.db,self.output,self.encoder,reader=unreadable)
        self.assertEqual(result['errors'],2)
        self.assertEqual(result['indexed'],0)
        self.assertEqual(vision.index(f.db,self.output,self.encoder,reader=self.reader)['indexed'],0)

    def test_provenance_and_invalid_vectors(self):
        f=self.fixture
        vision.index(f.db,self.output,self.encoder,reader=self.reader)
        with imports.database(self.output,[f.source,f.exports]) as conn:
            for row in conn.execute('SELECT * FROM visual_facts'):
                validate_row('visual_facts',dict(row))
        for values in ([],[0,0],[math.nan,0],[math.inf,1]):
            with self.assertRaises(ValueError):vision.unit(values)
        with self.assertRaises(ValueError):vision.search(self.output,self.encoder,'')


if __name__=='__main__':
    unittest.main()
