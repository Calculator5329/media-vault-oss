"""Source additions wake the single import loop, including mid-cycle changes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from src import jobs


class ImportWakeTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp())
        self.config=self.root/'config.json';self.config.write_text('{}')

    def test_unchanged_config_preserves_idle_deadline(self):
        elapsed=[0];waits=[]
        def sleep(seconds):waits.append(seconds);elapsed[0]+=seconds
        with patch.object(jobs.time,'monotonic',side_effect=lambda:elapsed[0]),patch.object(jobs.time,'sleep',side_effect=sleep):
            jobs.wait_for_config(self.config,jobs.config_revision(self.config),12)
        self.assertEqual(waits,[5,5,2])

    def test_atomic_replacement_wakes_at_next_check(self):
        elapsed=[0];waits=[]
        def sleep(seconds):
            waits.append(seconds);elapsed[0]+=seconds
            replacement=self.root/'replacement.json';replacement.write_text('{"sources":[]}');replacement.replace(self.config)
        with patch.object(jobs.time,'monotonic',side_effect=lambda:elapsed[0]),patch.object(jobs.time,'sleep',side_effect=sleep):
            jobs.wait_for_config(self.config,jobs.config_revision(self.config),900)
        self.assertEqual(waits,[5])

    def test_change_during_running_cycle_starts_next_cycle_without_idle_wait(self):
        calls=[]
        def cycle(*args):
            calls.append(args)
            if len(calls)>1:raise KeyboardInterrupt()
            replacement=self.root/'replacement.json';replacement.write_text('{"sources":[]}');replacement.replace(self.config)
            return {'state':'complete'}
        argv=['jobs','--config',str(self.config),'--exports',str(self.root),'--watch']
        with patch('sys.argv',argv),patch.object(jobs,'cycle',side_effect=cycle),patch.object(jobs.time,'sleep') as sleep:
            with self.assertRaises(KeyboardInterrupt):jobs.main()
        self.assertEqual(len(calls),2);sleep.assert_not_called()
