from pathlib import Path
import subprocess,json
import numpy as np
from PIL import Image,ImageDraw
from scipy.signal import correlate
P=Path(__file__).resolve().parent; Q=P/'stabilization';Q.mkdir(exist_ok=True);ff=next((P/'tools/imageio_ffmpeg/binaries').glob('*.exe'))
def frames(file,start,dur,fps=30,scale=(1920,1080)):
 r=subprocess.run([str(ff),'-v','error','-ss',str(start),'-i',str(P/file),'-t',str(dur),'-vf',f'fps={fps},scale={scale[0]}:{scale[1]}','-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True,check=True)
 return np.frombuffer(r.stdout,np.uint8).reshape(-1,scale[1],scale[0],3)
# Source detail timeline: actual captured viewport, no edit transforms.
sheet=Image.new('RGB',(1440,1080),'#142239');d=ImageDraw.Draw(sheet)
for i,t in enumerate([8,12,18,26,38,42,48,54,62,66,72,77]):
 a=frames('capture-detail.webm',t,.04,25,(480,270))[0];sheet.paste(Image.fromarray(a),(i%3*480,i//3*270));d.text((i%3*480+10,i//3*270+10),str(t)+'s',fill='red')
sheet.save(Q/'source-timeline.jpg')
# Consecutive frames during the old PDF zoom; no source motion at all.
a=frames('SIGNIX_Product_Demo.mp4',152,2)
# Track a small piece of the fixed PDF title with normalized cross correlation.
ref=a[0,280:315,940:1100].mean(2);r=ref-ref.mean();track=[]
for frame in a:
 search=frame[265:330,925:1115].mean(2)
 num=correlate(search,r,mode='valid',method='fft');ones=np.ones(r.shape);n=r.size
 sums=correlate(search,ones,mode='valid',method='fft');sq=correlate(search**2,ones,mode='valid',method='fft')
 score=num/np.sqrt(np.maximum(sq-sums*sums/n,1)*np.sum(r*r));y,x=np.unravel_index(score.argmax(),score.shape);track.append([int(x-15),int(y-15)])
# Show sixteen adjacent frames, enlarged without inventing detail.
strip=Image.new('RGB',(1280,560),'#142239');d=ImageDraw.Draw(strip)
for i in range(16):
 crop=Image.fromarray(a[i,260:320,900:1220]).resize((320,120));x=i%4*320;y=i//4*140;strip.paste(crop,(x,y));d.text((x+4,y+122),f'frame {i} offset {track[i]}',fill='white')
strip.save(Q/'old-consecutive-frames.png')
raw=frames('capture-detail.webm',52,1)
roi=raw[:,100:400,30:220].astype(float);diff=np.abs(np.diff(roi,axis=0)).mean(axis=(1,2,3))
report={'old_pdf_anchor_offsets_pixels':track,'raw_static_sidebar_adjacent_frame_mean_absolute_difference':float(diff.mean()),'source_resolution':'1920x1080','source_frame_rate':25,'original_output_frame_rate':30}
(Q/'diagnosis.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
