"""Deterministic 1080p/30 edit using preserved SIGNIX capture files."""
from pathlib import Path
import subprocess,json,os,sys,re
from PIL import Image,ImageDraw,ImageFont
P=Path(__file__).resolve().parent;os.chdir(P)
FF=next((P/'tools/imageio_ffmpeg/binaries').glob('*.exe'))
scenes=json.loads((P/'production.json').read_text());(P/'renders').mkdir(exist_ok=True)
font=lambda n,b=False:ImageFont.truetype('C:/Windows/Fonts/'+('segoeuib.ttf' if b else 'segoeui.ttf'),n)
navy='#142239';cream='#F8F5EC';amber='#EEA02B'
# Layered title cards: original logo, separate animated phrases.
for name in ['opening','closing']:
 bg=Image.new('RGB',(1920,1080),navy);d=ImageDraw.Draw(bg)
 for r in range(780,400,-6):d.ellipse((960-r,380-r,960+r,380+r),outline=(25,43,64),width=2)
 bg.save(name+'-base.png')
 logo=Image.open(P.parent/'app/static/images/signix-logo-full.png').convert('RGBA');logo.thumbnail((700,210),Image.Resampling.LANCZOS)
 layer=Image.new('RGBA',(1920,1080));d=ImageDraw.Draw(layer);d.rounded_rectangle((550,160,1370,435),radius=30,fill=cream);layer.paste(logo,(960-logo.width//2,295-logo.height//2),logo);layer.save(name+'-logo.png')
 lines=(['Your roof. Your energy.','Your decision.','Rooftop Solar Planning'] if name=='opening' else ['Map the roof.','Understand the energy. Compare the investment.','Make the solar decision clearer.'])
 for i,line in enumerate(lines):
  layer=Image.new('RGBA',(1920,1080));d=ImageDraw.Draw(layer);d.text((960,[535,635,785][i]),line,font=font([62,52,36][i],i<2),fill=cream if i<2 else amber,anchor='mm');layer.save(f'{name}-text-{i}.png')
# PDF is a real exported page, framed as a document with a financial close-up.
pdf=Image.open('pdf-page.png').convert('RGB');canvas=Image.new('RGB',(1920,1080),cream);d=ImageDraw.Draw(canvas)
page=pdf.copy();page.thumbnail((670,950),Image.Resampling.LANCZOS);canvas.paste(page,(105,65))
d.text((860,175),'THE PLANNING SUMMARY',font=font(27,True),fill=amber)
d.text((860,240),'Keep the context',font=font(55,True),fill=navy);d.text((860,310),'with the numbers.',font=font(55,True),fill=navy)
d.text((860,410),'Actual SIGNIX PDF export',font=font(29),fill=navy)
# Preserve the exact report financial table pixels, without rewriting figures.
financial=pdf.crop((90,710,1110,1080));financial.thumbnail((950,400),Image.Resampling.LANCZOS);canvas.paste(financial,(850,510))
d.text((860,925),'Illustrative residential case • Eligibility requires verification',font=font(22),fill=navy)
canvas.save('pdf-composition.png')
shots={}
for file,src in [('shots.json','capture.webm'),('shots-extra.json','capture-extra.webm'),('shots-detail.json','capture-detail.webm'),('shots-planner.json','capture-planner.webm')]:
 if Path(file).exists():
  for item in json.loads(Path(file).read_text()):shots[item['name']]={**item,'src':src}
alias={'business':'business-detail','finance':'finance-detail','compare':'compare-detail'}
def run(args,log):
 with open(log,'w',encoding='utf-8') as f:subprocess.run([str(FF),'-y','-hide_banner','-loglevel','warning',*args],stdout=f,stderr=f,check=True)
def complete_segment(file, duration):
 if not Path(file).exists() or Path(file).stat().st_size < 1024:
  return False
 result=subprocess.run([str(FF),'-hide_banner','-i',file],capture_output=True,text=True)
 match=re.search(r'Duration: (\d+):(\d+):([\d.]+)',result.stderr)
 return bool(match and abs(sum(float(v)*scale for v,scale in zip(match.groups(),[3600,60,1]))-duration)<0.06)

for idx,s in enumerate(scenes):
 name=s['name'];dur=s['duration'];dest=f'renders/{idx:02d}-{name}.mp4'
 if complete_segment(dest,dur) and '--force' not in sys.argv:print('Preserved',dest,flush=True);continue
 print('Rendering',name,dur,'seconds',flush=True)
 if name in ['opening','closing']:
  args=[]
  for f in [name+'-base.png',name+'-logo.png',*[f'{name}-text-{i}.png' for i in range(3)]]:args+=['-loop','1','-framerate','30','-i',f]
  filters=['[0:v]format=rgba[b]']
  starts=[.3,2.5,4.0,6.0] if name=='opening' else [.3,1.5,3.5,7.0]
  for i,st in enumerate(starts,1):
   filters.append(f'[{i}:v]format=rgba,fade=t=in:st={st}:d=1:alpha=1[l{i}]')
   prev='b' if i==1 else f'o{i-1}'
   filters.append(f'[{prev}][l{i}]overlay=x=0:y=\'24*exp(-3*max(t-{st},0))\':shortest=1[o{i}]')
  filters.append(f'[o4]fade=t=in:st=0:d=0.4,fade=t=out:st={dur-.4}:d=0.4,format=yuv420p[v]')
 else:
  if name=='pdf':args=['-loop','1','-framerate','30','-i','pdf-composition.png'];lead='[0:v]';length=dur
  else:
   sh=shots[alias.get(name,name)];length=sh['end']-sh['start']-.3
   args=['-ss',str(sh['start']+.1),'-t',str(length),'-i',sh['src']];lead='[0:v]'
  args+=['-loop','1','-framerate','30','-i',name+'-overlay.png']
  stretch=min(1.6,max(1,(dur-1)/length));amp=.07 if name not in ['home','pdf'] else .025
  filters=[f"{lead}setpts={stretch}*(PTS-STARTPTS),fps=30,tpad=stop_mode=clone:stop_duration={dur},scale=1920:1080,zoompan=z='1+{amp}*pow(sin(PI*on/{dur*30}),2)':x='(iw-iw/zoom)*0.65':y='(ih-ih/zoom)*0.55':d=1:s=1920x1080:fps=30,scale=1728:972,pad=1920:1080:96:54:color=0x142239[content]",
  '[1:v]format=rgba,fade=t=in:st=0.15:d=0.6:alpha=1[title]',
  "[content][title]overlay=x=0:y='-12*exp(-4*t)':shortest=1[composite]",
  f'[composite]fade=t=in:st=0:d=0.25,fade=t=out:st={dur-.25}:d=0.25,format=yuv420p[v]']
  # Small progress accent provides restrained motion without covering UI controls.
  filters[-1]=filters[-1].replace('format=yuv420p[v]',f"drawbox=x=96:y=1044:w='1728':h=2:color=0xEEA02B@0.8:t=fill,format=yuv420p[v]")
 args+=['-filter_complex',';'.join(filters),'-map','[v]','-t',str(dur),'-an','-c:v','libx264','-preset','fast','-crf','19','-r','30','-threads','4','-filter_complex_threads','2',dest]
 run(args,f'renders/{name}.log')
Path('renders/concat.txt').write_text(''.join(f"file '{i:02d}-{s['name']}.mp4'\n" for i,s in enumerate(scenes)))
print('Assembling final MP4 with narration and subtitles',flush=True)
run(['-f','concat','-safe','0','-i','renders/concat.txt','-i','soundtrack.wav','-vf','ass=subtitles.ass','-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p','-r','30','-c:a','aac','-b:a','192k','-ar','48000','-af','loudnorm=I=-16:TP=-1.5:LRA=11','-t',str(sum(s['duration'] for s in scenes)),'-movflags','+faststart','-threads','4','SIGNIX_Product_Demo.rendering.mp4'],'renders/final.log')
Path('SIGNIX_Product_Demo.rendering.mp4').replace('SIGNIX_Product_Demo.mp4')
print('FINISHED SIGNIX_Product_Demo.mp4',flush=True)
