"""Keywords, captions, ratings and dates that editing tools write into a photo or beside it."""
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from src import catalog, embedded
from tests.scratch import scratch

XMP = b'''<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description
 xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:xmp="http://ns.adobe.com/xap/1.0/"
 xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" xmlns:lr="http://ns.adobe.com/lightroom/1.0/"
 xmp:Rating="4" photoshop:DateCreated="2019-07-04T18:30:00-05:00">
<dc:subject><rdf:Bag><rdf:li>beach</rdf:li><rdf:li>family</rdf:li></rdf:Bag></dc:subject>
<lr:hierarchicalSubject><rdf:Bag><rdf:li>places|lake|dock</rdf:li><rdf:li>family</rdf:li></rdf:Bag></lr:hierarchicalSubject>
<dc:description><rdf:Alt><rdf:li xml:lang="x-default">Sunset at the lake</rdf:li></rdf:Alt></dc:description>
<dc:title><rdf:Alt><rdf:li xml:lang="x-default">Lake evening</rdf:li></rdf:Alt></dc:title>
</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'''
SIDECAR = b'''<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="5">
<dc:subject><rdf:Bag><rdf:li>edited</rdf:li></rdf:Bag></dc:subject></rdf:Description></rdf:RDF></x:xmpmeta>'''


class EmbeddedTests(unittest.TestCase):
    def setUp(self):
        self.root = scratch()
        self.photo = self.root / 'lake.jpg'
        Image.new('RGB', (8, 8), (10, 20, 30)).save(self.photo, xmp=XMP)

    def test_xmp_keywords_caption_title_rating_and_date_are_read_with_their_fields(self):
        found = embedded.describe(self.photo)
        self.assertEqual(found['keywords'], ['beach', 'family', 'dock'])
        self.assertEqual(found['title'], {'value': 'Lake evening', 'source': 'dc:title'})
        self.assertEqual(found['caption'], {'value': 'Sunset at the lake', 'source': 'dc:description'})
        self.assertEqual(found['rating'], 4)
        self.assertEqual(found['created'], {'value': '2019-07-04T18:30:00-05:00', 'source': 'xmp:photoshop:DateCreated', 'timezone_known': True})

    def test_a_plain_photo_has_nothing_embedded(self):
        plain = self.root / 'plain.jpg'
        Image.new('RGB', (8, 8)).save(plain)
        self.assertIsNone(embedded.describe(plain))

    def test_an_xmp_sidecar_speaks_over_the_embedded_packet(self):
        (self.root / 'lake.xmp').write_bytes(SIDECAR)
        found = embedded.describe(self.photo)
        self.assertEqual((found['rating'], found['keywords'], found['sidecar']), (5, ['edited', 'dock', 'family'], 'lake.xmp'), 'dc:subject comes from the sidecar; the hierarchical keywords it does not mention stay')
        self.assertEqual(found['caption']['value'], 'Sunset at the lake', 'fields the sidecar does not mention stay')

    def test_iptc_fields_and_partial_dates(self):
        fields = embedded.parse_iptc({(2, 25): [b'dog', b'park'], (2, 120): b'A dog in the park', (2, 55): b'20180203', (2, 60): b'141500+0100'})
        summary = embedded.summarize({}, fields)
        self.assertEqual(summary['keywords'], ['dog', 'park'])
        self.assertEqual(summary['created'], {'value': '2018-02-03T14:15:00+01:00', 'source': 'iptc:DateCreated', 'timezone_known': True})
        self.assertNotIn('created', embedded.summarize({}, embedded.parse_iptc({(2, 55): b'20180203'})), 'a date with no time is not a capture moment')
        self.assertIsNone(embedded.iso_date('2019-07'))
        self.assertEqual(embedded.iso_date('2019-07-04T18:30'), '2019-07-04T18:30:00')

    def test_png_creation_time_is_read_in_either_form(self):
        for name, raw in [('a.png', 'Tue, 02 Jan 2024 03:04:05 +0000'), ('b.png', '2024-01-02T03:04:05')]:
            info = PngImagePlugin.PngInfo(); info.add_text('Creation Time', raw)
            Image.new('RGB', (4, 4)).save(self.root / name, pnginfo=info)
        self.assertEqual(embedded.describe(self.root / 'a.png')['created'], {'value': '2024-01-02T03:04:05+00:00', 'source': 'png:Creation Time', 'timezone_known': True})
        self.assertEqual(embedded.describe(self.root / 'b.png')['created']['timezone_known'], False)

    def test_inspect_uses_the_embedded_date_only_when_exif_has_none(self):
        with patch.object(catalog.probe, '_run', return_value='8\n8\n'):
            result = catalog.inspect(self.photo)
        self.assertEqual(result['date'], {'value': '2019-07-04T18:30:00-05:00', 'source': 'xmp:photoshop:DateCreated', 'timezone_known': True, 'meaning': 'capture'})
        self.assertEqual(result['embedded']['keywords'], ['beach', 'family', 'dock'])
        with patch.object(catalog.probe, '_run', return_value='8\n8\nexif:DateTimeOriginal=2020:01:01 00:00:00\n'):
            result = catalog.inspect(self.photo)
        self.assertEqual(result['date']['source'], 'exif:DateTimeOriginal')

    def test_a_file_pillow_cannot_open_records_a_non_fatal_stage_error(self):
        odd = self.root / 'odd.jpg'; odd.write_bytes(b'not an image')
        with patch.object(catalog.probe, '_run', return_value='8\n8\n'):
            result = catalog.inspect(odd)
        self.assertEqual((result['width'], result['errors'][0]['stage']), (8, 'embedded-metadata'))


if __name__ == '__main__':
    unittest.main()
