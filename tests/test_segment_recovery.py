"""Recover valid speech spans while retaining failed/partial coverage."""
from contextlib import closing
import io,json,sqlite3,unittest
from unittest.mock import patch
from src import transcript_chunks as chunks,transcripts
from tests import test_transcript_chunks as fixtures


def mixed(stream):
    value=fixtures.Backend().transcribe(stream)
    value['segments'].append({**value['segments'][0],'start':value['duration']-.2,'end':value['duration']+1.38})
    return value


class RecoveryTests(unittest.TestCase):
    def test_mixed_segments_publish_only_valid_and_keep_partial_and_old_attempt(self):
        f=fixtures.ChunkTests();f.fixture()
        with patch.object(f.backend,'transcribe',side_effect=mixed):f.run_batch(limit=1)
        f.run_batch(limit=20)
        with closing(sqlite3.connect(f.db)) as c:before=c.execute('SELECT count(*) FROM transcript_facts').fetchone()[0]
        f.backend.transcribe_raw=mixed
        result=f.run_batch(limit=1);self.assertEqual(result['chunks_this_run'],1);self.assertEqual(result['remaining'],0)
        detail=f.library.transcript(f.library.by_content[f.digest]['id'])
        self.assertEqual(len(detail['segments']),before+1);self.assertEqual(detail['status'],'partial');self.assertEqual(detail['coverage']['rejected_segments'],1);self.assertEqual(detail['coverage']['failed'],1)
        recovered=detail['segments'][0];self.assertEqual(json.loads(recovered['source_span'])['validation_policy'],chunks.RECOVERY)
        with closing(sqlite3.connect(f.db)) as c:
            self.assertEqual(c.execute('SELECT error,retry_policy FROM transcript_chunk_attempt_history').fetchall(),[('ValueError',chunks.RECOVERY)])
            self.assertEqual(c.execute("SELECT segments,error FROM transcript_chunks WHERE start=0").fetchone(),(1,'InvalidSegments'))
        self.assertEqual(f.run_batch()['chunks_this_run'],0)
        self.assertEqual(f.library.moments('red car')['items'][0]['moment']['transcript_status'],'partial')
    def test_all_invalid_is_failed_not_silent_and_duration_is_never_salvaged(self):
        value=mixed(io.BytesIO(b'600'));value['segments']=value['segments'][1:]
        result=chunks.validated_chunk(value,600,7200,partial=True)
        self.assertEqual(result['status'],'error');self.assertEqual(result['segments'],[]);self.assertEqual(result['rejected_segments'],1)
        silent={**value,'segments':[],'status':'no_speech'}
        self.assertEqual(chunks.validated_chunk(silent,600,7200,True)['status'],'no_speech')
        for duration in (0,float('nan'),601,7201):
            with self.assertRaises(ValueError):chunks.validated_chunk({**value,'duration':duration},600,7200,True)
        with self.assertRaises(ValueError):chunks.validated_chunk(value,600,7200,False)
        valid=fixtures.Backend().transcribe(io.BytesIO(b'600'));valid['segments'][0]['start']=600;valid['segments'][0]['end']=600.5
        self.assertEqual(chunks.validated_chunk(valid,600,7200,True)['rejected_segments'],1)
    def test_retry_interruption_is_terminal(self):
        f=fixtures.ChunkTests();f.fixture()
        with patch.object(f.backend,'transcribe',side_effect=mixed):f.run_batch(limit=1)
        f.run_batch(limit=20);f.backend.transcribe_raw=mixed
        # Enroll recovery without starting it, then emulate process loss.
        with closing(sqlite3.connect(f.db)) as c:
            chunks.recoverable(c,f.backend,{f.digest:[f.row]})
            c.execute("UPDATE transcript_chunks SET status='running' WHERE start=0");c.commit()
        self.assertEqual(f.run_batch()['chunks_this_run'],0)
        with closing(sqlite3.connect(f.db)) as c:self.assertEqual(c.execute("SELECT error FROM transcript_chunks WHERE start=0").fetchone()[0],'InterruptedAttempt')
    def test_normal_transcribe_still_validates_whole_payload(self):
        backend=object.__new__(transcripts.Whisper)
        backend.transcribe_raw=lambda stream:mixed(io.BytesIO(b'600'))
        with self.assertRaises(ValueError):backend.transcribe(None)


if __name__=='__main__':unittest.main()
