"""Continuing imports resume without duplicates and preserve offline snapshots."""
import json
import unittest
from src import jobs
from src.library import Library
from tests import test_library


class JobTests(unittest.TestCase):
    def setUp(self):
        self.f=test_library.LibraryTests();self.f.setUp();self.viewer=self.f.build()
        self.config=self.f.f.root/'config.json';self.config.write_text(json.dumps({'sources':[str(self.f.f.source)]}))

    def cycle(self):return jobs.cycle(self.config,self.f.f.root,self.f.f.exports,seconds=2,limit=20)

    def test_new_source_appears_after_checkpoint_without_losing_owner_tags(self):
        v=self.viewer;photo=v.search(query='new.jpg')['items'][0]
        person=v.organize('person',{'person':'','name':'Synthetic Person'})['data']['person'];v.organize('add',{'person':person,'contents':[photo['content_hash']]})
        (self.f.f.source/'additional.jpg').write_bytes(b'additional synthetic bytes')
        result=self.cycle();self.assertEqual(result['state'],'complete')
        updated=v.snapshot();self.assertEqual(updated.search(query='additional.jpg')['total'],1)
        self.assertEqual(updated.search(person=person)['total'],1);self.assertEqual(len(v.items),3)
        again=self.cycle();self.assertEqual(again['imports']['verified_contents'],result['imports']['verified_contents'])
        self.assertEqual(v.snapshot().search(query='additional.jpg')['total'],1)
        self.assertTrue(jobs.status(self.f.f.root)['generation'])

    def test_offline_source_keeps_last_successful_generation(self):
        first=self.cycle();before=self.viewer.snapshot()
        self.f.f.source.rename(self.f.f.root/'offline-source')
        result=self.cycle();self.assertEqual(result['state'],'waiting_for_sources')
        self.assertEqual(jobs.status(self.f.f.root)['generation'],first['generation'])
        self.assertIs(self.viewer.snapshot(),before)

    def test_bounded_batch_publishes_partial_snapshot_then_finishes(self):
        # Establish a generation before adding more files than one batch can hash.
        self.cycle()
        for name in ('batch-one.jpg','batch-two.jpg'):
            (self.f.f.source/name).write_bytes(name.encode())
        result=jobs.cycle(self.config,self.f.f.root,self.f.f.exports,seconds=2,limit=1)
        self.assertEqual(result['state'],'partial')
        self.assertGreater(result['imports']['pending'],0)
        self.assertEqual(jobs.status(self.f.f.root)['generation'],result['generation'])
        partial=self.viewer.snapshot().search(query='batch-')['items']
        self.assertEqual(sum(bool(item['content_hash']) for item in partial),1)
        finished=self.cycle()
        self.assertEqual(finished['state'],'complete')
        self.assertEqual(finished['imports']['pending'],0)
        self.assertEqual(self.viewer.snapshot().search(query='batch-')['total'],2)
        again=self.cycle()
        self.assertEqual(again['imports']['verified_contents'],finished['imports']['verified_contents'])
