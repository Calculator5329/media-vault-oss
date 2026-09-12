"""Strict, attributable JPEG derivatives; never replace originals or model facts."""
from contextlib import closing
import hashlib,json,shutil,sqlite3,subprocess,time,uuid
from pathlib import Path
from .imports import database,now
from .kit import create_fact_table,insert_fact
from .portable import assert_local_state
from .video import verified_copy
from .vision import MAX_IMAGE_BYTES

POLICY='strict-jpeg-1'


def checksum(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


class StrictJPEG:
    def __init__(self):
        import PIL
        self.programs={name:Path(shutil.which(name)).resolve(strict=True) for name in ('ffmpeg','ffprobe')}
        self.contract={'policy':POLICY,'executables':{name:checksum(path) for name,path in self.programs.items()},'versions':{name:subprocess.run([str(path),'-version'],capture_output=True,check=True,timeout=10,text=True).stdout.strip() for name,path in self.programs.items()},'pillow':PIL.__version__,'settings':'-xerror -err_detect explode; first video frame; native size; PNG rgb24; 2 threads; 30s','orientation':'FFmpeg autorotate; no additional EXIF transform','max_pixels':80000000,'max_source_bytes':MAX_IMAGE_BYTES}
        self.identity=POLICY+':'+hashlib.sha256(json.dumps(self.contract,sort_keys=True).encode()).hexdigest()

    def decode(self,source,output):
        from PIL import Image
        with source.open('rb') as stream:
            if stream.read(2)!=b'\xff\xd8':raise ValueError('Not a JPEG source')
        probe=subprocess.run([str(self.programs['ffprobe']),'-v','error','-protocol_whitelist','file,pipe','-show_entries','stream=codec_name,width,height:stream_side_data','-of','json',str(source)],capture_output=True,timeout=15,check=True)
        if probe.stderr.strip():raise ValueError('Probe reported errors')
        streams=json.loads(probe.stdout).get('streams',[])
        if len(streams)!=1 or streams[0].get('codec_name')!='mjpeg':raise ValueError('Not a single JPEG image')
        shape=streams[0];width,height=shape['width'],shape['height']
        if not 0<width*height<=self.contract['max_pixels']:raise ValueError('Pixel bound')
        orientation=None
        try:
            with Image.open(source) as image:orientation=image.getexif().get(274)
        except (OSError,ValueError):pass
        result=subprocess.run([str(self.programs['ffmpeg']),'-v','error','-xerror','-err_detect','explode','-nostdin','-threads','2','-protocol_whitelist','file,pipe','-autorotate','-i',str(source),'-map','0:v:0','-frames:v','1','-map_metadata','-1','-pix_fmt','rgb24','-threads','2','-n',str(output)],capture_output=True,timeout=30,check=True)
        if result.stderr.strip():raise ValueError('Decoder reported errors')
        with Image.open(output) as image:
            image.load()
            if image.format!='PNG' or image.mode!='RGB' or image.size not in ((width,height),(height,width)):raise ValueError('Unexpected derivative shape')
            dimensions=list(image.size)
        return {'width':dimensions[0],'height':dimensions[1],'source_dimensions':[width,height],'raw_exif_orientation':orientation,'reported_side_data':shape.get('side_data_list',[]),'orientation_policy':self.contract['orientation']}


def prepare(row,root,decoder):
    root=Path(root).resolve()
    assert_local_state(root,'Recovery')
    if not 0<row['size']<=MAX_IMAGE_BYTES:raise ValueError('Source byte bound')
    root.mkdir(parents=True,exist_ok=True)
    source=verified_copy(row,root/'sources')
    attempt=root/(row['content_hash']+'.'+uuid.uuid4().hex);attempt.mkdir()
    output=attempt/'image.png';receipt=attempt/'receipt.json'
    record={'state':'running','content_hash':row['content_hash'],'recipe':decoder.identity,'contract':decoder.contract,'meaning':'Strict decoder completed; not a visual restoration or color fidelity judgment','derived_at':now()}
    receipt.write_text(json.dumps(record,sort_keys=True)+'\n')
    try:
        details=decoder.decode(source,output)
        record.update(state='complete',artifact_sha256=checksum(output),artifact_bytes=output.stat().st_size,**details)
    except Exception as exc:
        record.update(state='error',error=type(exc).__name__);receipt.write_text(json.dumps(record,sort_keys=True)+'\n');raise
    receipt.write_text(json.dumps(record,sort_keys=True)+'\n')
    return output,record


def index(import_database,quality_database,output,decoder,seconds=60,limit=10):
    source=Path(import_database).resolve(strict=True);quality=Path(quality_database).resolve(strict=True);output=Path(output).resolve()
    if output in (source,quality):raise ValueError('Recovery store must be separate')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row;roots=json.loads(c.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0]);rows=[dict(r) for r in c.execute("SELECT * FROM occurrences WHERE present=1 AND kind='photo' AND content_hash IS NOT NULL ORDER BY member!='',source,offset")]
    with closing(sqlite3.connect(quality.as_uri()+'?mode=ro',uri=True)) as q:
        model=q.execute("SELECT value FROM settings WHERE key='quality_current'").fetchone()[0]
        failed={r[0]:r[1] for r in q.execute("SELECT content_hash,error FROM quality_work WHERE model=? AND status='error' AND error IN ('OSError','UnidentifiedImageError')",(model,))}
    candidates={}
    for row in rows:
        if row['content_hash'] in failed:candidates.setdefault(row['content_hash'],row)
    begin=time.monotonic();processed=0
    with database(output,roots) as c:
        create_fact_table(c,'image_recovery_facts',{'content_hash':'TEXT NOT NULL','recipe':'TEXT NOT NULL','artifact_path':'TEXT NOT NULL','artifact_sha256':'TEXT NOT NULL','width':'INTEGER NOT NULL','height':'INTEGER NOT NULL','details_json':'TEXT NOT NULL'})
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS recovery_identity ON image_recovery_facts(content_hash,recipe)')
        c.execute('CREATE TABLE IF NOT EXISTS image_recovery_work(content_hash TEXT,recipe TEXT,status TEXT,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,recipe))')
        with c:c.execute("UPDATE image_recovery_work SET status='error',error='InterruptedAttempt' WHERE recipe=? AND status='running'",(decoder.identity,))
        done={r[0] for r in c.execute('SELECT content_hash FROM image_recovery_work WHERE recipe=?',(decoder.identity,))}
        for digest,row in candidates.items():
            if digest in done:continue
            if processed>=limit or time.monotonic()-begin>=seconds:break
            with c:c.execute('INSERT INTO image_recovery_work VALUES(?,?,?,?,?)',(digest,decoder.identity,'running',None,now()))
            try:
                artifact,record=prepare(row,output.parent/'image-recovery',decoder)
                with c:
                    insert_fact(c,'image_recovery_facts',{'content_hash':digest,'recipe':decoder.identity,'artifact_path':str(artifact),'artifact_sha256':record['artifact_sha256'],'width':record['width'],'height':record['height'],'details_json':json.dumps(record),'source_path':row['source'],'source_span':json.dumps({'member':row['member'],'offset':row['offset'],'original_error':failed[digest],'quality_model':model,'recovery_recipe':decoder.identity}),'extractor':'ffmpeg-strict-jpeg','extractor_version':POLICY,'confidence':1.0,'derived_at':now(),'tier':'personal'})
                    c.execute("UPDATE image_recovery_work SET status='complete',error=NULL,derived_at=? WHERE content_hash=? AND recipe=?",(now(),digest,decoder.identity))
            except Exception as exc:
                with c:c.execute("UPDATE image_recovery_work SET status='error',error=?,derived_at=? WHERE content_hash=? AND recipe=?",(type(exc).__name__,now(),digest,decoder.identity))
            processed+=1;done.add(digest)
        return {'processed_this_run':processed,'remaining':len(set(candidates)-done),'statuses':dict(c.execute('SELECT status,count(*) FROM image_recovery_work WHERE recipe=? GROUP BY status',(decoder.identity,))),'recipe':decoder.identity}


def load(database_path,digest):
    """Return verified pixels and their recovery fact, or None when not recovered."""
    import io
    from PIL import Image
    database_path=Path(database_path).resolve()
    if not database_path.is_file():return None
    with closing(sqlite3.connect(database_path.as_uri()+'?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row
        row=c.execute("SELECT f.* FROM image_recovery_facts f JOIN image_recovery_work w ON w.content_hash=f.content_hash AND w.recipe=f.recipe WHERE f.content_hash=? AND w.status='complete' AND f.recipe LIKE ? ORDER BY f.derived_at DESC LIMIT 1",(digest,POLICY+':%')).fetchone()
    if row is None:return None
    fact=dict(row);record=json.loads(fact['details_json']);contract=record['contract']
    expected=POLICY+':'+hashlib.sha256(json.dumps(contract,sort_keys=True).encode()).hexdigest()
    if contract.get('policy')!=POLICY or fact['recipe']!=expected or record['recipe']!=expected or record['content_hash']!=digest or record['state']!='complete' or record['artifact_sha256']!=fact['artifact_sha256']:raise ValueError('Recovery identity mismatch')
    path=Path(fact['artifact_path']);root=database_path.parent/'image-recovery'
    if not path.is_absolute() or path.resolve(strict=True)!=path or root not in path.parents:raise ValueError('Recovery path outside artifact store')
    with path.open('rb') as stream:raw=stream.read(256*1024**2+1)
    if len(raw)>256*1024**2 or len(raw)!=record['artifact_bytes'] or hashlib.sha256(raw).hexdigest()!=fact['artifact_sha256']:raise ValueError('Recovery artifact checksum mismatch')
    with Image.open(io.BytesIO(raw)) as image:
        if image.format!='PNG' or image.mode!='RGB' or not 0<image.width*image.height<=80000000 or image.size!=(fact['width'],fact['height']) or image.size!=(record['width'],record['height']):raise ValueError('Recovery pixel shape mismatch')
        image.load();pixels=image.copy()
    return pixels,fact
