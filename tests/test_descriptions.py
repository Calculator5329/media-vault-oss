"""Color belongs to the object; generated metadata stays separate from sources."""
import json
import unittest
from src import descriptions
from tests import test_library


def scene(color='red'):
    return {'caption':'A '+color+' car near a red tree.','category':'illustration','objects':[{'name':'car','color':color},{'name':'tree','color':'red'}]}


class DescriptionTests(unittest.TestCase):
    def test_red_background_does_not_turn_blue_car_red(self):
        self.assertTrue(descriptions.matches(scene(),'red car'))
        self.assertFalse(descriptions.matches(scene('blue'),'red car'))
        self.assertTrue(descriptions.matches(scene('blue'),'blue car'))
        self.assertEqual(descriptions.parse('```json\n'+json.dumps(scene())+'\n```'),scene())
        with self.assertRaises(ValueError):descriptions.parse(json.dumps({'caption':'hello','category':'secret trait','objects':[]}))
        repeated={**scene(),'objects':[{'name':'Crutch','color':'black'}]*9+[{'name':'crutch','color':'grey'}]}
        self.assertEqual(descriptions.parse(json.dumps(repeated))['objects'],[{'name':'crutch','color':'black'},{'name':'crutch','color':'grey'}])

    def test_checkpoint_search_category_and_provenance(self):
        f=test_library.LibraryTests();f.setUp();v=f.build();photo=v.search(query='new.jpg')['items'][0]
        class Backend:
            identity='synthetic-description'
            def describe(self,image):return scene()
        db=f.f.root/'descriptions.db'
        first=descriptions.index(f.f.db,db,Backend(),limit=1,priority=[photo['content_hash']],reader=lambda row:'synthetic')
        self.assertEqual(first['indexed'],1);self.assertEqual(first['remaining'],2)
        self.assertEqual(v.described('red car')['total'],1);self.assertEqual(v.described('red car',category='photograph')['total'],0)
        fact=v.metadata(photo['id'])['ai'];self.assertEqual(fact['extractor'],'qwen-image-description')
        self.assertIn('source_span',fact);self.assertEqual(fact['description'],scene())
        self.assertFalse(any(f['attribute']=='description' for f in v.metadata(photo['id'])['facts']))
        descriptions.index(f.f.db,db,Backend(),reader=lambda row:'synthetic')
        resumed=descriptions.index(f.f.db,db,Backend(),reader=lambda row:self.fail('Completed content reprocessed'))
        self.assertEqual(resumed['indexed'],3)

    def test_failed_output_is_retained_as_error_without_false_facts(self):
        f=test_library.LibraryTests();f.setUp();f.build()
        class Backend:
            identity='bad-output'
            def describe(self,image):return {'invalid':'output'}
        result=descriptions.index(f.f.db,f.f.root/'descriptions.db',Backend(),limit=1,reader=lambda row:'synthetic')
        self.assertEqual(result['indexed'],0);self.assertEqual(result['errors'],1)

    def test_compatible_configuration_recovers_errors_and_retains_prior_facts(self):
        import sqlite3
        f=test_library.LibraryTests();f.setUp();v=f.build();db=f.f.root/'descriptions.db'
        class Older:
            identity='synthetic-older'
            def describe(self,image):return scene()
        descriptions.index(f.f.db,db,Older(),limit=1,reader=lambda row:'synthetic')
        class Broken:
            identity='synthetic-broken'
            compatible_models=['synthetic-older']
            def describe(self,image):raise json.JSONDecodeError('truncated','',0)
        failed=descriptions.index(f.f.db,db,Broken(),limit=1,reader=lambda row:'synthetic')
        self.assertEqual(failed['indexed'],1);self.assertEqual(failed['errors'],1)
        class Recovered:
            identity='synthetic-recovered'
            compatible_models=['synthetic-older','synthetic-broken']
            def describe(self,image):return scene('blue')
        result=descriptions.index(f.f.db,db,Recovered(),limit=1,reader=lambda row:'synthetic')
        self.assertEqual(result['indexed'],2);self.assertEqual(result['processed_this_run'],1)
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM description_errors').fetchone()[0],1)
            failed_key=conn.execute('SELECT content_hash FROM description_errors').fetchone()[0]
            self.assertEqual(conn.execute('SELECT count(*) FROM description_facts WHERE content_hash=?',(failed_key,)).fetchone()[0],1)
        values=v.descriptions();self.assertEqual(len(values),2)
        self.assertEqual({f['model'] for f in values.values()},{'synthetic-older','synthetic-recovered'})
        self.assertEqual(v.progress()['descriptions']['errors'],0)
        self.assertEqual(v.progress()['descriptions']['retained_error_records'],1)
