import json,wave,math,textwrap,subprocess
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont
P=Path(__file__).resolve().parent
ROOT=P.parent
scenes=json.loads((P/'production.json').read_text())
for s in scenes:
 s['duration']={'opening':18,'energy':22,'finance':19,'closing':19}.get(s['name'],s['duration'])
(P/'production.json').write_text(json.dumps(scenes,indent=2))
font=lambda n,b=False:ImageFont.truetype('C:/Windows/Fonts/'+('segoeuib.ttf' if b else 'segoeui.ttf'),n)
navy='#142239';cream='#F8F5EC';amber='#EEA02B';green='#3F896A'
logo=Image.open(ROOT/'app/static/images/signix-logo-full.png').convert('RGBA')
for name in ['opening','closing']:
 im=Image.new('RGB',(1920,1080),navy);d=ImageDraw.Draw(im)
 for r in range(780,400,-4):
  col=(20+int((780-r)*.02),34+int((780-r)*.04),57+int((780-r)*.025))
  d.ellipse((960-r,440-r,960+r,440+r),outline=col,width=3)
 d.rounded_rectangle((535,180,1385,470),radius=28,fill=cream)
 mark=logo.copy();mark.thumbnail((740,230),Image.Resampling.LANCZOS);im.paste(mark,(960-mark.width//2,325-mark.height//2),mark)
 lines=['Your roof. Your energy.','Your decision.'] if name=='opening' else ['Map the roof. Understand the energy.','Compare the investment.']
 for y,line in zip([550,640],lines):d.text((960,y),line,font=font(62,True),fill=cream,anchor='mm')
 d.rounded_rectangle((815,710,1105,716),radius=3,fill=amber)
 d.text((960,790),'ROOFTOP SOLAR PLANNING' if name=='opening' else 'Make the solar decision clearer.',font=font(35),fill=amber,anchor='mm')
 d.text((960,970),'Planning estimates • Site, design and approvals require verification',font=font(24),fill='#BAC5D3',anchor='mm')
 im.save(P/(name+'.png'))
# Sentence-level audio and subtitles; original quiet synthesized pad beneath narration.
sr=22050;total=sum(s['duration'] for s in scenes);mix=np.zeros(int(sr*total),dtype=np.float64);entries=[];offset=0
for s in scenes:
 clips=[]
 for i,line in enumerate(s['lines']):
  with wave.open(str(P/'audio'/f'{s["name"]}-{i}.wav')) as w:
   a=np.frombuffer(w.readframes(w.getnframes()),dtype=np.int16).astype(float)/32768
   if w.getnchannels()>1:a=a.reshape(-1,w.getnchannels()).mean(axis=1)
   if w.getframerate()!=sr:a=np.interp(np.arange(int(len(a)*sr/w.getframerate()))*w.getframerate()/sr,np.arange(len(a)),a)
  clips.append(a)
 speech=sum(len(a)/sr for a in clips);gap=.28;start=offset+.5
 if speech+gap*(len(clips)-1)+.8>s['duration']:raise RuntimeError(f'Narration exceeds scene: {s["name"]}')
 for line,a in zip(s['lines'],clips):
  duration=len(a)/sr;j=int(start*sr);mix[j:j+len(a)]+=a*.95;entries.append((start,start+duration,line));start+=duration+gap
 offset+=s['duration']
t=np.arange(len(mix))/sr
for k in range(math.ceil(total/8)):
 st=k*8;ix=(t>=st)&(t<st+8);u=t[ix]-st;env=np.sin(np.pi*u/8)**2
 freqs=[[130.81,196,261.63],[110,164.81,220],[146.83,220,293.66],[98,146.83,196]][k%4]
 mix[ix]+=sum(np.sin(2*np.pi*f*u) for f in freqs)*env*.004
mix*=np.minimum(1,t/1.5)*np.minimum(1,(total-t)/2);mix=np.clip(mix,-.95,.95)
with wave.open(str(P/'soundtrack.wav'),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(sr);w.writeframes((mix*32767).astype(np.int16).tobytes())
def stamp(t,ass=False):
 h=int(t//3600);m=int(t%3600//60);sec=t%60
 return f'{h}:{m:02d}:{sec:05.2f}' if ass else f'{h:02d}:{m:02d}:{int(sec):02d},{int((sec%1)*1000):03d}'
(P/'subtitles_en.srt').write_text('\n\n'.join(f'{i}\n{stamp(a)} --> {stamp(b)}\n'+ '\n'.join(textwrap.wrap(line,85)) for i,(a,b,line) in enumerate(entries,1)),encoding='utf-8')
ass='''[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Segoe UI,32,&H00FFFFFF,&H00FFFFFF,&H00221810,&H00221810,0,0,0,0,100,100,0,0,3,2,0,2,125,125,24,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
for a,b,line in entries:ass+=f'Dialogue: 0,{stamp(a,True)},{stamp(b,True)},Default,,0,0,0,,{{\\fad(90,90)}}'+r'\N'.join(textwrap.wrap(line,85))+'\n'
(P/'subtitles.ass').write_text(ass,encoding='utf-8')
for s in scenes:
 im=Image.new('RGBA',(1920,1080),(0,0,0,0));d=ImageDraw.Draw(im)
 if s['name'] not in ['opening','closing']:
  d.rectangle((0,0,1920,58),fill=navy);d.rectangle((0,58,8,1050),fill=amber)
  d.text((38,10),s['title'],font=font(27,True),fill=cream)
  d.text((1880,19),'DEMO INPUTS • MOCK WEATHER • PRELIMINARY ESTIMATES',font=font(19),fill='#EFC17A',anchor='ra')
 im.save(P/(s['name']+'-overlay.png'))
print('Prepared',total,'seconds; synchronized narration and',len(entries),'subtitles.')

