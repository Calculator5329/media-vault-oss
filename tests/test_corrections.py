import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from src.corrections import ASSIGNMENTS,OBSERVATIONS,ConstraintOverlay,CorrectionError,append,apply_constraints,read_log,setup
from src.kit import Rebuilder

def event(key,op,**args):return {"id":key,"op":op,"at":"2026-09-04T00:00:00Z","reason":"fictional test correction",**args}

class CorrectionTests(unittest.TestCase):
    def test_merge_split_negative_and_disabled_application_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/"fictional.json";log=root/"corrections/events.jsonl";db=root/"index.db"
            source.write_text(json.dumps({"photo-a":"entity-a","photo-b":"entity-b","photo-c":"entity-a","photo-d":"entity-a"}))
            def derive(p):
                for obs,entity in json.loads(p.read_text()).items():
                    yield OBSERVATIONS,{"observation_id":obs,"entity_id":entity,"source_path":str(p),"source_span":obs,"extractor":"fictional-detector","extractor_version":"1","confidence":0.7,"derived_at":"2026-01-01T00:00:00Z","tier":"personal"}
            def answers(path):
                with closing(sqlite3.connect(path)) as c:return dict(c.execute(f"SELECT observation_id,entity_id FROM {ASSIGNMENTS}"))
            append(log,event("m","merge",left="entity-a",right="entity-b"))
            append(log,event("s","split",entity="entity-a",new_entity="entity-c",observations=["photo-c"]))
            append(log,event("n","not-her",entity="entity-a",observation="photo-d"))
            expected={"photo-a":"entity-a","photo-b":"entity-a","photo-c":"entity-c","photo-d":None}
            # Disabled application must fail the actual answer criterion first.
            Rebuilder(db,setup,derive).rebuild([source])
            with self.assertRaises(AssertionError):self.assertEqual(answers(db),expected)
            shutil.move(db,root/"disabled-index.archive.db")
            r=Rebuilder(db,setup,derive,ConstraintOverlay(log));r.rebuild([source]);self.assertEqual(answers(db),expected)
            snapshot=log.read_bytes();shutil.move(db,root/"corrected-index.archive.db")
            r.rebuild([source]);self.assertEqual(answers(db),expected);self.assertEqual(log.read_bytes(),snapshot)
            self.assertEqual(r.rebuild([source]),[]);self.assertEqual(answers(db),expected)
            with closing(sqlite3.connect(db)) as c:
                self.assertEqual(c.execute(f"SELECT count(*) FROM {ASSIGNMENTS} WHERE extractor='mv.owner-constraints' AND tier='personal' AND confidence=0.7").fetchone()[0],4)
    def test_negative_constraint_cannot_be_evaded_by_later_merge(self):
        events=[event("n","not-her",entity="b",observation="one"),event("m","merge",left="a",right="b")]
        with self.assertRaises(CorrectionError):apply_constraints({"one":"a","two":"b"},events)
    def test_split_cannot_be_merged_away_and_stale_ids_refuse(self):
        events=[event("s","split",entity="a",new_entity="b",observations=["one"]),event("m","merge",left="a",right="b")]
        with self.assertRaises(CorrectionError):apply_constraints({"one":"a","two":"a"},events)
        with self.assertRaises(CorrectionError):apply_constraints({"one":"a"},[event("n","not-her",entity="a",observation="missing")])
    def test_append_never_changes_prior_lines_and_rejects_duplicate_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/"events.jsonl";a=event("a","not-her",entity="a",observation="one");append(log,a);before=log.read_bytes()
            append(log,event("b","merge",left="a",right="b"));self.assertTrue(log.read_bytes().startswith(before))
            with self.assertRaises(CorrectionError):append(log,a)
            self.assertEqual(len(read_log(log)),2)

    def test_owner_can_retract_a_mistake_without_rewriting_history(self):
        events=[event("split","split",entity="a",new_entity="b",observations=["one"]),event("undo","retract",target="split")]
        self.assertEqual(apply_constraints({"one":"a","two":"a"},events),{"one":"a","two":"a"})
        with self.assertRaises(CorrectionError):apply_constraints({"one":"a"},[event("invalid","retract",target="missing")])
