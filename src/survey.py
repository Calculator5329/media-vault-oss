"""Bounded read-only metadata coverage; outputs counts, never coordinates or filenames."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

from . import probe


def sidecar_paths(path):
    return [Path(str(path)+".json"), Path(str(path)+".supplemental-metadata.json")]


def shape(path):
    kind=probe.kind_for(path)
    if kind=="photo":
        origin="pixel-named" if path.name.upper().startswith("PXL_") else "other-photo"
        if any(p.is_file() for p in sidecar_paths(path)): origin="sidecar-associated"
    else: origin=kind
    return origin+":"+path.suffix.lower()


def sidecar_presence(data):
    if not isinstance(data,dict): return {}
    result={"sidecar_json":1}
    taken=data.get("photoTakenTime")
    if isinstance(taken,dict) and taken.get("timestamp") is not None: result["sidecar_taken_at"]=1
    for name in ("geoDataExif","geoData"):
        gps=data.get(name)
        if isinstance(gps,dict) and isinstance(gps.get("latitude"),(int,float)) and isinstance(gps.get("longitude"),(int,float)):
            # Google's zero/zero placeholder is not evidence of a location.
            if (gps["latitude"],gps["longitude"])!=(0,0): result["sidecar_gps"]=1
    return result


def inspect(path):
    before=path.stat(); fields=Counter()
    if probe.kind_for(path)=="photo":
        props=probe.exif_properties(path)
        if props: fields["exif_any"]=1
        else: fields["exif_empty_or_probe_failed"]=1
        for key in ("DateTimeOriginal","DateTimeDigitized","DateTime","GPSLatitude","GPSLongitude","Make","Model","Orientation","OffsetTimeOriginal"):
            if props.get(key): fields["exif_"+key]=1
        if "pixel" in props.get("Model","").lower(): fields["exif_pixel_model"]=1
        stamp=props.get("DateTimeOriginal") or props.get("DateTimeDigitized") or props.get("DateTime")
        if probe.parse_exif_datetime(stamp): fields["probe_taken_at"]=1
        try:
            if probe.parse_gps(props) is not None: fields["probe_gps"]=1
        except (ValueError,OverflowError,ZeroDivisionError): fields["gps_parse_error"]=1
    elif probe.kind_for(path)=="video":
        try:
            data=probe.ffprobe_json(path);fields["ffprobe_readable"]=1
            if probe.video_creation_time(data): fields["probe_taken_at"]=1
            tags=[(data.get("format") or {}).get("tags") or {}]+[s.get("tags") or {} for s in data.get("streams",[])]
            if any(any("location" in key.lower() for key in t) for t in tags):fields["container_location_tag"]=1
        except probe.ToolError: fields["ffprobe_failed"]=1
    for sidecar in sidecar_paths(path):
        if sidecar.is_file() and not sidecar.is_symlink():
            if sidecar.stat().st_size>8*1024*1024: fields["sidecar_oversized"]=1;continue
            try: fields.update(sidecar_presence(json.loads(sidecar.read_text())))
            except (OSError,ValueError,UnicodeError):fields["sidecar_unreadable"]=1
            break
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns): fields["source_changed_during_probe"]=1
    return dict(fields)


def survey(sources, per_shape=32, max_seconds=600, workers=4):
    if per_shape<1 or workers<1 or workers>8 or max_seconds<=0:raise ValueError("positive bounded sample/runtime and 1–8 workers required")
    started=time.monotonic(); groups=defaultdict(list); census=[]; seen=set()
    for i,root in enumerate(sources):
        root=Path(root).expanduser().resolve(); extensions=Counter();size=0;errors=0
        if not root.is_dir(): census.append({"source_index":i,"status":"missing"});continue
        for base,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                path=Path(base)/name
                if name.startswith(".") or path.is_symlink() or not path.is_file():continue
                if path in seen:continue
                seen.add(path)
                try: size+=path.stat().st_size
                except OSError:errors+=1;continue
                extensions[path.suffix.lower()]+=1
                if probe.kind_for(path) in ("photo","video"):groups[(i,shape(path))].append(path)
        census.append({"source_index":i,"status":"present","files":sum(extensions.values()),"bytes":size,"extensions":dict(extensions),"stat_errors":errors})
    selected=[]; coverage={}
    for key,paths in sorted(groups.items()):
        paths.sort(key=lambda p:hashlib.sha256(str(p).encode()).hexdigest())
        sample=paths[:per_shape];selected.extend((key,p) for p in sample)
        coverage[key]={"source_index":key[0],"shape":key[1],"population":len(paths),"selected":len(sample),"probed":0,"fields":Counter()}
    probe.TOOL_TIMEOUT_S=min(30,max_seconds)
    processed=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0,len(selected),workers):
            if time.monotonic()-started>=max_seconds:break
            batch=selected[start:start+workers]
            futures=[pool.submit(inspect,p) for _,p in batch]
            for (key,path),future in zip(batch,futures):
                try:fields=future.result()
                except OSError:fields={"file_read_error":1}
                coverage[key]["probed"]+=1;coverage[key]["fields"].update(fields);processed.append(str(path))
    return {"generated_at":datetime.now(timezone.utc).isoformat(),"elapsed_seconds":round(time.monotonic()-started,3),"limits":{"per_shape":per_shape,"max_seconds":max_seconds,"workers":workers,"per_tool_seconds":probe.TOOL_TIMEOUT_S},"census":census,"coverage":[{**v,"fields":dict(v["fields"])} for v in coverage.values()],"probed_path_manifest_sha256":hashlib.sha256(json.dumps(processed).encode()).hexdigest(),"tools":{"identify":probe.tool_version("identify"),"ffprobe":probe.tool_version("ffprobe")},"notes":["Per-shape deterministic SHA256(path) sample; not a random population estimate.","EXIF empty and probe failure are conflated by the existing probe and are reported together.","Pixel-named is a filename shape; exif_pixel_model is separate confirmation.","Sidecar matching checks exact media filename plus .json or .supplemental-metadata.json; truncated-title association is not implemented.","Probe timestamps are syntactic parser results, not verified camera-clock accuracy.","Only metadata was read; no thumbnails, embeddings or image bytes were exported."]}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--config",type=Path,default=Path("vault.config.json"));p.add_argument("--output",type=Path,required=True);p.add_argument("--per-shape",type=int,default=32);p.add_argument("--max-seconds",type=float,default=600);p.add_argument("--workers",type=int,default=4);a=p.parse_args()
    sources=[Path(s).expanduser().resolve() for s in json.loads(a.config.read_text())["sources"]]
    out=a.output.resolve()
    if any(out==s or s in out.parents for s in sources):p.error("output must be outside every read-only source")
    if out.exists():p.error("retain existing survey evidence; choose a new output path")
    result=survey(sources,a.per_shape,a.max_seconds,a.workers);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2));out.chmod(0o600)
    print(json.dumps({"output":str(out),"files":sum(s.get("files",0) for s in result["census"]),"probed":sum(s["probed"] for s in result["coverage"]),"elapsed_seconds":result["elapsed_seconds"]}))

if __name__=="__main__":main()
