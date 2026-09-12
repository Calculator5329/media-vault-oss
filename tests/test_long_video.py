"""Long-video sampling expands duration only; count and interval remain bounded."""
import unittest
from src.frames import sampling_interval

class LongVideoTests(unittest.TestCase):
    def test_sampling_interval_spreads_at_most_sixty_samples(self):
        for duration in (1,100,7200,16800,21600):
            interval=sampling_interval(duration)
            self.assertGreaterEqual(interval,5)
            self.assertLessEqual(duration/interval,60)
        self.assertEqual(sampling_interval(16800),280)

    def test_unknown_or_unbounded_duration_is_refused(self):
        for duration in (0,-1,float('nan'),float('inf'),21601):
            with self.assertRaises(ValueError):sampling_interval(duration)
