"""Only a drained unchanged stage may reuse its last completed checkpoint."""
from contextlib import closing
from pathlib import Path
import sqlite3
import unittest
from src.enrichment import Supervisor
from tests import test_enrichment


class EnrichmentIdleTests(unittest.TestCase):
    def setUp(self):
        self.f=test_enrichment.EnrichmentTests();self.f.setUp()
        (self.f.root/'src').mkdir();self.code=self.f.root/'src/worker.py';self.code.write_text('VERSION=1')
        self.db=self.f.directory/'imports.db'
        with closing(sqlite3.connect(self.db,isolation_level=None)) as c:
            c.execute('CREATE TABLE occurrences(id TEXT,content_hash TEXT,source_size INT,mtime_ns INT,kind TEXT,metadata TEXT,present INT)')
            c.execute("INSERT INTO occurrences VALUES('one','hash-one',3,1,'photo','{}',1)")
        self.calls=[];self.result={'state':'complete','result':{'remaining':0,'processed_this_run':1,'indexed':1,'errors':0}}
        def runner(args,*unused):
            self.calls.append(args)
            Path(args[args.index('--database')+1]).write_bytes(b'synthetic checkpoint')
            return self.result
        self.supervisor=Supervisor(self.f.root,self.f.directory,self.f.resources,[self.f.source],runner=runner)

    def test_drained_stage_skips_model_launch_but_equal_count_replacement_wakes_it(self):
        first=self.supervisor.batch('descriptions')
        idle=self.supervisor.batch('descriptions')
        self.assertEqual(idle['state'],'idle');self.assertEqual(len(self.calls),1)
        self.assertEqual(idle['last_completed_at'],first['at'])
        self.assertEqual(idle['result']['processed_this_run'],0)
        with closing(sqlite3.connect(self.db,isolation_level=None)) as c:
            c.execute("UPDATE occurrences SET content_hash='hash-two'")
        self.assertEqual(self.supervisor.batch('descriptions')['state'],'complete')
        self.assertEqual(len(self.calls),2)

    def test_code_checkpoint_and_model_receipt_changes_wake_stage(self):
        self.supervisor.batch('vision');self.assertEqual(self.supervisor.batch('vision')['state'],'idle')
        self.code.write_text('VERSION=2')
        self.assertEqual(self.supervisor.batch('vision')['state'],'complete')
        (self.f.directory/'vision.db').write_bytes(b'manually changed checkpoint')
        self.assertEqual(self.supervisor.batch('vision')['state'],'complete')
        (self.f.root/'acquisition.json').write_text('{"revision":2}')
        self.assertEqual(self.supervisor.batch('vision')['state'],'complete')
        self.assertEqual(len(self.calls),4)

    def test_changed_model_file_without_receipt_edit_wakes_stage(self):
        model=self.f.root/'weights.bin';model.write_bytes(b'synthetic model')
        (self.f.root/'acquisition.json').write_text('{"files":[{"file":"weights.bin"}]}')
        self.supervisor.batch('vision');self.assertEqual(self.supervisor.batch('vision')['state'],'idle')
        model.write_bytes(b'different synthetic model bytes')
        self.assertEqual(self.supervisor.batch('vision')['state'],'complete')
        self.assertEqual(len(self.calls),2)

    def test_new_video_samples_wake_scene_stage(self):
        self.supervisor.batch('scenes');self.assertEqual(self.supervisor.batch('scenes')['state'],'idle')
        (self.f.directory/'frames.db').write_bytes(b'new synthetic frame checkpoint')
        self.assertEqual(self.supervisor.batch('scenes')['state'],'complete')
        self.assertEqual(len(self.calls),2)

    def test_added_registered_folder_is_checked_even_without_service_restart(self):
        with closing(sqlite3.connect(self.db,isolation_level=None)) as c:
            c.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
            import json
            c.execute('INSERT INTO settings VALUES(?,?)',('sources',json.dumps([str(self.f.source),str(self.f.root/'offline-added-folder')])))
        self.assertEqual(self.supervisor.batch('vision')['state'],'waiting_for_sources')
        self.assertEqual(self.calls,[])

    def test_errors_and_partial_work_never_earn_idle_reuse(self):
        self.result={'state':'complete','result':{'remaining':2}}
        self.supervisor.batch('ocr');self.supervisor.batch('ocr')
        self.result={'state':'error','error':'synthetic failure'}
        self.supervisor.batch('ocr');self.supervisor.batch('ocr')
        self.assertEqual(len(self.calls),4)
