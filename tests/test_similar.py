import random
import unittest
from src.similar import propose, hamming, stack_id, NEAR, BURST


def item(digest,day,when=None,pixels=(100,100)):
    return {'content_hash':digest,'day':day,'date':{'value':when} if when else None,'width':pixels[0],'height':pixels[1]}


class ProposalTests(unittest.TestCase):
    def test_same_day_near_hashes_stack_and_largest_becomes_top(self):
        a,b,c='a'*64,'b'*64,'c'*64
        stacks=propose([item(a,'2020-01-01','2020-01-01T10:00:00'),item(b,'2020-01-01','2020-01-01T18:00:00',(400,300)),item(c,'2020-01-02','2020-01-02T10:00:00')],
                       {a:('ff00ff00ff00ff00',None,None),b:('ff00ff00ff00ff01',None,None),c:('ff00ff00ff00ff00',None,None)})
        self.assertEqual(len(stacks),1)
        self.assertEqual((stacks[0]['contents'],stacks[0]['top'],stacks[0]['id']),([a,b],b,stack_id([a,b])),'a different day never joins, even with an identical hash')

    def test_bursts_allow_looser_matches_only_within_seconds(self):
        a,b,c='a'*64,'b'*64,'c'*64
        loose='ff00ff00ff00ff00';far=format(int(loose,16)^0b1111111111,'016x')
        self.assertEqual(hamming(loose,far),10)
        other=format(int(loose,16)^(0b1111111111<<20),'016x')
        hashes={a:(loose,None,None),b:(far,None,None),c:(other,None,None)}
        stacks=propose([item(a,'2020-01-01','2020-01-01T10:00:00'),item(b,'2020-01-01','2020-01-01T10:00:40'),item(c,'2020-01-01','2020-01-01T12:00:00')],hashes)
        self.assertEqual([s['contents'] for s in stacks],[[a,b]])
        undated=propose([item(a,None),item(b,None)],hashes)
        self.assertEqual(undated,[],'undated photos only stack on the tight limit')

    def test_candidate_passes_are_exhaustive_up_to_each_limit(self):
        rng=random.Random(7)
        for _ in range(300):
            base=rng.getrandbits(64);burst=rng.random()<.5;flips=rng.sample(range(64),rng.randint(1,BURST if burst else NEAR))
            other=base
            for bit in flips:other^=1<<bit
            hashes={'a'*64:(format(base,'016x'),None,None),'b'*64:(format(other,'016x'),None,None)}
            stacks=propose([item('a'*64,'2020-01-01','2020-01-01T10:00:00'),item('b'*64,'2020-01-01','2020-01-01T10:00:05' if burst else '2020-01-01T16:00:00')],hashes)
            self.assertEqual(len(stacks),1,f'{len(flips)} flipped bits were not proposed (burst={burst})')
        self.assertLess(NEAR,BURST)


class HashTests(unittest.TestCase):
    def test_dhash_is_stable_across_scale(self):
        try:from PIL import Image
        except ImportError:self.skipTest('Pillow is not installed for this interpreter')
        from src.similar import dhash
        image=Image.new('RGB',(40,30))
        for x in range(40):
            for y in range(30):image.putpixel((x,y),(x*6,y*8,(x*y)%255))
        self.assertLessEqual(hamming(dhash(image),dhash(image.resize((20,15)))),2)


if __name__=='__main__':unittest.main()
