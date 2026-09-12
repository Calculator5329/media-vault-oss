"""Recover video containers mislabeled as photos, with retained byte evidence."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import time
import zipfile
from .imports import _source_stat,now
from .kit import create_fact_table,insert_fact
from .video import verified_copy

VIDEO_BRANDS={b'isom',b'iso2',b'iso4',b'iso5',b'iso6',b'mp41',b'mp42',b'M4V ',b'qt  ',b'3gp4',b'3gp5',b'3gp6'}


def header(row):
    _source_stat(row)
    if row['member']:
        with zipfile.ZipFile(row['source']) as archive:
            info=next(i for i in archive.infolist() if i.header_offset==row['offset'])
            if (info.filename,info.file_size,info.CRC)!=(row['member'],row['size'],row['crc']):raise ValueError('ZIP member changed')
            with archive.open(info) as stream:return stream.read(12)
    with Path(row['source']).open('rb') as stream:return stream.read(12)


def facts(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='media_type_facts'").fetchone():return {}
    return {r['content_hash']:dict(r) for r in conn.execute('SELECT * FROM media_type_facts')}


def apply(conn):
    for digest,fact in facts(conn).items():
        conn.execute('UPDATE occurrences SET kind=? WHERE content_hash=?',(fact['detected_kind'],digest))


def recover(conn,vision_database,cache,seconds=30,limit=10):
    path=Path(vision_database).resolve();count=0;examined=0;deadline=time.monotonic()+seconds
    if not path.is_file():return {'reclassified':0,'examined':0}
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as failures:
        candidates={r[0] for r in failures.execute('SELECT content_hash FROM visual_errors')}
    create_fact_table(conn,'media_type_facts',{'content_hash':'TEXT NOT NULL','original_kind':'TEXT NOT NULL','detected_kind':'TEXT NOT NULL','format_json':'TEXT NOT NULL'})
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS media_type_identity ON media_type_facts(content_hash)')
    known=facts(conn)
    for digest in sorted(candidates-set(known)):
        if count>=limit or time.monotonic()>deadline:break
        rows=conn.execute("SELECT * FROM occurrences WHERE content_hash=? AND present=1 AND kind='photo' ORDER BY member!=''",(digest,)).fetchall()
        for row in rows:
            try:
                prefix=header(row)
                if len(prefix)<12 or prefix[4:8]!=b'ftyp' or prefix[8:12] not in VIDEO_BRANDS:continue
                examined+=1
                source=verified_copy(row,cache)
                result=subprocess.run(['ffprobe','-v','error','-protocol_whitelist','file','-show_entries','format=format_name,duration:stream=codec_type,codec_name,width,height,duration','-of','json',str(source)],capture_output=True,timeout=30,check=True)
                probe=json.loads(result.stdout)
                if not any(s.get('codec_type')=='video' and s.get('width',0)>0 and s.get('height',0)>0 for s in probe.get('streams',[])):continue
                if not any(k in probe.get('format',{}).get('format_name','').split(',') for k in ('mov','mp4','3gp')):continue
                with conn:
                    insert_fact(conn,'media_type_facts',{'content_hash':digest,'original_kind':'photo','detected_kind':'video','format_json':json.dumps(probe),'source_path':row['source'],'source_span':json.dumps({'member':row['member'],'offset':row['offset']}),'extractor':'verified-container-type','extractor_version':'1','confidence':1.0,'derived_at':now(),'tier':'personal'})
                    conn.execute("UPDATE occurrences SET kind='video' WHERE content_hash=?",(digest,))
                count+=1;break
            except (OSError,ValueError,RuntimeError,StopIteration,zipfile.BadZipFile,subprocess.SubprocessError):continue
    return {'reclassified':count,'examined':examined}
