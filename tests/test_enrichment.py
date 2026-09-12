"""Coordinator ownership, source waiting, restart and private status boundaries."""
import os
os.environ.setdefault('MEDIA_VAULT_EXTERNAL_ROOTS', '/run/media')  # the tests use /run/media as the example removable root
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from src.enrichment import Supervisor,exclusive,status,command,run_worker,RESOURCES

class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp());self.source=self.root/'source';self.source.mkdir()
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
        self.assertEqual(set(status(self.directory)['stages']),{'faces','ocr','vision','scenes','frames','descriptions','transcripts','fingerprints'})
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
        for stage in ('faces','ocr','vision','scenes','frames','descriptions','transcripts','fingerprints'):
            args=command(stage,self.directory,self.resources,30,10)
            self.assertIn(str(self.directory/(stage+'.db')),args)
            self.assertEqual(args[2],'src.'+stage)
        self.assertIn('--publish-groups',command('faces',self.directory,self.resources,30,10))

    def test_virtual_environment_executable_symlink_is_not_collapsed(self):
        executable=self.root/'venv/bin/python';executable.parent.mkdir(parents=True)
        try: executable.symlink_to(sys.executable)
        except OSError: self.skipTest('symlinks unavailable')
        resources={**self.resources,'vision_python':str(executable)}
        supervisor=Supervisor(self.root,self.directory,resources,[self.source])
        self.assertEqual(command('vision',self.directory,supervisor.resources,30,10)[0],str(executable))
        self.assertNotEqual(str(executable),str(executable.resolve()))

    def test_optional_quality_shares_gpu_queue_and_requires_resource_pair(self):
        resources={**self.resources,'quality_python':str(Path(sys.executable)),'quality_model':str(self.root)}
        calls=[]
        def runner(args,*rest):calls.append(args);return {'state':'complete','result':{'remaining':0}}
        supervisor=Supervisor(self.root,self.directory,resources,[self.source],runner=runner)
        self.assertEqual(supervisor.roles['gpu'][-1],'quality')
        supervisor.run(once=True)
        self.assertEqual(len(calls),9)
        args=next(a for a in calls if a[2]=='src.quality')
        self.assertEqual(args[0],resources['quality_python']);self.assertIn('--model',args)
        incomplete={**self.resources,'quality_python':str(Path(sys.executable))}
        self.assertNotIn('quality',Supervisor(self.root,self.directory,incomplete,[self.source]).roles['gpu'])

    def test_stages_run_only_with_their_runtime_and_model(self):
        photos_only={'face_python':str(Path(sys.executable))}
        supervisor=Supervisor(self.root,self.directory,photos_only,[self.source])
        self.assertEqual(supervisor.roles,{'photos':('ocr','fingerprints')})
        with_faces={**photos_only,'face_models':str(self.root)}
        self.assertEqual(Supervisor(self.root,self.directory,with_faces,[self.source]).roles,{'photos':('faces','ocr','fingerprints')})
        with self.assertRaises(ValueError):Supervisor(self.root,self.directory,{'vision_model':str(self.root)},[self.source])
        with self.assertRaises(ValueError):Supervisor(self.root,self.directory,{**photos_only,'mystery_python':str(self.root)},[self.source])
