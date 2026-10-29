// Play the entire delivered file at 1x and record browser playback diagnostics.
// Does not recapture SIGNIX or access its accounts/data.
const fs=require('fs'),path=require('path');
const {pathToFileURL}=require('url');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const q=path.join(__dirname,'stabilization','qc');fs.mkdirSync(q,{recursive:true});
 const html=path.join(q,'playback.html');
 fs.writeFileSync(html,'<!doctype html><meta charset="utf-8"><title>SIGNIX stabilized playback review</title><style>html,body{margin:0;background:#142239}video{width:100vw;height:100vh;object-fit:contain}</style><video id="v" muted playsinline src="../../SIGNIX_Product_Demo_Stabilized.mp4"></video>');
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--autoplay-policy=no-user-gesture-required']});
 const page=await browser.newPage({viewport:{width:1920,height:1080}});const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(pathToFileURL(html).href);await page.waitForFunction(()=>document.querySelector('video').readyState>=3);
 await page.evaluate(()=>{const v=document.querySelector('video');window.review={events:[],samples:[],maxCallbackGap:0,lastWall:0};['error','stalled','waiting','ended'].forEach(n=>v.addEventListener(n,()=>review.events.push({event:n,t:v.currentTime})));function frame(now,m){if(review.lastWall)review.maxCallbackGap=Math.max(review.maxCallbackGap,now-review.lastWall);review.lastWall=now;if(!review.samples.length||m.mediaTime-review.samples.at(-1).t>=1)review.samples.push({t:m.mediaTime,frames:m.presentedFrames});v.requestVideoFrameCallback(frame);}v.requestVideoFrameCallback(frame);return v.play()});
 let sample=0;
 while(true){await page.waitForTimeout(8000);const state=await page.evaluate(()=>{const v=document.querySelector('video');return{t:v.currentTime,ended:v.ended,error:v.error?.message}});console.log(JSON.stringify(state));if(state.error)throw Error(state.error);await page.screenshot({path:path.join(q,`playback-${String(sample++).padStart(2,'0')}.jpg`),quality:85});if(state.ended)break;if(sample>30)throw Error('Playback exceeded expected duration');}
 const result=await page.evaluate(()=>{const v=document.querySelector('video'),q=v.getVideoPlaybackQuality();return{...window.review,duration:v.duration,currentTime:v.currentTime,ended:v.ended,rate:v.playbackRate,videoWidth:v.videoWidth,videoHeight:v.videoHeight,totalVideoFrames:q.totalVideoFrames,droppedVideoFrames:q.droppedVideoFrames,corruptedVideoFrames:q.corruptedVideoFrames,muted:v.muted}});result.errors=errors;fs.writeFileSync(path.join(q,'browser-playback.json'),JSON.stringify(result,null,2));await browser.close();console.log(JSON.stringify({...result,samples:result.samples.length}));
 if(!result.ended||errors.length)process.exitCode=1;
})();
