#!/usr/bin/env node
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createServer } from 'vite';
import { captureScreenshot, findChrome, sleep, startBrowser, withTimeout } from './browser-smoke-harness.mjs';

const evidenceDir=resolve(process.env.AI_PLATFORM_WORD_SMOKE_DIR||'../../.codex-tmp/word-preview-browser-smoke');
mkdirSync(evidenceDir,{recursive:true});
const evidence={sourceCommit:process.env.SOURCE_COMMIT||execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),status:'running',requests:[],assertions:[],browserEvents:[],csp:[]};
let server;let browser;
try {
  const chrome=findChrome();
  if(!chrome) throw Error('chrome_not_found: no installation or skipped acceptance is allowed');
  server=await createServer({server:{host:'127.0.0.1',port:0,strictPort:true},logLevel:'warn'});
  await server.listen();
  const origin=new URL(server.resolvedUrls.local[0]).origin;
  browser=await startBrowser({chromePath:chrome,viewport:{width:1280,height:1000},timeoutMs:30000,report:(event,details)=>evidence.browserEvents.push({event,details})});
  const {client}=browser;
  evidence.browser=await client.send('Browser.getVersion');
  const contexts=new Map();const tasks=new Set();const failures=[];const requestLog=[];
  const tracked=(work)=>{const task=Promise.resolve(work).catch(error=>failures.push(String(error))).finally(()=>tasks.delete(task));tasks.add(task);};
  const assertHealthy=()=>assert.deepEqual(failures,[],'browser interception/target setup failed');
  const contextKey=(sessionId,id)=>`${sessionId||'top'}:${id}`;
  client.on('Runtime.executionContextCreated',({context},sessionId)=>{
    if(context.auxData?.isDefault)contexts.set(contextKey(sessionId,context.id),{contextId:context.id,sessionId,frameId:context.auxData.frameId});
  });
  client.on('Runtime.executionContextDestroyed',({executionContextId},sessionId)=>contexts.delete(contextKey(sessionId,executionContextId)));
  client.on('Runtime.executionContextsCleared',(_params,sessionId)=>{for(const [key,value]of contexts)if(value.sessionId===sessionId)contexts.delete(key);});
  client.on('Target.detachedFromTarget',({sessionId})=>{for(const [key,value]of contexts)if(value.sessionId===sessionId)contexts.delete(key);});
  client.on('Log.entryAdded',({entry})=>{if(entry.source==='security')evidence.csp.push(entry.text);});
  client.on('Runtime.exceptionThrown',({exceptionDetails})=>failures.push(exceptionDetails.exception?.description||exceptionDetails.text));
  client.on('Fetch.requestPaused',(params,sessionId)=>tracked((async()=>{
    const url=new URL(params.request.url);
    const local=url.origin===origin||['data:','blob:','about:'].includes(url.protocol);
    if(!local){requestLog.push({url:url.href,session:sessionId?'child':'top'});await client.send('Fetch.failRequest',{requestId:params.requestId,errorReason:'BlockedByClient'},sessionId);}
    else await client.send('Fetch.continueRequest',{requestId:params.requestId},sessionId);
  })()));
  async function protect(sessionId){
    await client.send('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]},sessionId);
    await client.send('Target.setAutoAttach',{autoAttach:true,waitForDebuggerOnStart:true,flatten:true},sessionId);
    await client.send('Runtime.enable',{},sessionId);await client.send('Log.enable',{},sessionId);
  }
  client.on('Target.attachedToTarget',({sessionId})=>tracked((async()=>{await protect(sessionId);await client.send('Runtime.runIfWaitingForDebugger',{},sessionId);})()));
  // Install both top and paused-child request guards BEFORE any fixture or renderer action.
  await protect();
  await client.send('Page.navigate',{url:`${origin}/scripts/fixtures/word-preview/index.html`});
  await client.waitFor("document.getElementById('status')?.textContent==='ready'",'fixture_ready');
  assert.equal(await client.evaluate("Boolean(document.querySelector('meta[http-equiv=\"Content-Security-Policy\"]'))"),false,'parent CSP must not mask child isolation');
  // Negative controls validate request interception in both parent and opaque frame.
  await client.evaluate(`fetch('https://synthetic.invalid/top-control').catch(()=>{});const control=document.createElement('iframe');control.id='network-control';control.sandbox='';control.srcdoc='<img src="https://synthetic.invalid/frame-control">';document.body.append(control);`);
  await withTimeout((async()=>{while(requestLog.length<2){assertHealthy();await sleep(50);}})(),10000,'all_frame_network_controls');
  assert.deepEqual(requestLog.map(r=>r.url).sort(),['https://synthetic.invalid/frame-control','https://synthetic.invalid/top-control']);
  evidence.networkControls=requestLog.splice(0);
  await client.evaluate("document.getElementById('network-control').remove()");
  await Promise.all([...tasks]);assertHealthy();
  evidence.assertions.push('top/opaque-frame interception controls blocked before render; no masking parent CSP');

  async function frameWithText(text){
    return withTimeout((async()=>{for(;;){assertHealthy();for(const context of contexts.values()){
      const matches=await client.evaluate(`location.href==='about:srcdoc' && document.body?.textContent.includes(${JSON.stringify(text)})`,context).catch(()=>false);
      if(matches)return context;
    }await sleep(50);}})(),20000,`frame:${text}`);
  }
  const click=(selector)=>client.evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  async function inspectDocument(marker){
    const context=await frameWithText(marker);
    return client.evaluate(`(async()=>{
      await document.fonts.ready;
      const image=document.querySelector('img');await image.decode();
      const fonts=await document.fonts.load('16px SyntheticPreview','AAA');
      const pages=[...document.querySelectorAll('section.docx')];const last=pages.at(-1);last.scrollIntoView({block:'start'});
      const end=[...last.querySelectorAll('span')].find(x=>x.textContent.includes(${JSON.stringify(marker)}));end.scrollIntoView({block:'center'});
      const box=end.getBoundingClientRect();
      return {pages:pages.length,imageLoaded:image.naturalWidth>0,fontLoaded:fonts.length>0&&fonts.every(f=>f.status==='loaded'),math:document.querySelectorAll('math mroot').length,endVisible:box.top>=0&&box.top<innerHeight,scrollY,pageWidth:pages[0].getBoundingClientRect().width,scrollWidth:document.documentElement.scrollWidth,viewportWidth:innerWidth,links:document.querySelectorAll('a[href]:not([href^="#"])').length,csp:document.head.querySelector('meta[http-equiv="Content-Security-Policy"]')?.content};
    })()`,context);
  }
  await click('#portrait');
  const portrait=await inspectDocument('Final synthetic page');
  assert.ok(portrait.pages>=2&&portrait.imageLoaded&&portrait.fontLoaded&&portrait.math>0&&portrait.endVisible&&portrait.scrollY>0);
  assert.equal(portrait.links,0);assert.match(portrait.csp,/default-src 'none'/);
  assert.equal(await client.evaluate("document.querySelector('#preview-host iframe').contentDocument===null"),true);
  const scale=()=>client.evaluate("document.querySelector('#preview-host iframe').parentElement.style.transform");
  const initial=await scale();await click('button[aria-label="Zoom in"]');await client.waitFor(`document.querySelector('#preview-host iframe').parentElement.style.transform!==${JSON.stringify(initial)}`,'real_toolbar_zoom');
  await click('button[aria-label="Reset"]');await client.waitFor(`document.querySelector('#preview-host iframe').parentElement.style.transform===${JSON.stringify(initial)}`,'real_toolbar_reset');
  evidence.portrait=portrait;evidence.assertions.push('real Word component: PNG, font, MathML, last page, opaque frame, real toolbar zoom/reset');
  await captureScreenshot(client,evidenceDir,'portrait');

  await click('#landscape');const landscape=await inspectDocument('Final landscape page');
  assert.ok(landscape.endVisible&&landscape.imageLoaded&&landscape.fontLoaded&&landscape.math>0&&landscape.pageWidth>portrait.pageWidth);
  const landscapeContext=await frameWithText('Final landscape page');
  const horizontal=await client.evaluate("window.scrollTo(document.documentElement.scrollWidth,window.scrollY);({x:scrollX,max:document.documentElement.scrollWidth-innerWidth})",landscapeContext);
  assert.ok(horizontal.max>0&&horizontal.x>0);
  evidence.landscape={...landscape,horizontal};evidence.assertions.push('replacement document and landscape final page/horizontal scroll reachable');
  await captureScreenshot(client,evidenceDir,'landscape');

  await click('#dark-fallback');const darkContext=await frameWithText('Dark synthetic fallback');
  const dark=await client.evaluate("({background:getComputedStyle(document.body).backgroundColor,color:getComputedStyle(document.querySelector('.word-preview-content')).color})",darkContext);
  assert.equal(dark.background,'rgb(17, 24, 39)');assert.equal(dark.color,'rgb(229, 231, 235)');
  assert.equal(await client.evaluate('Boolean(window.__wordProbeCompromised)'),false);
  assert.equal(await client.evaluate("document.querySelectorAll('script,a[href]').length",darkContext),0);
  evidence.dark=dark;evidence.assertions.push('same static frame owner: dark fallback text/background contrast');
  await click('#portrait');await frameWithText('Final synthetic page');await click('#close');
  await client.waitFor("document.querySelectorAll('#preview-host iframe').length===0",'close_unmount');
  await sleep(200);assert.equal(await client.evaluate("document.querySelectorAll('#preview-host iframe').length"),0);
  await Promise.all([...tasks]);assertHealthy();
  evidence.requests=requestLog;assert.deepEqual(requestLog,[],'untrusted renderer attempted external network');
  assert.ok(evidence.csp.some(text=>text.includes('synthetic.invalid/pixel')),'malicious CSS must reach child policy and be blocked');
  evidence.assertions.push('no external requests across render/prepare/iframe/replacement; close removes frame');
  evidence.status='passed';console.log(JSON.stringify(evidence,null,2));
}catch(error){evidence.status='failed';evidence.failure=String(error);throw error;}
finally {
  const cleanupErrors=[];
  try { await browser?.close(); } catch(error) { cleanupErrors.push(String(error)); }
  try { await server?.close(); } catch(error) { cleanupErrors.push(String(error)); }
  if(cleanupErrors.length){evidence.status='failed';evidence.cleanupErrors=cleanupErrors;}
  writeFileSync(resolve(evidenceDir,'evidence.json'),JSON.stringify(evidence,null,2));
  if(cleanupErrors.length)throw Error(`word_preview_cleanup_failed:${cleanupErrors.join('; ')}`);
}
