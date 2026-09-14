"""Keywords, captions, titles, ratings and dates written into a photo by editing tools.

Lightroom, darktable, digiKam, Photoshop and Apple Photos exports keep this in an XMP
packet and, for older workflows, an IPTC block, neither of which ImageMagick exposes as
properties. Pillow reads both without decoding pixels. Everything here is a source claim
with the field it came from; nothing is inferred.
"""
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
import re
import xml.etree.ElementTree as ET

NS = {'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#', 'dc': 'http://purl.org/dc/elements/1.1/',
      'xmp': 'http://ns.adobe.com/xap/1.0/', 'photoshop': 'http://ns.adobe.com/photoshop/1.0/',
      'lr': 'http://ns.adobe.com/lightroom/1.0/', 'exif': 'http://ns.adobe.com/exif/1.0/'}
DATE_FIELDS = ('photoshop:DateCreated', 'exif:DateTimeOriginal', 'xmp:CreateDate')
MAX_KEYWORDS = 200


def _tag(prefix, name):
    return '{%s}%s' % (NS[prefix], name)


def _text_values(description, prefix, name):
    """Every value of a property, whether it is an attribute, a simple element, or an rdf container."""
    attribute = description.get(_tag(prefix, name))
    if attribute is not None:
        return [attribute.strip()]
    values = []
    for element in description.findall(_tag(prefix, name)):
        items = element.findall('.//' + _tag('rdf', 'li'))
        if items:
            values += [(item.text or '').strip() for item in items]
        elif element.text and element.text.strip():
            values.append(element.text.strip())
    return [v for v in values if v]


def parse_xmp(packet):
    """The fields Media Vault uses from an XMP packet, keyed by prefix:Name. Raises on bad XML."""
    text = packet.decode('utf-8', 'replace') if isinstance(packet, bytes) else packet
    root = ET.fromstring(text[text.index('<'):])
    fields = {}
    for description in root.iter(_tag('rdf', 'Description')):
        for prefix, name in [('dc', 'subject'), ('lr', 'hierarchicalSubject'), ('dc', 'title'), ('dc', 'description'),
                             ('xmp', 'Rating'), ('photoshop', 'DateCreated'), ('exif', 'DateTimeOriginal'), ('xmp', 'CreateDate')]:
            values = _text_values(description, prefix, name)
            if values:
                fields.setdefault(f'{prefix}:{name}', []).extend(values)
    return fields


def parse_iptc(info):
    """Pillow's IPTC dictionary, {(record, dataset): bytes or [bytes]}, to the same shape as XMP."""
    def texts(key):
        raw = info.get(key)
        if raw is None:
            return []
        raw = raw if isinstance(raw, list) else [raw]
        return [v.decode('utf-8', 'replace').strip() for v in raw if isinstance(v, bytes) and v.strip()]
    fields = {}
    for key, name in [((2, 25), 'iptc:Keywords'), ((2, 5), 'iptc:ObjectName'), ((2, 120), 'iptc:Caption'),
                      ((2, 55), 'iptc:DateCreated'), ((2, 60), 'iptc:TimeCreated')]:
        values = texts(key)
        if values:
            fields[name] = values
    return fields


def iso_date(value, time=None):
    """An XMP or IPTC date as ISO 8601, or None when it is partial or malformed."""
    value = (value or '').strip()
    if re.fullmatch(r'\d{8}', value):  # IPTC 2:55, with 2:60 as HHMMSS+HHMM
        value = f'{value[:4]}-{value[4:6]}-{value[6:]}'
        stamp = (time or '').strip()
        if re.fullmatch(r'\d{6}([+-]\d{4})?', stamp):
            value += f'T{stamp[:2]}:{stamp[2:4]}:{stamp[4:6]}' + (f'{stamp[6:9]}:{stamp[9:]}' if len(stamp) > 6 else '')
        else:
            return None
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?', value):
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).isoformat()
    except ValueError:
        return None


def summarize(xmp, iptc):
    """What the rest of the vault reads: keywords, title, caption, rating and a dated field."""
    keywords = []
    for value in xmp.get('dc:subject', []) + [v.split('|')[-1] for v in xmp.get('lr:hierarchicalSubject', [])] + iptc.get('iptc:Keywords', []):
        if value and value not in keywords:
            keywords.append(value)
    result = {'keywords': keywords[:MAX_KEYWORDS]}
    for key, sources in [('title', ('dc:title', 'iptc:ObjectName')), ('caption', ('dc:description', 'iptc:Caption'))]:
        for source in sources:
            values = (xmp if source.startswith(('dc', 'xmp', 'photoshop')) else iptc).get(source)
            if values:
                result[key] = {'value': values[0][:2000], 'source': source}
                break
    rating = xmp.get('xmp:Rating', [''])[0]
    if re.fullmatch(r'-?\d+(\.0+)?', rating) and -1 <= float(rating) <= 5:
        result['rating'] = int(float(rating))
    for field in DATE_FIELDS:
        for raw in xmp.get(field, []):
            parsed = iso_date(raw)
            if parsed:
                result['created'] = {'value': parsed, 'source': f'xmp:{field}', 'timezone_known': datetime.fromisoformat(parsed).tzinfo is not None}
                break
        if 'created' in result:
            break
    if 'created' not in result and iptc.get('iptc:DateCreated'):
        parsed = iso_date(iptc['iptc:DateCreated'][0], iptc.get('iptc:TimeCreated', [''])[0])
        if parsed:
            result['created'] = {'value': parsed, 'source': 'iptc:DateCreated', 'timezone_known': datetime.fromisoformat(parsed).tzinfo is not None}
    return result


def sidecar_for(path):
    """The XMP sidecar RAW workflows write beside a file: photo.xmp, or photo.NEF.xmp."""
    path = Path(path)
    for candidate in (path.with_suffix('.xmp'), path.with_suffix(path.suffix + '.xmp')):
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def png_creation(info):
    """PNG writers stamp a tEXt 'Creation Time' in RFC 1123 or ISO form; screenshots often have nothing else."""
    raw = info.get('Creation Time')
    if not isinstance(raw, str):
        return None
    parsed = iso_date(raw.strip())
    if not parsed:
        try:
            parsed = parsedate_to_datetime(raw.strip()).isoformat()
        except (TypeError, ValueError, IndexError):
            return None
    return {'value': parsed, 'source': 'png:Creation Time', 'timezone_known': datetime.fromisoformat(parsed).tzinfo is not None}


def describe(path):
    """Read the XMP packet, IPTC block, XMP sidecar and PNG creation time for a photo.

    None when the file carries none of them. Raises OSError or ValueError when Pillow cannot
    open the file; the caller records that as a non-fatal stage error, because ImageMagick
    has already measured the image."""
    from PIL import Image, IptcImagePlugin
    with Image.open(path) as image:
        packet = image.info.get('xmp')
        iptc_raw = IptcImagePlugin.getiptcinfo(image) or {}
        png_created = png_creation(image.info) if image.format == 'PNG' else None
    xmp = parse_xmp(packet) if packet else {}
    sidecar = sidecar_for(path)
    if sidecar is not None:
        # A sidecar is the editor's current word; embedded fields fill in whatever it does not say.
        xmp = {**xmp, **parse_xmp(sidecar.read_bytes())}
    iptc = parse_iptc(iptc_raw)
    if not xmp and not iptc and not png_created:
        return None
    result = {'xmp': xmp, 'iptc': iptc, **summarize(xmp, iptc)}
    if sidecar is not None:
        result['sidecar'] = sidecar.name
    if 'created' not in result and png_created:
        result['created'] = png_created
    return result
