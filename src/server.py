"""Loopback-only viewer for the fresh catalog. Originals are read-only.

python3 -m src.server opens no browser and sends nothing to another service.
The viewer serves resized JPEG previews, never an arbitrary filesystem path.
"""
import argparse
from contextlib import closing
from datetime import date, datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import uuid
from urllib.parse import parse_qs, urlsplit

from .portable import assert_local_state

ROOT = Path(__file__).resolve().parents[1]
THUMBNAIL_WORKERS = threading.BoundedSemaphore(8)
from .export import Exporter
EXPORTER = Exporter()
BASEMAP_LAYERS = ('land','water','borders','highways','places')


class Viewer:
    def __init__(self, database, thumbnails=None):
        database = Path(database).resolve(strict=True)
        self.catalog_database = database
        self.catalog_directory = database.parent
        self.thumbnails = (Path(thumbnails) if thumbnails else database.parent / 'previews').resolve()
        assert_local_state(self.thumbnails, 'Preview cache')
        # The first configured source owns catalog.db; every further source has its own
        # catalog under source-catalogs/. Items from those carry an absolute path so the
        # single-root code below keeps working unchanged (joining a root with an absolute
        # path yields the absolute path).
        extra = sorted((database.parent / 'source-catalogs').glob('*.db')) if (database.parent / 'source-catalogs').is_dir() else []
        self.sources = []
        rows = []
        for index, path in enumerate([database, *extra]):
            conn = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
            conn.row_factory = sqlite3.Row
            try:
                root = Path(conn.execute("SELECT value FROM settings WHERE key='source'").fetchone()[0]).resolve()
                if self.thumbnails == root or root in self.thumbnails.parents:
                    raise ValueError('Preview cache cannot be inside the source')
                self.sources.append(root)
                for row in conn.execute('''SELECT path,kind,extension,size,mtime_ns,content_hash,error,
                    json_extract(metadata,'$.date') AS date,
                    json_extract(metadata,'$.location') AS location,
                    json_extract(metadata,'$.exif.Model') AS camera,
                    json_extract(metadata,'$.embedded.keywords') AS keywords,
                    json_extract(metadata,'$.embedded.caption.value') AS caption,
                    json_extract(metadata,'$.embedded.title.value') AS title,
                    json_extract(metadata,'$.embedded.rating') AS rating,
                    json_extract(metadata,'$.errors') AS errors,
                    json_extract(metadata,'$.width') AS width,
                    json_extract(metadata,'$.height') AS height,
                    json_extract(metadata,'$.video.duration_s[0]') AS duration
                    FROM files WHERE present=1''').fetchall():
                    row = dict(row)
                    if index:
                        row['path'] = str(root / row['path'])
                    rows.append(row)
                if index == 0:
                    self.inventory_at = conn.execute("SELECT value FROM settings WHERE key='inventory_at'").fetchone()[0]
            finally:
                conn.close()
        self.source = self.sources[0]
        hash_counts = {}
        for row in rows:
            if row['content_hash']:
                hash_counts[row['content_hash']] = hash_counts.get(row['content_hash'], 0)+1
        self.items = []
        self.paths = {}
        for row in rows:
            relative = row['path']
            key = hashlib.sha256(f"{relative}\0{row['size']}\0{row['mtime_ns']}".encode()).hexdigest()
            date = json.loads(row['date']) if row['date'] else None
            value = date['value'] if date else None
            item = {'id': key, 'name': Path(relative).name, 'kind': row['kind'], 'extension': row['extension'],
                    'size': row['size'], 'date': date, 'day': value[:10] if value else None,
                    'location': json.loads(row['location']) if row['location'] else None,
                    'camera': row['camera'], 'width': row['width'], 'height': row['height'],
                    'keywords': json.loads(row['keywords']) if row['keywords'] else [], 'caption': row['caption'], 'title': row['title'], 'rating': row['rating'],
                    'duration': row['duration'], 'metadata_error': bool(row['error'] or (row['errors'] and json.loads(row['errors']))),
                    'duplicate': bool(row['content_hash'] and hash_counts[row['content_hash']]>1)}
            self.items.append(item)
            self.paths[key] = (relative, row['size'], row['mtime_ns'])
        self.items.sort(key=lambda i: (i['day'] or '', i['name']), reverse=True)
        self.by_id = {item['id']: item for item in self.items}

    def summary(self):
        dates = [i['day'] for i in self.items if i['day']]
        return {'files':len(self.items), 'bytes':sum(i['size'] for i in self.items),
                'photos':sum(i['kind']=='photo' for i in self.items), 'videos':sum(i['kind']=='video' for i in self.items),
                'missing_date':sum(not i['day'] for i in self.items),'inferred_date':sum(bool(i.get('date_inferred')) for i in self.items), 'missing_location':sum(not i['location'] for i in self.items),
                'metadata_errors':sum(i['metadata_error'] for i in self.items),
                'duplicate_files':sum(i['duplicate'] for i in self.items),
                'first_date':min(dates) if dates else None, 'last_date':max(dates) if dates else None,
                'inventory_at':self.inventory_at, 'source_available':all(root.is_dir() for root in self.sources),
                'years':sorted({d[:4] for d in dates}, reverse=True),
                'capabilities':{'faces':False,'semantic_search':False,'ocr':False}}

    def progress(self):
        """Read current worker checkpoints; missing stores are unavailable, not zero."""
        from .imports import summary as import_summary
        result = {}
        from .jobs import status
        result['import_service']=status(self.catalog_directory)
        from .enrichment import status as enrichment_status
        result['enrichment_service']=enrichment_status(self.catalog_directory)
        for name in ('imports','metadata','vision','ocr','descriptions','transcripts','frames','scenes'):
            path = self.catalog_directory / (name+'.db')
            if not path.is_file():
                result[name] = {'available':False}
                continue
            try:
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as conn:
                    conn.row_factory=sqlite3.Row
                    if name=='imports':
                        values=import_summary(conn)
                    elif name=='metadata':
                        values={'contents':conn.execute('SELECT count(DISTINCT content_hash) FROM metadata_facts').fetchone()[0],
                                'facts':conn.execute('SELECT count(*) FROM metadata_facts').fetchone()[0]}
                    elif name=='frames':
                        from .frames import coverage
                        values=coverage(conn)
                    elif name=='transcripts':
                        values={'contents':conn.execute('SELECT count(*) FROM transcript_work').fetchone()[0],'segments':conn.execute('SELECT count(*) FROM transcript_facts').fetchone()[0],'errors':conn.execute("SELECT count(*) FROM transcript_work WHERE status='error'").fetchone()[0]}
                    elif name=='descriptions':
                        values={'contents':conn.execute('SELECT count(DISTINCT content_hash) FROM description_facts').fetchone()[0],'errors':conn.execute('SELECT count(DISTINCT e.content_hash) FROM description_errors e WHERE NOT EXISTS (SELECT 1 FROM description_facts f WHERE f.content_hash=e.content_hash)').fetchone()[0],'retained_error_records':conn.execute('SELECT count(*) FROM description_errors').fetchone()[0]}
                    elif name=='ocr':
                        values={'contents':conn.execute('SELECT count(DISTINCT content_hash) FROM ocr_facts').fetchone()[0],
                                'with_text':conn.execute("SELECT count(DISTINCT content_hash) FROM ocr_facts WHERE text!=''").fetchone()[0],
                                'errors':conn.execute('SELECT count(DISTINCT e.content_hash) FROM ocr_errors e WHERE NOT EXISTS (SELECT 1 FROM ocr_facts f WHERE f.content_hash=e.content_hash AND f.model=e.model)').fetchone()[0],
                                'retained_error_records':conn.execute('SELECT count(*) FROM ocr_errors').fetchone()[0]}
                    else:
                        values={'contents':conn.execute('SELECT count(DISTINCT content_hash) FROM visual_facts').fetchone()[0],
                                'errors':conn.execute('SELECT count(*) FROM visual_errors').fetchone()[0]}
                    result[name]={'available':True,**values}
            except sqlite3.Error:
                result[name]={'available':False}
        return result

    def metadata(self,key):
        relative,_,_=self.paths[key]
        with closing(sqlite3.connect(self.catalog_database.as_uri()+'?mode=ro',uri=True)) as conn:
            row=conn.execute('SELECT metadata FROM files WHERE path=?',(relative,)).fetchone()
        return {'raw_embedded':json.loads(row[0]) if row and row[0] else None,
                'sources':[{'path':str(self.source/relative)}],
                'status':'Original embedded metadata snapshot; content verification may still be pending.'}

    def _select(self, query='', kind='all', year='', after='', before='', near=''):
        if kind not in ('all','photo','video','unknown','undated','duplicates','errors','located'):
            raise ValueError('Unknown filter')
        if year and not re.fullmatch(r'\d{4}', year):
            raise ValueError('Invalid year')
        if len(query)>300:
            raise ValueError('Search is too long')
        for value in (after,before):
            if value:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
                    raise ValueError('Invalid date')
                date.fromisoformat(value)
        if after and before and after>before:
            raise ValueError('Date range is reversed')
        point = None
        if near:
            point = tuple(float(v) for v in near.split(','))
            if len(point)!=3 or not all(math.isfinite(v) for v in point) or not (-90<=point[0]<=90 and -180<=point[1]<=180 and 0<point[2]<=20000):
                raise ValueError('Use latitude,longitude,radius_km')
        terms = query.casefold().split()
        selected = []
        for item in self.items:
            if kind in ('photo','video') and item['kind']!=kind: continue
            if kind=='unknown' and item['location']: continue
            if kind=='undated' and item['day']: continue
            if kind=='duplicates' and not item['duplicate']: continue
            if kind=='errors' and not item['metadata_error']: continue
            if kind=='located' and not item['location']: continue
            if year and (not item['day'] or not item['day'].startswith(year)): continue
            if after and (not item['day'] or item['day']<after): continue
            if before and (not item['day'] or item['day']>before): continue
            if point:
                location=item['location']
                if not location: continue
                lat1,lat2=map(math.radians,(point[0],location['lat']))
                dlat=lat2-lat1;dlon=math.radians(location['lon']-point[1])
                distance=6371*2*math.asin(min(1,math.sqrt(math.sin(dlat/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2)))
                if distance>point[2]: continue
            searchable = f"{item['name']} {item['camera'] or ''} {item['day'] or ''} {' '.join(item.get('keywords') or [])} {item.get('caption') or ''} {item.get('title') or ''}".casefold()
            if not all(term in searchable for term in terms): continue
            selected.append(item)
        return selected

    def search(self, query='', kind='all', year='', offset=0, limit=80, after='', before='', near=''):
        if offset < 0 or limit < 1 or limit > 200:
            raise ValueError('Invalid pagination')
        selected = self._select(query,kind,year,after,before,near)
        return {'items':selected[offset:offset+limit], 'total':len(selected),
                'next_offset':offset+limit if offset+limit<len(selected) else None}

    def slideshow(self, query='', year='', after='', before='', near='', located=False, limit=500, kind='all'):
        if not 1<=limit<=500:
            raise ValueError('Slideshow limit must be 1 through 500')
        selected = [i for i in self._select(query,'located' if located else kind,year,after,before,near) if i['kind']=='photo']
        selected.sort(key=lambda i:(i['day'] is None,i['day'] or '',i['name'],i['id']))
        return {'items':selected[:limit], 'total':len(selected), 'truncated':len(selected)>limit,
                'order':'oldest first; undated last', 'inventory_at':self.inventory_at,
                'filters':{'query':query,'year':year,'after':after,'before':before,'near':near,'located':located,'kind':kind},
                'coverage':'Current master catalog only; source verification and ZIP integration are in progress.'}

    def source_path(self, key):
        if key not in self.paths:
            raise FileNotFoundError('Unknown item')
        relative, size, mtime = self.paths[key]
        path = self.source / relative
        # Sources must still be real files below the configured root.
        resolved = path.resolve(strict=True)
        if path.is_symlink() or not any(root in resolved.parents for root in self.sources):
            raise FileNotFoundError('Source changed')
        info = resolved.stat()
        if (info.st_size,info.st_mtime_ns)!=(size,mtime):
            raise FileNotFoundError('Source changed; refresh the catalog')
        return resolved

    def thumbnail(self, key):
        if key not in self.paths or self.by_id[key]['kind'] == 'other':
            raise FileNotFoundError('Unknown item')
        target = self.thumbnails / (key+'.jpg')
        if target.is_file() and target.stat().st_size:
            return target
        with THUMBNAIL_WORKERS:
            if target.is_file() and target.stat().st_size:
                return target
            source = self.source_path(key)
            self.thumbnails.mkdir(parents=True, exist_ok=True)
            return self.render_preview(source, key, verify=lambda: self.source_path(key))

    def render_preview(self, source, key, verify=None):
        """One ffmpeg frame from a readable file, written to the cache under the item key.

        The caller holds THUMBNAIL_WORKERS. `verify` re-checks the source after the
        render so a file that changed underneath ffmpeg never gets cached."""
        target = self.thumbnails / (key+'.jpg')
        # Distinct staging paths avoid simultaneous requests writing one file.
        staging = self.thumbnails / (key+f'.{uuid.uuid4().hex}.jpg')
        cmd = ['ffmpeg','-v','error','-nostdin','-threads','1','-i',str(source),
               '-map_metadata','-1','-frames:v','1','-vf',"scale=640:640:force_original_aspect_ratio=decrease",
               '-threads','1','-q:v','4','-f','image2','-update','1','-n',str(staging)]
        try:
            result = subprocess.run(cmd,capture_output=True,timeout=35)
            if result.returncode or not staging.is_file() or not staging.stat().st_size:
                raise RuntimeError('Preview could not be generated')
            if verify: verify()
            if target.exists():
                # Retain the redundant staging artifact rather than overwriting.
                return target
            staging.rename(target)
            return target
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('Preview timed out') from exc


def handler(viewer, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            # Personal search queries and file identifiers do not belong in logs.
            pass

        def send(self, status, body, mime='application/json', cache=False):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            # Previews are addressed by content hash, so a cached copy can never go stale.
            self.send_header('Cache-Control','private, max-age=31536000, immutable' if cache else 'no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Cross-Origin-Resource-Policy','same-origin')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):
                pass

        def send_video(self,path):
            from .video import byte_range
            with path.open('rb') as stream:
                size=path.stat().st_size
                try:start,end,partial=byte_range(self.headers.get('Range'),size)
                except ValueError:
                    self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.send_header('Content-Length','0');self.end_headers();return
                self.send_response(206 if partial else 200)
                self.send_header('Content-Type','video/mp4');self.send_header('Content-Length',str(end-start+1))
                self.send_header('Accept-Ranges','bytes');self.send_header('Cache-Control','no-store')
                self.send_header('Cross-Origin-Resource-Policy','same-origin');self.send_header('X-Content-Type-Options','nosniff')
                if partial:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                self.end_headers();stream.seek(start);remaining=end-start+1
                try:
                    while remaining:
                        chunk=stream.read(min(1024*1024,remaining))
                        if not chunk:break
                        self.wfile.write(chunk);remaining-=len(chunk)
                except (BrokenPipeError,ConnectionResetError):pass

        def do_POST(self):
            current=viewer.snapshot() if hasattr(viewer,'snapshot') else viewer
            expected=(f'http://127.0.0.1:{port}',f'http://localhost:{port}')
            if self.headers.get('Host') not in (f'127.0.0.1:{port}',f'localhost:{port}') or self.headers.get('Origin') not in expected:
                self.send(403,b'{"error":"Local access only"}');return
            if self.path.startswith('/api/video/') and hasattr(current,'video_status'):
                try:
                    key=self.path.removeprefix('/api/video/')
                    if not re.fullmatch(r'[a-f0-9]{64}',key):raise ValueError()
                    self.send(200,json.dumps(current.video_status(key,start=True)).encode())
                except (ValueError,KeyError):self.send(400,b'{"error":"Unknown video"}')
                return
            if self.path=='/api/export-buckets' and hasattr(current,'organize'):
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=32768 or self.headers.get('Content-Type')!='application/json':raise ValueError()
                    value=json.loads(self.rfile.read(length))
                    if not isinstance(value,dict) or not isinstance(value.get('destination'),str) or not isinstance(value.get('buckets',[]),list):raise ValueError('Invalid export request')
                    if value.get('cancel') is True:EXPORTER.cancel();self.send(200,json.dumps(EXPORTER.snapshot()).encode());return
                    self.send(200,json.dumps(EXPORTER.start(current,value['destination'],[b for b in value.get('buckets',[]) if isinstance(b,str)],create_parents=value.get('create_parents') is True)).encode())
                except (ValueError,TypeError,KeyError) as exc:self.send(400,json.dumps({'error':str(exc) or 'Invalid export request'}).encode())
                return
            if self.path=='/api/gaps' and hasattr(current,'fill_gaps'):
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=262144 or self.headers.get('Content-Type')!='application/json':raise ValueError()
                    value=json.loads(self.rfile.read(length))
                    if not isinstance(value,dict) or not {'kind','group','action'}<=set(value)<={'kind','group','action','contents','skip'}:raise ValueError()
                    contents=value.get('contents');skip=value.get('skip',[])
                    if contents is not None and (not isinstance(contents,list) or any(not isinstance(d,str) for d in contents)):raise ValueError()
                    if not isinstance(skip,list) or any(not isinstance(d,str) for d in skip):raise ValueError()
                    result=current.fill_gaps(value['kind'],value['group'],value['action'],None if contents is None else set(contents),skip)
                    self.send(200,json.dumps({**result,'coverage':current.gaps(value['kind'])['coverage']}).encode())
                except (ValueError,TypeError,KeyError) as exc:self.send(400,json.dumps({'error':str(exc) or 'Invalid gap change'}).encode())
                except OSError:self.send(503,b'{"error":"Could not save. No success was recorded."}')
                return
            if self.path!='/api/organize' or not hasattr(current,'organize'):
                self.send(404,b'{"error":"Not found"}');return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=32768 or self.headers.get('Content-Type')!='application/json':raise ValueError()
                value=json.loads(self.rfile.read(length))
                if not isinstance(value,dict) or set(value)!={'op','data'} or not isinstance(value['data'],dict):raise ValueError()
                event=current.organize(value['op'],value['data'])
                self.send(200,json.dumps({'saved':True,'event':event['id'],'data':event['data']}).encode())
            except (ValueError,TypeError,KeyError):self.send(400,b'{"error":"Invalid organization change"}')
            except OSError:self.send(503,b'{"error":"Could not save. No success was recorded."}')

        def do_GET(self):
            current=viewer.snapshot() if hasattr(viewer,'snapshot') else viewer
            if self.headers.get('Host') not in (f'127.0.0.1:{port}',f'localhost:{port}'):
                self.send(403,b'{"error":"Local access only"}')
                return
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{port}',f'http://localhost:{port}'):
                self.send(403,b'{"error":"Local access only"}')
                return
            request = urlsplit(self.path)
            params = parse_qs(request.query)
            try:
                if request.path=='/':
                    self.send(200,(ROOT/'web/index.html').read_bytes(),'text/html; charset=utf-8')
                elif request.path=='/app.js':
                    self.send(200,(ROOT/'web/app.js').read_bytes(),'text/javascript; charset=utf-8')
                elif request.path=='/basemap.js':
                    self.send(200,(ROOT/'web/basemap.js').read_bytes(),'text/javascript; charset=utf-8')
                elif request.path.startswith('/basemap/') and request.path.endswith('.json'):
                    name=request.path[len('/basemap/'):-len('.json')]
                    if name in BASEMAP_LAYERS:self.send(200,(ROOT/'web/basemap'/(name+'.json')).read_bytes(),cache=True)
                    elif name=='detail':
                        detail=Path(getattr(current,'catalog_directory',ROOT))/'basemap-detail.json'
                        self.send(200,detail.read_bytes() if detail.is_file() else b'{"roads":[],"urban":[]}')
                    else:self.send(404,b'{"error":"Not found"}')
                elif request.path=='/api/gaps' and hasattr(current,'gaps'):
                    self.send(200,json.dumps(current.gaps(params.get('kind',['date'])[0],params.get('group',[''])[0],params.get('offset',['0'])[0],params.get('limit',['60'])[0])).encode())
                elif request.path=='/api/trip-paths' and hasattr(current,'trip_paths'):
                    self.send(200,json.dumps(current.trip_paths(archived=params.get('archived',['0'])[0]=='1')).encode())
                elif request.path in ('/vendor/leaflet/leaflet.js','/vendor/leaflet/leaflet.css'):
                    self.send(200,(ROOT/'web'/request.path.lstrip('/')).read_bytes(),'text/javascript; charset=utf-8' if request.path.endswith('.js') else 'text/css; charset=utf-8')
                elif request.path.startswith('/api/video/') and hasattr(current,'video_status'):
                    key=request.path.removeprefix('/api/video/')
                    self.send(200,json.dumps(current.video_status(key)).encode())
                elif request.path.startswith('/video/') and hasattr(current,'video_file'):
                    key=request.path.removeprefix('/video/')
                    self.send_video(current.video_file(key))
                elif request.path=='/api/summary':
                    self.send(200,json.dumps(current.summary()).encode())
                elif request.path=='/api/faces' and hasattr(current,'face_review'):
                    self.send(200,json.dumps(current.face_review(ignored=params.get('ignored',['0'])[0]=='1',sensitivity=params.get('sensitivity',['balanced'])[0])).encode())
                elif request.path.startswith('/face-preview/') and hasattr(current,'face_preview'):
                    key=request.path.removeprefix('/face-preview/')
                    if not re.fullmatch(r'[a-f0-9]{64}',key):raise FileNotFoundError()
                    self.send(200,current.face_preview(key).read_bytes(),'image/jpeg',cache=True)
                elif request.path.startswith('/api/person-suggestions/') and hasattr(current,'person_suggestions'):
                    self.send(200,json.dumps(current.person_suggestions(request.path.removeprefix('/api/person-suggestions/'))).encode())
                elif request.path.startswith('/api/photo-faces/') and hasattr(current,'photo_faces'):
                    key=request.path.removeprefix('/api/photo-faces/')
                    if key not in current.by_id:self.send(404,b'{"error":"Not found"}');return
                    self.send(200,json.dumps(current.photo_faces(key)).encode())
                elif request.path=='/api/export-status':
                    self.send(200,json.dumps(EXPORTER.snapshot()).encode())
                elif request.path.startswith('/api/item/') and hasattr(current,'item'):
                    key=request.path.removeprefix('/api/item/')
                    if key not in current.by_id:self.send(404,b'{"error":"Not found"}');return
                    self.send(200,json.dumps(current.item(key)).encode())
                elif request.path=='/api/stacks' and hasattr(current,'stacks'):
                    self.send(200,json.dumps(current.stacks()).encode())
                elif request.path=='/api/people' and hasattr(current,'people'):
                    self.send(200,json.dumps(current.people()).encode())
                elif request.path=='/api/buckets' and hasattr(current,'buckets'):
                    self.send(200,json.dumps(current.buckets(archived=params.get('archived',['0'])[0]=='1')).encode())
                elif request.path=='/api/albums' and hasattr(current,'albums'):
                    self.send(200,json.dumps(current.albums(archived=params.get('archived',['0'])[0]=='1')).encode())
                elif request.path.startswith('/api/album/') and hasattr(current,'album_items'):
                    self.send(200,json.dumps(current.album_items(request.path.removeprefix('/api/album/'))).encode())
                elif request.path=='/api/highlights' and hasattr(current,'highlights'):
                    result=current.highlights(mode=params.get('mode',['metadata'])[0],query=params.get('q',[''])[0],category=params.get('category',[''])[0],kind=params.get('kind',['all'])[0],year=params.get('year',[''])[0],after=params.get('after',[''])[0],before=params.get('before',[''])[0],person=params.get('person',[''])[0],place=params.get('place',[''])[0],trip=params.get('trip',[''])[0],limit=int(params.get('limit',['30'])[0]))
                    self.send(200,json.dumps(result).encode())
                elif request.path.startswith('/api/trip-exclusions/') and hasattr(current,'trip_exclusions'):
                    self.send(200,json.dumps(current.trip_exclusions(request.path.removeprefix('/api/trip-exclusions/'))).encode())
                elif request.path=='/api/trips' and hasattr(current,'trips'):
                    self.send(200,json.dumps(current.trips(archived=params.get('archived',['0'])[0]=='1')).encode())
                elif request.path=='/api/places' and hasattr(current,'places'):
                    self.send(200,json.dumps(current.places()).encode())
                elif request.path=='/api/progress':
                    self.send(200,json.dumps(current.progress()).encode())
                elif request.path.startswith('/api/metadata/'):
                    key=request.path.removeprefix('/api/metadata/')
                    if not re.fullmatch(r'[a-f0-9]{64}',key): raise FileNotFoundError()
                    self.send(200,json.dumps(current.metadata(key)).encode())
                elif request.path=='/api/suggest' and hasattr(current,'suggest'):
                    self.send(200,json.dumps(current.suggest(query=params.get('q',[''])[0],limit=int(params.get('limit',['6'])[0]))).encode())
                elif request.path=='/api/search' and hasattr(current,'everything'):
                    result=current.everything(query=params.get('q',[''])[0],limit=int(params.get('limit',['12'])[0]),year=params.get('year',[''])[0],after=params.get('after',[''])[0],before=params.get('before',[''])[0])
                    self.send(200,json.dumps(result).encode())
                elif request.path=='/api/timeline' and hasattr(current,'timeline'):
                    result=current.timeline(query=params.get('q',[''])[0],kind=params.get('kind',['all'])[0],year=params.get('year',[''])[0],after=params.get('after',[''])[0],before=params.get('before',[''])[0],near=params.get('near',[''])[0],person=params.get('person',[''])[0],place=params.get('place',[''])[0],trip=params.get('trip',[''])[0])
                    self.send(200,json.dumps(result).encode())
                elif request.path=='/api/items':
                    result = current.search(query=params.get('q',[''])[0],kind=params.get('kind',['all'])[0],year=params.get('year',[''])[0],offset=int(params.get('offset',['0'])[0]),limit=int(params.get('limit',['80'])[0]),after=params.get('after',[''])[0],before=params.get('before',[''])[0],near=params.get('near',[''])[0],**({'person':params.get('person',[''])[0],'place':params.get('place',[''])[0],'trip':params.get('trip',[''])[0],'stack':params.get('stack',[''])[0],'bucket':params.get('bucket',[''])[0]} if hasattr(current,'organization') else {}))
                    self.send(200,json.dumps(result).encode())
                elif request.path=='/api/video-moments' and hasattr(current,'video_moments'):
                    result=current.video_moments(query=params.get('q',[''])[0],year=params.get('year',[''])[0],after=params.get('after',[''])[0],before=params.get('before',[''])[0],offset=int(params.get('offset',['0'])[0]),limit=int(params.get('limit',['80'])[0]),person=params.get('person',[''])[0],place=params.get('place',[''])[0],trip=params.get('trip',[''])[0])
                    self.send(200,json.dumps(result).encode())
                elif request.path.startswith('/frame-preview/') and hasattr(current,'frame_preview'):
                    key=request.path.removeprefix('/frame-preview/')
                    if not re.fullmatch(r'[a-f0-9]{64}',key):raise FileNotFoundError()
                    self.send(200,current.frame_preview(key).read_bytes(),'image/jpeg',cache=True)
                elif request.path=='/api/moments' and hasattr(current,'moments'):
                    result=current.moments(query=params.get('q',[''])[0],year=params.get('year',[''])[0],after=params.get('after',[''])[0],before=params.get('before',[''])[0],offset=int(params.get('offset',['0'])[0]),limit=int(params.get('limit',['80'])[0]),person=params.get('person',[''])[0],place=params.get('place',[''])[0],trip=params.get('trip',[''])[0])
                    self.send(200,json.dumps(result).encode())
                elif request.path=='/api/described' and hasattr(current,'described'):
                    result=current.described(query=params.get('q',[''])[0],category=params.get('category',[''])[0],kind=params.get('kind',['all'])[0],year=params.get('year',[''])[0],after=params.get('after',[''])[0],before=params.get('before',[''])[0],offset=int(params.get('offset',['0'])[0]),limit=int(params.get('limit',['80'])[0]),person=params.get('person',[''])[0],place=params.get('place',[''])[0],trip=params.get('trip',[''])[0])
                    self.send(200,json.dumps(result).encode())
                elif request.path=='/api/visual':
                    if not hasattr(current,'visual'): raise RuntimeError('Visual search unavailable')
                    result=current.visual(query=params.get('q',[''])[0],kind=params.get('kind',['all'])[0],year=params.get('year',[''])[0],after=params.get('after',[''])[0],before=params.get('before',[''])[0],offset=int(params.get('offset',['0'])[0]),limit=int(params.get('limit',['80'])[0]),person=params.get('person',[''])[0],place=params.get('place',[''])[0],trip=params.get('trip',[''])[0])
                    self.send(200,json.dumps(result).encode())
                elif request.path=='/api/slideshow':
                    result=current.slideshow(query=params.get('q',[''])[0],year=params.get('year',[''])[0],after=params.get('after',[''])[0],before=params.get('before',[''])[0],near=params.get('near',[''])[0],located=params.get('located',['0'])[0]=='1',limit=int(params.get('limit',['500'])[0]),kind=params.get('kind',['all'])[0],**({'person':params.get('person',[''])[0],'place':params.get('place',[''])[0],'trip':params.get('trip',[''])[0],'bucket':params.get('bucket',[''])[0]} if hasattr(current,'organization') else {}))
                    self.send(200,json.dumps(result).encode())
                elif request.path.startswith('/preview/'):
                    key = request.path.removeprefix('/preview/')
                    if not re.fullmatch(r'[a-f0-9]{64}',key): raise FileNotFoundError()
                    self.send(200,current.thumbnail(key).read_bytes(),'image/jpeg',cache=True)
                else:
                    self.send(404,b'{"error":"Not found"}')
            except (FileNotFoundError,KeyError):
                self.send(404,b'{"error":"Source unavailable or changed. Refresh the catalog."}')
            except (ValueError,TypeError):
                self.send(400,b'{"error":"Invalid request"}')
            except (OSError,RuntimeError):
                self.send(503,b'{"error":"Local operation unavailable or busy"}')
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=ROOT/'.catalog/catalog.db')
    parser.add_argument('--port',type=int,default=8770)
    parser.add_argument('--imports',type=Path,help='Use verified source occurrences and connected metadata')
    parser.add_argument('--vision-model',type=Path,help='Enable offline image search with locally installed weights')
    parser.add_argument('--no-warm-previews',action='store_true',help='Do not generate missing previews in the background while the viewer runs')
    parser.add_argument('--organization',type=Path,help='Owner corrections log to read and append (default corrections/organization.jsonl); point a rehearsal viewer at a copy so nothing lands in the real log')
    args = parser.parse_args()
    if args.port not in range(1024,65536): parser.error('Choose a port between 1024 and 65535')
    if args.imports:
        from .library import Library
        encoder=None
        if args.vision_model:
            from .vision import Siglip
            encoder=Siglip(args.vision_model)
        viewer=Library(args.database,args.imports,encoder=encoder,organization=args.organization)
        if encoder is not None and viewer.visual_database.is_file():
            from .vision import SearchIndex
            viewer.visual_index=SearchIndex(viewer.visual_database,encoder)
            viewer.visual_index.search('a photo',limit=1,allowed=set())
    else:
        if args.vision_model: parser.error('--vision-model requires --imports')
        if args.organization: parser.error('--organization requires --imports')
        viewer = Viewer(args.database)
    server = ThreadingHTTPServer(('127.0.0.1',args.port),handler(viewer,args.port))
    print(f'Media Vault: http://127.0.0.1:{args.port} ({len(viewer.items)} indexed files)',flush=True)
    if args.imports and not args.no_warm_previews:
        from .previews import Warmer
        Warmer(viewer).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__=='__main__':
    main()
