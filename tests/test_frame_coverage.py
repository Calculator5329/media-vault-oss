"""Retries count as one video; failure history remains independently observable."""
import sqlite3
import unittest
from src.frames import coverage

class FrameCoverageTests(unittest.TestCase):
    def test_current_attempt_wins_and_unrelated_models_are_not_coverage(self):
        with sqlite3.connect(':memory:') as conn:
            conn.executescript('CREATE TABLE settings(key TEXT,value TEXT); CREATE TABLE frame_work(content_hash TEXT,sampler TEXT,error TEXT); CREATE TABLE frame_facts(sampler TEXT);')
            conn.executemany('INSERT INTO settings VALUES(?,?)',[('frames_current','new'),('frames_compatible','["new","old"]')])
            conn.executemany('INSERT INTO frame_work VALUES(?,?,?)',[('recovered','old','ValueError'),('recovered','new',None),('still-failed','old','ValueError'),('still-failed','new','ValueError'),('good','old',None),('unrelated','other',None)])
            conn.executemany('INSERT INTO frame_facts VALUES(?)',[('old',),('new',),('other',)])
            result=coverage(conn)
            self.assertEqual(result,{'contents':3,'frames':2,'errors':1,'retained_attempts':6,'retained_error_records':3})

    def test_single_sampler_before_compatibility_migration(self):
        with sqlite3.connect(':memory:') as conn:
            conn.executescript("CREATE TABLE settings(key TEXT,value TEXT); CREATE TABLE frame_work(content_hash TEXT,sampler TEXT,error TEXT); CREATE TABLE frame_facts(sampler TEXT); INSERT INTO settings VALUES('frames_current','old'); INSERT INTO frame_work VALUES('good','old',NULL); INSERT INTO frame_facts VALUES('old');")
            self.assertEqual(coverage(conn)['contents'],1)
            self.assertEqual(coverage(conn)['errors'],0)
