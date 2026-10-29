"""Validate stabilized delivery without touching the original movie."""
from pathlib import Path
import subprocess,json,re,hashlib
import numpy as np
from PIL import Image,ImageDraw
P=Path(__file__).resolve().parent;Q=P/'stabilization'/'qc';Q.mkdir(exist_ok=True)
FF=next((P/'tools/imageio_ffmpeg/binaries').glob('*.exe'));MOVIE=P/'SIGNIX_Product_Demo_Stabilized.mp4'
def run(args):return subprocess.run([str(FF),'-hide_banner',*args],capture_output=True)
info=run(['-i',str(MOVIE)]).stderr.decode();(Q/'streams.txt').write_text(info)
assert all(x in info for x in ['00:03:13.00','1920x1080','30 fps','Video: h264','yuv420p','Audio: aac']),info
check=run(['-v','error','-i',str(MOVIE),'-f','null','-']);assert check.returncode==0 and not check.stderr,check.stderr
(Q/'decode.txt').write_text('Complete audio/video decode: exit 0, no errors.\n')
# Verify every decoded video timestamp at 30 FPS, not just container metadata.
r=run(['-v','error','-i',str(MOVIE),'-map','0:v:0','-f','framemd5','-']);assert r.returncode==0
(Q/'frames.md5').write_bytes(r.stdout)
rows=[x.split(',') for x in r.stdout.decode().splitlines() if x and not x.startswith('#')];assert len(rows)==5790,len(rows)
pts=[int(x[2]) for x in rows];assert pts==list(range(5790)),pts[:10]
# Audio is copied from the approved original; compare compressed audio packet hash.
audio=[]
for file in [P/'SIGNIX_Product_Demo.mp4',MOVIE]:
 r=run(['-v','error','-i',str(file),'-map','0:a:0','-c','copy','-f','hash','-hash','sha256','-']);assert r.returncode==0;audio.append(r.stdout.decode().strip())
assert audio[0]==audio[1],audio
# Measure page-edge position on consecutive PDF frames in the push-in window.
metrics={}
for label,file in [('original',P/'SIGNIX_Product_Demo.mp4'),('stabilized',MOVIE)]:
 r=run(['-v','error','-ss','152','-i',str(file),'-t','2','-vf','crop=160:100:130:400,format=gray','-f','rawvideo','-pix_fmt','gray','-']);assert r.returncode==0
 frames=np.frombuffer(r.stdout,np.uint8).reshape(-1,100,160);positions=[]
 for frame in frames:
  profile=frame.mean(0);gradient=np.diff(profile);k=np.argmax(gradient[:75]);lo=max(0,k-2);hi=k+3;w=np.maximum(gradient[lo:hi],0);positions.append(float(np.sum(np.arange(lo,hi)*w)/sum(w)))
 delta=np.diff(positions);metrics[label]={'page_edge_pixels':positions,'acceleration_rms_px_per_frame2':float(np.sqrt(np.mean(np.diff(delta)**2))),'reverse_steps_over_point15_px':int(np.sum(delta>.15))}
scenes=json.loads((P/'production.json').read_text());offset=0;sheet=Image.new('RGB',(1440,1200),'#142239');d=ImageDraw.Draw(sheet)
for i,s in enumerate(scenes):
 t=offset+s['duration']*.65;out=Q/(s['name']+'.png');r=run(['-v','error','-y','-ss',str(t),'-i',str(MOVIE),'-frames:v','1',str(out)]);assert r.returncode==0
 im=Image.open(out).resize((480,270));x=i%3*480;y=i//3*300;sheet.paste(im,(x,y));d.text((x+8,y+275),s['name']+f' {t:.1f}s',fill='white');offset+=s['duration']
sheet.save(Q/'contact-sheet.jpg',quality=95)
# Adjacent-frame strips for all requested major scenes, after title fade.
for name,time in [('opening',3),('mapping',29),('planner',50),('energy',67),('finance',93),('compare',139),('pdf',152),('closing',178)]:
 r=run(['-v','error','-ss',str(time),'-i',str(MOVIE),'-t','0.6','-vf','scale=480:270','-f','rawvideo','-pix_fmt','rgb24','-']);assert r.returncode==0
 a=np.frombuffer(r.stdout,np.uint8).reshape(-1,270,480,3);im=Image.new('RGB',(1920,1200),'#142239');dr=ImageDraw.Draw(im)
 for i,frame in enumerate(a[:16]):
  x=i%4*480;y=i//4*300;im.paste(Image.fromarray(frame),(x,y));dr.text((x+8,y+275),f'{time+i/30:.3f}s',fill='white')
 im.save(Q/(name+'-consecutive.jpg'),quality=93)
original_hash=hashlib.sha256((P/'SIGNIX_Product_Demo.mp4').read_bytes()).hexdigest();assert original_hash==json.loads((P/'stabilization/settings.json').read_text())['original_sha256']
summary={'decode':'passed','frames':len(rows),'timestamps':'consecutive 1/30 second','audio_packet_hash':audio[0],'audio_matches_original':True,'original_unchanged':True,'file_bytes':MOVIE.stat().st_size,'total_bitrate_bps':round(MOVIE.stat().st_size*8/193),'sha256':hashlib.sha256(MOVIE.read_bytes()).hexdigest(),'motion':metrics}
(Q/'results.json').write_text(json.dumps(summary,indent=2));print(info);print(json.dumps({k:v for k,v in summary.items() if k!='motion'},indent=2));print({k:{x:y for x,y in v.items() if x!='page_edge_pixels'} for k,v in metrics.items()})
