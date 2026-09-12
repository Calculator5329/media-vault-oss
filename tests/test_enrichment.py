"""Coordinator ownership, source waiting, restart and private status boundaries."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from src.enrichment import Supervisor,exclusive,status,command,run_worker,RESOURCES
from tests.scratch import scratch

class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.root=scratch();self.source=self.root/'source';self.source.mkdir()
        self.directory=self.root/'catalog';self.directory.mkdir();(self.directory/'imports.db').touch()
        self.resources={k:str(Path(sys.executable) if k.endswith('python') else self.root) for k in RESOURCES}

    def test_one_supervisor_and_restart_reuses_worker_checkpoints(self):
        calls=[]
        def runner(args,*rest):calls.append(args);return {'state':'complete','result':{'remaining':0,'processed_this_run':0}}
        supervisor=Supervisor(self.root,self.directory,self.resources,[self.source],runner=runner)
        with exclusive(self.directory):
            with self.assertRaises(BlockingIOError):supervisor.run(once=True)
        supervisor.run(once=True)
        self.assertEqual(len(calls),8)
        self.assertEqual(set(status(self.directory)['stages']),{'faces','ocr','similar','vision','scenes','frames','descriptions','transcripts'})
        Supervisor(self.root,self.directory,self.resources,[self.source],runner=runner).run(once=True)
        self.assertEqual(len(calls),16)
        self.assertTrue(all(str(self.directory/'imports.db') in c for c in calls))
        self.assertEqual(len((self.directory/'enrichment-events.jsonl').read_text().splitlines()),32)

    def test_missing_source_waits_without_launch_or_source_write(self):
        def forbidden(*args):self.fail('Worker ran against missing source')
        supervisor=Supervisor(self.root,self.directory,self.resources,[self.root/'absent'],runner=forbidden)
        supervisor.run(once=True)
        self.assertTrue(all(e['state']=='waiting_for_sources' for e in status(self.directory)['stages'].values()))
        self.assertFalse((self.root/'absent').exists())
        with self.assertRaises(ValueError):Supervisor(self.root,self.source,self.resources,[self.source])
        with self.assertRaises(ValueError):Supervisor(self.root,'/run/media/derived',self.resources,[self.source])

    def test_child_output_only_publishes_aggregate_and_has_local_cwd(self):
        log=self.root/'worker.log'
        code='import json,os; print(json.dumps({"remaining":0,"indexed":3,"private_caption":"DO NOT PUBLISH","cwd":os.getcwd()}))'
        result=run_worker([sys.executable,'-c',code],log,self.root,threading.Event(),5)
        self.assertEqual(result,{'state':'complete','result':{'remaining':0,'indexed':3}})
        self.assertEqual(json.loads(log.read_text())['cwd'],str(self.root))
        failure=run_worker([sys.executable,'-c','import time; time.sleep(30)'],self.root/'timeout.log',self.root,threading.Event(),.01)
        self.assertEqual(failure['state'],'timeout')
        self.assertTrue(log.exists())

    def test_commands_preserve_models_and_separate_stores(self):
        for stage in ('faces','ocr','vision','scenes','frames','descriptions','transcripts'):
            args=command(stage,self.directory,self.resources,30,10)
            self.assertIn(str(self.directory/(stage+'.db')),args)
            self.assertEqual(args[2],'src.'+stage)
        self.assertIn('--publish-groups',command('faces',self.directory,self.resources,30,10))

    def test_virtual_environment_executable_symlink_is_not_collapsed(self):
        executable=self.root/'venv/bin/python';executable.parent.mkdir(parents=True)
        executable.symlink_to(sys.executable)
        resources={**self.resources,'vision_python':str(executable)}
        supervisor=Supervisor(self.root,self.directory,resources,[self.source])
        self.assertEqual(command('vision',self.directory,supervisor.resources,30,10)[0],str(executable))
        self.assertNotEqual(str(executable),str(executable.resolve()))
