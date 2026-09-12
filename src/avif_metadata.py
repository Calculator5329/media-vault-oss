"""Read AVIF metadata missed by ImageMagick; preserve raw EXIF separately."""
import base64
from datetime import datetime
import math
from .catalog import timestamp,gps


def serializable(value):
    if isinstance(value,bytes):return {'encoding':'base64','value':base64.b64encode(value).decode('ascii')}
    if isinstance(value,dict):return {str(k):serializable(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [serializable(v) for v in value]
    if hasattr(value,'numerator') and hasattr(value,'denominator') and not isinstance(value,int):
        return {'numerator':value.numerator,'denominator':value.denominator}
    if isinstance(value,float) and not math.isfinite(value):return {'nonfinite':str(value)}
    if value is None or isinstance(value,(str,int,float,bool)):return value
    raise ValueError('Unsupported EXIF value type')


def inspect(path):
    from PIL import Image,ExifTags,__version__
    with Image.open(path) as image:
        if image.format!='AVIF':raise ValueError('Recovery is limited to detected AVIF')
        exif=image.getexif();raw=dict(exif)
        nested={}
        for tag in (34665,34853):
            if tag in exif:nested[tag]=exif.get_ifd(tag)
        props={ExifTags.TAGS.get(k,str(k)):v for k,v in raw.items()}
        props.update({ExifTags.TAGS.get(k,str(k)):v for k,v in nested.get(34665,{}).items()})
        point_props={ExifTags.GPSTAGS.get(k,str(k)):v for k,v in nested.get(34853,{}).items()}
        for key in ('GPSLatitude','GPSLongitude'):
            if isinstance(point_props.get(key),(tuple,list)):
                point_props[key]=', '.join(str(float(v)) for v in point_props[key])
        result={'extractor':'pillow-avif','extractor_version':'avif-1:pillow-'+__version__,
                'format':image.format,'width':image.width,'height':image.height,
                'raw_exif':serializable(raw),'raw_ifds':serializable(nested),
                'exif':serializable(props),'date':None,'location':None}
        for key,offset in [('DateTimeOriginal','OffsetTimeOriginal'),('DateTimeDigitized','OffsetTimeDigitized'),('DateTime','OffsetTime')]:
            parsed=timestamp(props.get(key),props.get(offset),exif=True)
            if parsed:
                result['date']={'value':parsed,'source':'pillow-exif:'+key,'meaning':'modification' if key=='DateTime' else 'capture','timezone_known':datetime.fromisoformat(parsed).tzinfo is not None};break
        for key in ('GPSLatitudeRef','GPSLongitudeRef'):
            if not isinstance(point_props.get(key),str):point_props.pop(key,None)
        point=gps(point_props)
        if point:result['location']={'lat':point[0],'lon':point[1],'source':'pillow-exif:GPSLatitude/GPSLongitude'}
        return result
