"""Snapshot spacing includes boundaries and follows video query scope."""
import unittest
from src.frames import sampling_gaps,gap_plans
from src import frames,scenes
from tests import test_video_moments as demo


def row(t,duration=100):return {'content_hash':'synthetic','timestamp':t,'duration':duration}


class SamplingGapTests(unittest.TestCase):
    def test_edges_duplicates_and_single_snapshot(self):
        self.assertEqual(sampling_gaps([row(20),row(30),row(30)])['synthetic']['largest_gap_seconds'],70)
        self.assertEqual(sampling_gaps([row(80)])['synthetic']['largest_gap_seconds'],80)
        self.assertEqual(sampling_gaps([row(0),row(100)])['synthetic']['largest_gap_seconds'],100)
        self.assertEqual(sampling_gaps([]),{})
    def test_invalid_or_conflicting_time_does_not_become_measurement(self):
        for rows in ([row(float('nan'))],[row(-1)],[row(101)],[row(0,0)],[row(1,100),row(2,101)]):
            result=sampling_gaps(rows)['synthetic']
            self.assertFalse(result['available']);self.assertIsNone(result['largest_gap_seconds']);self.assertIsNone(result['duration_seconds'])
    def test_gap_plan_is_deterministic_and_only_subdivides_large_gaps(self):
        def sample(t):return {**row(t),'frame_id':str(t),'sampler':'synthetic','image_hash':'synthetic-pixels'}
        rows=[sample(0),sample(10),sample(70),sample(100)]
        plan=gap_plans(iter(rows))['synthetic']
        self.assertEqual(plan['targets'],[25,40,55]);self.assertEqual(plan['requested_count'],3)
        self.assertEqual(gap_plans(list(reversed(rows)))['synthetic']['revision'],plan['revision'])
        changed=[{**r,'image_hash':'changed'} for r in rows]
        self.assertNotEqual(gap_plans(changed)['synthetic']['revision'],plan['revision'])
        self.assertEqual(gap_plans(rows,max_targets=2)['synthetic']['status'],'over_budget')
        self.assertEqual(gap_plans(rows,max_targets=2)['synthetic']['targets'],[])
        self.assertNotEqual(gap_plans(rows,spacing=10)['synthetic']['revision'],plan['revision'])
    def test_plan_rejects_invalid_policy_and_does_not_invent_missing_time(self):
        for options in ({'spacing':0},{'spacing':float('nan')},{'spacing':31},{'max_targets':0},{'max_targets':True}):
            with self.assertRaises(ValueError):gap_plans([],**options)
        self.assertEqual(gap_plans([row(-1)])['synthetic']['status'],'unavailable')
        self.assertEqual(gap_plans([row(0,30000)])['synthetic']['status'],'unavailable')
        self.assertEqual(gap_plans([row(0,10)]),{})

    def test_search_counts_and_item_spacing_follow_filters(self):
        f=demo.VideoMomentTests();f.setUp();root=f.f.f.root
        frames.index(f.f.f.db,root/'frames.db',demo.SampleBackend(),reader=demo.reader)
        scenes.index(f.f.f.db,root/'scenes.db',demo.Encoder())
        found=f.v.video_moments('red car');self.assertEqual(found['sampled_videos'],1)
        self.assertEqual(found['eligible_videos'],1);self.assertEqual(found['videos_without_samples'],0)
        self.assertEqual(found['items'][0]['sampling']['largest_gap_seconds'],1)
        filtered=f.v.video_moments('red car',year='2026')
        self.assertEqual(filtered['sampled_videos'],0);self.assertEqual(filtered['eligible_videos'],0)
        self.assertIsNone(filtered['sampling']['largest_gap_seconds'])
        (f.f.f.source/'unsampled.mp4').write_bytes(b'second synthetic video');f.f.f.refresh();f.v=f.f.build();f.v.encoder=demo.Encoder()
        pending=f.v.video_moments('red car');self.assertEqual(pending['eligible_videos'],2);self.assertEqual(pending['videos_without_samples'],1)


if __name__=='__main__':unittest.main()
