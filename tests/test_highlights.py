"""Draft variety preserves time coverage, missing-data fallback and filters."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from src.highlights import select
from tests import test_albums as album_fixtures


class HighlightTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp())
        self.db=self.root/'vision.db'
        self.items=[{'id':str(i),'content_hash':f'{i:064x}'} for i in range(6)]
        self.vectors=[[1,0],[1,0],[1,0],[0,1],[0,1],[1,0]]
        with closing(sqlite3.connect(self.db,isolation_level=None)) as c:
            c.execute('CREATE TABLE visual_facts(content_hash TEXT,model TEXT,vector_json TEXT)')
            c.executemany('INSERT INTO visual_facts VALUES(?,?,?)',[(i['content_hash'],'synthetic',json.dumps(v)) for i,v in zip(self.items,self.vectors)])

    def test_variety_changes_repeated_middle_photo_and_keeps_time_endpoints(self):
        result=select(self.items,3,self.db,'synthetic')
        self.assertEqual([r['id'] for r in result['items']],['0','3','5'])
        self.assertEqual(result['selection']['changed_from_timeline'],1)
        self.assertEqual(result['selection']['indexed_candidates'],6)
        self.assertEqual(select(self.items,3,self.db,'synthetic'),result)

    def test_missing_or_other_model_vectors_keep_timeline_choice(self):
        with closing(sqlite3.connect(self.db,isolation_level=None)) as c:
            c.execute('UPDATE visual_facts SET model=? WHERE content_hash=?',('old',self.items[3]['content_hash']))
        result=select(self.items,3,self.db,'synthetic')
        self.assertEqual([r['id'] for r in result['items']],['0','2','5'])
        self.assertEqual(result['selection']['indexed_candidates'],5)
        no_model=select(self.items,3,self.db,'unavailable')
        self.assertEqual(no_model['selection']['method'],'timeline-1')
        self.assertEqual(no_model['selection']['indexed_candidates'],0)
        self.assertEqual(select([],3,self.db)['items'],[])

    def test_pool_and_selection_are_bounded_without_creating_missing_database(self):
        missing=self.root/'absent.db'
        many=[{'id':str(i),'content_hash':f'{i:064x}'} for i in range(10000)]
        result=select(many,30,missing,'synthetic')
        self.assertEqual(len(result['items']),30)
        self.assertEqual(result['selection']['candidates'],200)
        self.assertEqual(result['items'][0]['id'],'0')
        self.assertEqual(result['items'][-1]['id'],'9999')
        self.assertFalse(missing.exists())

    def test_library_filters_before_draft_and_does_not_save_automatically(self):
        f=album_fixtures.AlbumTests();f.setUp();v=f.v
        with patch('src.highlights.select',wraps=select) as chooser:
            result=v.highlights(year='2026')
            self.assertEqual(result['total'],1)
            self.assertTrue(all(i['day'].startswith('2026') for i in chooser.call_args.args[0]))
        self.assertIn('selection',result)
        self.assertEqual(v.albums()['albums'],[])

    def quality_store(self):
        path=self.root/'quality.db'
        with closing(sqlite3.connect(path,isolation_level=None)) as c:
            c.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
            c.execute("INSERT INTO settings VALUES('quality_current','quality-model')")
            c.execute('CREATE TABLE quality_facts(content_hash TEXT,model TEXT,score REAL)')
            c.executemany('INSERT INTO quality_facts VALUES(?,?,?)',[(i['content_hash'],'quality-model',90 if n==2 else 20) for n,i in enumerate(self.items)])
        return path

    def test_quality_draft_preserves_time_and_default_variety(self):
        db=self.quality_store()
        result=select(self.items,3,self.db,'synthetic',style='quality',quality_database=db)
        self.assertEqual([r['id'] for r in result['items']],['0','2','5'])
        self.assertEqual(result['selection']['quality_spans'],1)
        self.assertEqual(result['selection']['quality_candidates'],6)
        self.assertEqual(result['selection']['method'],'timeline-quality-1')
        self.assertEqual([r['id'] for r in select(self.items,3,self.db,'synthetic',quality_database=db)['items']],['0','3','5'])

    def test_missing_or_old_quality_keeps_unscored_photo_eligible(self):
        db=self.quality_store()
        with closing(sqlite3.connect(db,isolation_level=None)) as c:
            c.execute("UPDATE quality_facts SET model='old' WHERE content_hash=?",(self.items[3]['content_hash'],))
        result=select(self.items,3,self.db,'synthetic',style='quality',quality_database=db)
        self.assertEqual([r['id'] for r in result['items']],['0','3','5'])
        self.assertEqual(result['selection']['quality_spans'],0)
        self.assertEqual(result['selection']['quality_candidates'],5)
        missing=self.root/'missing-quality.db'
        fallback=select(self.items,3,self.db,'synthetic',style='quality',quality_database=missing)
        self.assertEqual(fallback['selection']['quality_status'],'unavailable');self.assertFalse(missing.exists())
        with self.assertRaises(ValueError):select(self.items,3,self.db,style='best')

    def test_library_quality_mode_keeps_filters_and_explicit_saving(self):
        f=album_fixtures.AlbumTests();f.setUp();v=f.v
        result=v.highlights(year='2026',style='quality')
        self.assertEqual(result['total'],1)
        self.assertIn('Technical clarity coverage',result['coverage'])
        self.assertIn('not available yet',result['coverage'])
        self.assertEqual(v.albums()['albums'],[])
