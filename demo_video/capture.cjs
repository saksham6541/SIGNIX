const fs=require('fs'), path=require('path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const out=__dirname, base='http://127.0.0.1:5000';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});
 const ctx=await browser.newContext({viewport:{width:1920,height:1080},recordVideo:{dir:path.join(out,'raw'),size:{width:1920,height:1080}},acceptDownloads:true});
 const page=await ctx.newPage(); const video=page.video(); const start=Date.now(); const shots=[];
 page.on('pageerror',e=>fs.appendFileSync(path.join(out,'browser-errors.log'),e.message+'\n'));
 const pause=ms=>page.waitForTimeout(ms);
 async function shot(name,action,hold=4000){const t=(Date.now()-start)/1000; if(action)await action(); await pause(hold);shots.push({name,start:t,end:(Date.now()-start)/1000});await page.screenshot({path:path.join(out,name+'.png')});fs.writeFileSync(path.join(out,'shots.json'),JSON.stringify(shots,null,2));console.log(name);}
 async function scroll(sel){await page.locator(sel).scrollIntoViewIfNeeded(); await pause(800);}
 try{
 await page.goto(base+'/'); await pause(3000); await shot('landing',null,5000);
 await page.goto(base+'/signup');
 const email='demo'+Date.now()+'@signix.example';
 await page.locator('#display_name').fill('SIGNIX Demonstration');await page.locator('#email').fill(email);await page.locator('#password').fill('Demo-Only-2026!');await page.locator('#confirm_password').fill('Demo-Only-2026!');await page.locator('#submit').click();await pause(2000);
 await page.goto(base+'/survey/home'); await pause(3000);await shot('home',null,4000);
 await page.locator('#maps-link-input').fill('https://www.google.com/maps/@28.6145,77.2074,20z'); await page.locator('#maps-link-btn').click(); await pause(2500);
 await page.evaluate(()=>SignixMapStore.map.setView([28.6145,77.2074],20));await pause(2500);await scroll('#map-section');await shot('mapping',async()=>{
 await page.locator('#draw-btn').click();const b=await page.locator('#map').boundingBox();
 const points=[[.27,.16],[.43,.16],[.43,.24],[.27,.24]];
 for(const [x,y]of points){await page.mouse.move(b.x+b.width*x,b.y+b.height*y,{steps:16});await page.mouse.click(b.x+b.width*x,b.y+b.height*y);await pause(400);}await page.mouse.click(b.x+b.width*.27,b.y+b.height*.16); await pause(1000);
 await page.locator('#mode-obstruction-btn').click();await page.locator('#draw-btn').click();
 for(const [x,y]of [[.34,.18],[.36,.18],[.36,.21],[.34,.21],[.34,.18]]){await page.mouse.move(b.x+b.width*x,b.y+b.height*y,{steps:12});await page.mouse.click(b.x+b.width*x,b.y+b.height*y);await pause(300);}
 },4000);
 await page.locator('#monthly-bill').fill('5000');await page.locator('#tariff-per-kwh').fill('8');await scroll('#start-estimation-btn');
 await page.locator('#start-estimation-btn').click();await page.waitForFunction(()=>document.querySelector('#view-full-report')?.href.includes('/report/'),{timeout:60000});
 await scroll('#estimate-result');await shot('home-results',null,5000);
 const report=await page.locator('#view-full-report').getAttribute('href');fs.writeFileSync(path.join(out,'demo-report.json'),JSON.stringify({report}));
 await page.goto(new URL(report,base).href);await pause(6000);await scroll('#rpt-rooftop');await shot('planner',async()=>{const top=page.locator('[data-roof3d-view="top"]');if(await top.count())await top.click();await pause(2000);await page.locator('[data-roof3d-view="3d"]').click();},6000);
 await page.getByRole('tab',{name:'Energy Forecasting',exact:true}).click();await pause(1500);await shot('energy',async()=>{await scroll('#rpt-energy')},6000);
 await page.getByRole('tab',{name:'Financing Options',exact:true}).click();await pause(1000);await shot('finance',null,6000);
 await page.screenshot({path:path.join(out,'finance-full.png'),fullPage:true});
 console.log('TABS',await page.getByRole('tab').allTextContents());console.log('REPORTLINKS',await page.locator('a[href*="pdf"]').evaluateAll(a=>a.map(x=>({text:x.innerText,href:x.getAttribute('href')}))));
 await ctx.storageState({path:path.join(out,'session.json')});
 }catch(e){console.error(e.message);await page.screenshot({path:path.join(out,'failure.png')});fs.writeFileSync(path.join(out,'failure.txt'),await page.locator('body').innerText());}
 await ctx.close();await video.saveAs(path.join(out,'capture.webm'));await browser.close();
})();
