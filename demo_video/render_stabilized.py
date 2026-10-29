"""Stable 1080p edit. Preserves original captures, renderer and delivery MP4.
Run: .\\.venv\\Scripts\\python.exe demo_video/render_stabilized.py
Fractional affine sampling avoids zoompan's integer crop-position jitter.
"""
from pathlib import Path
import subprocess,json,math,sys,hashlib
from PIL import Image,ImageDraw
P=Path(__file__).resolve().parent;R=P/'stabilization';R.mkdir(exist_ok=True)
FF=next((P/'tools/imageio_ffmpeg/binaries').glob('*.exe'))
SCENES=json.loads((P/'production.json').read_text());FPS=30;SIZE=(1920,1080);INNER=(1728,972);NAVY='#142239'
SHOTS={}
for f,src in [('shots.json','capture.webm'),('shots-extra.json','capture-extra.webm'),('shots-planner.json','capture-planner.webm')]:
 for s in json.loads((P/f).read_text()):SHOTS[s['name']]={**s,'src':src}
# Actual stable viewport positions before and after the recorded stepped scrolls.
# Preserve content and avoid pretending a stabilized browser scroll is new footage.
DETAIL={'finance':[(38.1,1.0,6),(53.0,2.0,13)],'business':[(8.2,1.0,7),(28.0,2.0,18)],'compare':[(62.2,1.0,7),(76.0,2.0,13)]}
def smooth(t):
 q=max(0.,min(1.,t));return q*q*q*(q*(q*6-15)+10)
def zoom(t,duration):
 # Two-second stable holds and a single six-second eased push/pull.
 ramp=min(4.,(duration-6)/2)
 return 1+.035*smooth((t-2)/ramp)*(1-smooth((t-(duration-2-ramp))/ramp))
def decoded(src,start,length,duration):
 cmd=[str(FF),'-v','error','-ss',str(start),'-t',str(length),'-i',str(P/src),'-vf',f'setpts=PTS-STARTPTS,fps=30:start_time=0,tpad=stop_mode=clone:stop_duration={duration+1}', '-frames:v',str(round(duration*FPS)),'-f','rawvideo','-pix_fmt','rgb24','-']
 with subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as proc:
  count=round(duration*FPS)
  for i in range(count):
   data=proc.stdout.read(1920*1080*3)
   if len(data)!=1920*1080*3:raise RuntimeError(f'Incomplete source frame {i} of {count} in {src}: {proc.stderr.read().decode()}')
   yield Image.frombytes('RGB',SIZE,data)
  proc.stdout.close();err=proc.stderr.read();code=proc.wait()
  if code:raise RuntimeError(err.decode())
def complete(path,duration):
 if not path.exists():return False
 result=subprocess.run([str(FF),'-hide_banner','-i',str(path)],capture_output=True,text=True)
 import re
 m=re.search(r'Duration: (\d+):(\d+):([\d.]+)',result.stderr)
 return bool(m and abs(sum(float(v)*s for v,s in zip(m.groups(),[3600,60,1]))-duration)<.02)
def source_frames(s):
 name=s['name'];d=s['duration']
 if name in DETAIL:
  for start,length,hold in DETAIL[name]:yield from decoded('capture-detail.webm',start,length,hold)
 elif name=='pdf':
  im=Image.open(P/'pdf-composition.png').convert('RGB')
  for _ in range(d*FPS):yield im
 else:
  sh=SHOTS[name];yield from decoded(sh['src'],sh['start']+.1,sh['end']-sh['start']-.3,d)
def title_frames(s):
 name=s['name'];base=Image.open(P/(name+'-base.png')).convert('RGB')
 layers=[Image.open(P/(name+'-logo.png')).convert('RGBA')]+[Image.open(P/f'{name}-text-{i}.png').convert('RGBA') for i in range(3)]
 starts=[.3,2.5,4.,6.] if name=='opening' else [.3,1.5,3.5,7.]
 for i in range(s['duration']*FPS):
  t=i/FPS;im=base.copy()
  for layer,st in zip(layers,starts):
   alpha=smooth((t-st)/1.1)
   if alpha:im.paste(layer,(0,0),layer.getchannel('A').point(lambda v:round(v*alpha)))
  yield im
manifest={'version':1,'output':'SIGNIX_Product_Demo_Stabilized.mp4','fps':30,'resolution':list(SIZE),'source_fps':25,'intermediate_crf':17,'final_crf':18,'preset':'slow','source_retiming':False,'motion':'Fixed interactive footage; fractional RGB bicubic affine PDF push/hold/pull, quintic easing','detail_edit':DETAIL,'original_sha256':hashlib.sha256((P/'SIGNIX_Product_Demo.mp4').read_bytes()).hexdigest()}
(R/'settings.json').write_text(json.dumps(manifest,indent=2))
for idx,s in enumerate(SCENES):
 name=s['name'];d=s['duration'];dest=R/f'{idx:02d}-{name}.mp4'
 if complete(dest,d) and '--force' not in sys.argv:print('Preserved',dest.name,flush=True);continue
 print('Rendering stable',name,flush=True);tmp=dest.with_suffix('.partial.mp4')
 title=name in ['opening','closing'];frames=title_frames(s) if title else source_frames(s)
 overlay=None if title else Image.open(P/(name+'-overlay.png')).convert('RGBA')
 with (R/(name+'.log')).open('w') as log:
  enc=subprocess.Popen([str(FF),'-y','-v','warning','-f','rawvideo','-pix_fmt','rgb24','-s','1920x1080','-r','30','-i','-','-an','-c:v','libx264','-preset','slow','-crf','17','-pix_fmt','yuv420p','-threads','4','-fps_mode','cfr',str(tmp)],stdin=subprocess.PIPE,stderr=log)
  try:
   for i,frame in enumerate(frames):
    t=i/FPS
    if not title:
     z=zoom(t,d) if name=='pdf' else 1.
     if z==1:inside=frame.resize(INNER,Image.Resampling.LANCZOS)
     else:
      # One fractional RGB resampling, no integer crop or YUV chroma snapping.
      factor=1920/(1728*z);x=(1920-1920/z)*.68;y=(1080-1080/z)*.55
      inside=frame.transform(INNER,Image.Transform.AFFINE,(factor,0,x,0,factor,y),Image.Resampling.BICUBIC)
     out=Image.new('RGB',SIZE,NAVY);out.paste(inside,(96,54))
     a=smooth((t-.1)/.6);out.paste(overlay,(0,0),overlay.getchannel('A').point(lambda v:round(v*a)))
     ImageDraw.Draw(out).line((96,1045,1824,1045),fill='#EEA02B',width=2)
    else:out=frame
    fade=min(1,t/.3,(d-1/FPS-t)/.3)
    if name in DETAIL:
     cut=DETAIL[name][0][2]
     if cut-.2<t<cut+.2:fade=min(fade,abs(t-cut)/.2)
    if fade<1:out=Image.blend(Image.new('RGB',SIZE,'black'),out,max(0,fade))
    enc.stdin.write(out.tobytes())
    if i and i%300==0:print(f'  {name}: {i//FPS}/{d}s',flush=True)
  finally:enc.stdin.close()
  if enc.wait():raise RuntimeError(f'Encoder failed: {name}. See log.')
 tmp.replace(dest)
(R/'concat.txt').write_text(''.join(f"file '{i:02d}-{s['name']}.mp4'\n" for i,s in enumerate(SCENES)))
print('Assembling stabilized delivery',flush=True)
# Copy the already validated original AAC stream bit-for-bit: same duration and mix.
tmp=P/'SIGNIX_Product_Demo_Stabilized.rendering.mp4'
with (R/'final.log').open('w') as log:
 subprocess.run([str(FF),'-y','-v','warning','-f','concat','-safe','0','-i',str(R/'concat.txt'),'-i',str(P/'SIGNIX_Product_Demo.mp4'),'-map','0:v:0','-map','1:a:0','-vf','ass=subtitles.ass','-c:v','libx264','-preset','slow','-crf','18','-pix_fmt','yuv420p','-fps_mode','cfr','-r','30','-c:a','copy','-t',str(sum(s['duration'] for s in SCENES)),'-movflags','+faststart','-threads','4',str(tmp)],cwd=P,stdout=log,stderr=log,check=True)
tmp.replace(P/'SIGNIX_Product_Demo_Stabilized.mp4')
print('FINISHED SIGNIX_Product_Demo_Stabilized.mp4',flush=True)
