"""Bounded video gap decoding. Caller verifies source before publishing results."""
import hashlib,json,math,re,shutil,time,uuid


def validate(plan,start_index,limit,seconds):
    if isinstance(start_index,bool) or not isinstance(start_index,int) or start_index<0:raise ValueError('Invalid resume index')
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=64:raise ValueError('Choose 1 through 64 requests')
    if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or not 0<seconds<=120:raise ValueError('Choose at most120 seconds')
    if plan.get('status')!='planned' or not re.fullmatch('[a-f0-9]{64}',plan.get('revision','')):raise ValueError('A bounded gap plan is required')
    targets=plan.get('targets');duration=plan.get('duration_seconds')
    if not isinstance(duration,(int,float)) or isinstance(duration,bool) or not math.isfinite(duration) or not 0<duration<=21600:raise ValueError('Invalid duration')
    if not isinstance(targets,list) or not 1<=len(targets)<=1024 or plan.get('requested_count')!=len(targets) or start_index>len(targets):raise ValueError('Invalid target count')
    previous=-1
    for target in targets:
        if isinstance(target,bool) or not isinstance(target,(int,float)) or not math.isfinite(target) or not previous<target<duration or target<=0:raise ValueError('Targets must increase within the video')
        previous=target


class GapDecoder:
    def __init__(self):
        import av
        self.av=av
        policy={'version':'gap-decode-1','av':av.__version__,'libraries':av.library_versions,'max_side':640,'lateness_seconds':1,'pixels':80000000,'threads':2,'seek':'backward-keyframe-then-decode','max_decoded_frames':100000,'per_request_seconds':15}
        self.identity='gap-decoder:'+hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()

    def decode(self,stream,root,digest,plan,start_index=0,limit=64,seconds=120):
        validate(plan,start_index,limit,seconds)
        if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Verified content hash required')
        root.mkdir(parents=True,exist_ok=True)
        started=time.monotonic();deadline=started+seconds;outcomes=[];frames=[];decoded=0;cursor=start_index
        with self.av.open(stream,metadata_errors='surrogateescape') as container:
            if not container.streams.video:raise ValueError('No video stream')
            video=container.streams.video[0];video.thread_count=2;origin=(container.start_time or 0)/self.av.time_base
            for index in range(start_index,min(len(plan['targets']),start_index+limit)):
                if time.monotonic()>=deadline or decoded>=100000:break
                target=plan['targets'][index];request_deadline=min(deadline,time.monotonic()+15)
                try:
                    if shutil.disk_usage(root).free<12*1024**3:raise RuntimeError('Low Linux cache space')
                    container.seek(int((target+origin)/video.time_base),stream=video,backward=True,any_frame=False)
                    selected=None
                    for frame in container.decode(video):
                        decoded+=1
                        if decoded>100000 or time.monotonic()>=request_deadline:raise TimeoutError('Decode budget reached')
                        if frame.width*frame.height>80000000:raise ValueError('Frame dimensions exceed limit')
                        if frame.time is None:continue
                        actual=float(frame.time)-origin
                        if not math.isfinite(actual) or actual<0:raise ValueError('Invalid frame timestamp')
                        if actual<target:continue
                        if actual>target+1 or actual>plan['duration_seconds']:raise ValueError('No sufficiently close frame')
                        selected=frame;break
                    if selected is None:raise ValueError('No frame at requested position')
                    key=hashlib.sha256(f'{digest}:{plan["revision"]}:{self.identity}:{selected.pts}:{video.time_base}'.encode()).hexdigest()
                    filename=key+'.'+uuid.uuid4().hex+'.jpg';path=root/filename
                    scale=min(1,640/max(selected.width,selected.height));width=max(2,int(selected.width*scale)//2*2);height=max(2,int(selected.height*scale)//2*2)
                    resized=selected.reformat(width=width,height=height,format='yuvj420p');pts=selected.pts;time_base=str(video.time_base)
                    with self.av.open(str(path),mode='w',format='image2') as output:
                        encoder=output.add_stream('mjpeg');encoder.width=width;encoder.height=height;encoder.pix_fmt='yuvj420p';encoder.thread_count=1;resized.pts=None
                        for packet in encoder.encode(resized):output.mux(packet)
                        for packet in encoder.encode():output.mux(packet)
                    with path.open('rb') as image:sha=hashlib.file_digest(image,'sha256').hexdigest()
                    frames.append({'frame_id':key,'timestamp':actual,'requested_timestamp':target,'request_index':index,'filename':filename,'image_hash':sha,'decoder':self.identity,'plan_revision':plan['revision'],'pts':pts,'time_base':time_base,'duration':plan['duration_seconds']})
                    outcomes.append({'request_index':index,'status':'complete','frame_id':key})
                except Exception as exc:
                    outcomes.append({'request_index':index,'status':'error','error':type(exc).__name__})
                cursor=index+1
        return {'frames':frames,'outcomes':outcomes,'next_index':cursor,'remaining':len(plan['targets'])-cursor,'decoded_frames':decoded}
