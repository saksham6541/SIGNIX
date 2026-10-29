"""Decode the finished movie, inspect streams/audio and extract visual QC frames."""
from pathlib import Path
import subprocess,json,re
from PIL import Image,ImageDraw,ImageFont
P=Path(__file__).resolve().parent;ff=next((P/'tools/imageio_ffmpeg/binaries').glob('*.exe'));movie=P/'SIGNIX_Product_Demo.mp4';qc=P/'qc';qc.mkdir(exist_ok=True)
def call(args):return subprocess.run([str(ff),'-hide_banner',*args],capture_output=True,text=True)
info=call(['-i',str(movie)])
(qc/'streams.txt').write_text(info.stderr,encoding='utf-8')
assert '1920x1080' in info.stderr and '30 fps' in info.stderr and 'Video: h264' in info.stderr and 'Audio: aac' in info.stderr,info.stderr
assert '00:03:13.' in info.stderr,info.stderr
check=call(['-v','error','-i',str(movie),'-f','null','-'])
(qc/'decode.txt').write_text(check.stderr or 'Full video/audio decode completed with no errors.\n',encoding='utf-8');assert check.returncode==0,check.stderr
volume=call(['-i',str(movie),'-af','volumedetect','-vn','-f','null','-']);(qc/'audio.txt').write_text(volume.stderr,encoding='utf-8')
scenes=json.loads((P/'production.json').read_text());offset=0;sheet=Image.new('RGB',(1440,4*300),'#142239');draw=ImageDraw.Draw(sheet)
for i,s in enumerate(scenes):
 t=offset+s['duration']*.65;out=qc/(s['name']+'.png');r=call(['-y','-ss',str(t),'-i',str(movie),'-frames:v','1',str(out)]);assert r.returncode==0,r.stderr
 im=Image.open(out).resize((480,270));x=i%3*480;y=i//3*300;sheet.paste(im,(x,y));draw.text((x+10,y+277),f'{i+1}. {s["name"]} | {t:.1f}s',fill='white');offset+=s['duration']
sheet.save(qc/'contact-sheet.jpg',quality=94)
print(info.stderr)
print('\n'.join(line for line in volume.stderr.splitlines() if 'volume:' in line))
print('Full decode passed. All 11 scene samples extracted.')
