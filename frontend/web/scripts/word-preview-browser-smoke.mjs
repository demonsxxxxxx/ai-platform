#!/usr/bin/env node
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createServer } from 'vite';
import { chromium } from 'playwright-core';
import { findChrome, startBrowser } from './browser-smoke-harness.mjs';
import { isWordFixtureAsset } from './word-preview-request-policy.mjs';
import { isPinnedPlaywrightSwError, verifyPinnedPlaywrightSwSource } from './word-preview-browser-errors.mjs';

const evidenceDir=resolve(process.env.AI_PLATFORM_WORD_SMOKE_DIR||'../../.codex-tmp/word-preview-browser-smoke');
mkdirSync(evidenceDir,{recursive:true});
const evidence={sourceCommit:process.env.SOURCE_COMMIT||execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),status:'running',requests:[],assertions:[],browserEvents:[],csp:[]};
let server;let lifecycle;let browser;let context;
try {
  const chrome=findChrome();
  if(!chrome) throw Error('chrome_not_found: no installation or skipped acceptance is allowed');
  server=await createServer({server:{host:'127.0.0.1',port:0,strictPort:true},logLevel:'warn',plugins:[{
    name:'word-smoke-conversion-observer',enforce:'pre',
    transform(source,id){
      if(!id.endsWith('/src/components/documents/previews/WordPreview.tsx'))return;
      const call='await renderDocxPreviewHtml({';
      assert.equal(source.split(call).length,2,'test timing seam must match exactly once');
      return `import { observeWordConversion } from '/scripts/fixtures/word-preview/conversion-observer.mjs';\n${source.replace(call,'await observeWordConversion(renderDocxPreviewHtml, {')}`;
    },
  }]});
  await server.listen();
  const origin=new URL(server.resolvedUrls.local[0]).origin;
  const crossSiteOrigin=origin.replace('127.0.0.1','localhost');
  lifecycle=await startBrowser({chromePath:chrome,viewport:{width:1280,height:1000,mobile:false},timeoutMs:30000,report:(event,details)=>evidence.browserEvents.push({event,details})});
  // Official CDP attachment has lower fidelity than Playwright's own protocol.
  // Controls below are mandatory; the existing harness still owns Chrome/profile cleanup.
  browser=await chromium.connectOverCDP(lifecycle.endpoint,{timeout:30000});
  evidence.browser=browser.version();
  context=await browser.newContext({serviceWorkers:'block',viewport:{width:1280,height:1000}});
  context.setDefaultTimeout(20000);
  const requestLog=[];const routeErrors=[];
  const sandboxControlUrl=`${origin}/__word_probe__/empty-sandbox-control`;
  await context.route('**/*',async route=>{
    try {
      const request=route.request();
      if(request.url()===sandboxControlUrl&&request.resourceType()==='document')return await route.fulfill({status:200,contentType:'text/html',body:'<!doctype html><title>Empty control</title>'});
      if(isWordFixtureAsset(request.url(),request.resourceType(),[origin,crossSiteOrigin]))return await route.continue();
      const ancestry=[];for(let frame=request.frame();frame;frame=frame.parentFrame())ancestry.push(frame.url());
      requestLog.push({url:request.url(),resourceType:request.resourceType(),ancestry});
      await route.abort('blockedbyclient');
    }catch(error){routeErrors.push(String(error));await route.abort().catch(()=>{});}
  });
  // The isolated context is guarded BEFORE any page or renderer exists.
  verifyPinnedPlaywrightSwSource();
  const page=await context.newPage();
  page.on('console',message=>{if(/Content Security Policy|violates.*directive/i.test(message.text()))evidence.csp.push(message.text());});
  const pageErrors=[];evidence.pageErrors=pageErrors;
  page.on('pageerror',error=>pageErrors.push({name:error.name,message:error.message,stack:error.stack}));
  // No application modules or scripts exist yet. Require the known upstream
  // signature; never learn an allowlist from an arbitrary control exception.
  await page.goto(sandboxControlUrl);
  await page.setContent('<!doctype html><iframe id="empty-sandbox" sandbox="" srcdoc="<!doctype html><p>Empty sandbox control</p>"></iframe>');
  await page.frameLocator('#empty-sandbox').getByText('Empty sandbox control',{exact:true}).waitFor();
  assert.equal(pageErrors.length,1,'empty opaque frame must reproduce exactly one upstream init error');
  assert.ok(isPinnedPlaywrightSwError(pageErrors[0]),'unknown control error: inspect raw stack before changing classification');
  evidence.sandboxControl=pageErrors[0];
  await page.locator('#empty-sandbox').evaluate(node=>node.remove());
  const navigation=await page.goto(`${origin}/scripts/fixtures/word-preview/index.html`);
  assert.equal(navigation.headers()['content-security-policy'],undefined,'parent HTTP CSP must not mask isolation');
  await page.locator('#status').filter({hasText:'ready'}).waitFor();
  assert.equal(await page.locator('meta[http-equiv="Content-Security-Policy"]').count(),0,'parent CSP must not mask isolation');
  const swControl=await page.evaluate(async()=>({result:await navigator.serviceWorker.register('/__word_probe__/worker.js'),registrations:(await navigator.serviceWorker.getRegistrations()).length}));
  assert.equal(swControl.result,undefined);assert.equal(swControl.registrations,0);
  assert.equal(context.serviceWorkers().length,0);
  assert.deepEqual(requestLog,[],'SW registration must be blocked before any script request');
  evidence.serviceWorkerControl={registrations:swControl.registrations,workers:context.serviceWorkers().length};
  // Real cross-site child and nested frames, plus same-origin sentinel controls.
  const controls=[
    'https://synthetic.invalid/top-control','https://synthetic.invalid/frame-control','https://synthetic.invalid/nested-control',
    `${origin}/__word_probe__/control/top`,`${crossSiteOrigin}/__word_probe__/control/child`,`${origin}/__word_probe__/control/nested`,
  ];
  const failedControls=Promise.all(controls.map(url=>page.waitForEvent('requestfailed',{predicate:request=>request.url()===url,timeout:10000})));
  await page.evaluate(url=>{
    void fetch('https://synthetic.invalid/top-control').catch(()=>{});
    void fetch('/__word_probe__/control/top').catch(()=>{});
    const control=document.createElement('iframe');control.id='network-control';control.src=url;document.body.append(control);
  },`${crossSiteOrigin}/scripts/fixtures/word-preview/control.html`);
  await failedControls;
  assert.deepEqual(requestLog.map(r=>r.url).sort(),controls.sort());
  assert.equal(requestLog.find(r=>r.url==='https://synthetic.invalid/frame-control').ancestry.length,2);
  assert.equal(requestLog.find(r=>r.url==='https://synthetic.invalid/nested-control').ancestry.length,3);
  evidence.networkControls=requestLog.splice(0);evidence.requests=requestLog;
  await page.locator('#network-control').evaluate(node=>node.remove());
  evidence.assertions.push('official context routing blocks top, cross-site child, nested frame and same-origin controls; no parent CSP');

  // Controlled async-return window, after the unmodified real converter settles.
  await page.evaluate(()=>{window.__wordSmokeGate={armed:true};});
  await page.locator('#portrait').click();
  await page.waitForFunction(()=>window.__wordSmokeGate?.settled===true);
  assert.equal(await page.locator('#preview-host iframe').count(),1,'loading preview keeps its empty frame');
  assert.doesNotMatch(await page.locator('#preview-host iframe').getAttribute('srcdoc'),/Final synthetic page/,'conversion result is still pending');
  await page.locator('#close').click();
  assert.equal(await page.locator('#preview-host iframe').count(),0,'Close unmounts the loading frame');
  await page.evaluate(()=>window.__wordSmokeGate.release());
  await page.waitForFunction(()=>window.__wordSmokeGate.returned===true);
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await page.locator('#preview-host iframe').count(),0,'late completed conversion must not republish after Close');
  await page.evaluate(()=>{delete window.__wordSmokeGate;});
  evidence.assertions.push('Close during controlled real-converter return window: settled result released after unmount, no late iframe');

  const frame=()=>page.frameLocator('#preview-host iframe');
  async function visibleMarker(marker,scroll=true){
    const target=frame().getByText(marker,{exact:true});
    if(scroll)await target.scrollIntoViewIfNeeded();
    const box=await target.boundingBox();const host=await page.locator('#preview-host').boundingBox();
    const viewport=page.viewportSize();
    const inner=await frame().locator('html').evaluate(root=>({width:root.clientWidth,height:root.clientHeight}));
    const clips=await page.locator('#preview-host').evaluate((host,inner)=>{
      const contentRect=node=>{const r=node.getBoundingClientRect();const sx=r.width/node.offsetWidth,sy=r.height/node.offsetHeight;return {x:r.x+node.clientLeft*sx,y:r.y+node.clientTop*sy,width:node.clientWidth*sx,height:node.clientHeight*sy,sx,sy};};
      const iframe=contentRect(host.querySelector('iframe'));
      iframe.width=Math.min(iframe.width,inner.width*iframe.sx);iframe.height=Math.min(iframe.height,inner.height*iframe.sy);
      return {iframe,scroller:contentRect(host.querySelector('div[aria-label]'))};
    },inner);
    assert.ok(box&&host&&box.width>0&&box.height>0);
    const bounds=[host,clips.iframe,clips.scroller,{x:0,y:0,...viewport}];
    assert.ok(box.x>=Math.max(...bounds.map(r=>r.x))&&box.x+box.width<=Math.min(...bounds.map(r=>r.x+r.width)),'marker must be inside every horizontal ancestor clip');
    assert.ok(box.y>=Math.max(...bounds.map(r=>r.y))&&box.y+box.height<=Math.min(...bounds.map(r=>r.y+r.height)),'marker must be inside every vertical ancestor clip');
    return {box,host,...clips,viewport};
  }
  async function inspectDocument(marker){
    await frame().getByText(marker,{exact:true}).waitFor();
    const owner=await page.locator('#preview-host iframe').elementHandle();
    const child=await owner.contentFrame();
    await child.waitForFunction(()=>document.fonts.status==='loaded'&&[...document.fonts].some(f=>f.family.includes('SyntheticPreview')&&f.status==='loaded')&&document.querySelector('img')?.naturalWidth>0);
    return frame().locator('body').evaluate((body,marker)=>{
      const image=body.querySelector('img');
      const fonts=[...document.fonts].filter(f=>f.family.includes('SyntheticPreview'));
      const pages=[...body.querySelectorAll('section.docx')];const last=pages.at(-1);
      const end=[...last.querySelectorAll('span')].find(x=>x.textContent.includes(marker));end.scrollIntoView({block:'center'});
      const box=end.getBoundingClientRect();
      return {pages:pages.length,imageLoaded:image.naturalWidth>0,fontLoaded:fonts.length>0&&fonts.every(f=>f.status==='loaded'),math:body.querySelectorAll('math mroot').length,endVisible:box.top>=0&&box.bottom<=innerHeight,scrollY,pageWidth:pages[0].getBoundingClientRect().width,links:body.querySelectorAll('a[href]:not([href^="#"])').length,csp:document.head.querySelector('meta[http-equiv="Content-Security-Policy"]')?.content};
    },marker);
  }
  await page.locator('#portrait').click();const portrait=await inspectDocument('Final synthetic page');
  assert.ok(portrait.pages>=2&&portrait.imageLoaded&&portrait.fontLoaded&&portrait.math>0&&portrait.endVisible&&portrait.scrollY>0);
  assert.equal(portrait.links,0);assert.match(portrait.csp,/default-src 'none'/);
  assert.equal(await page.locator('#preview-host iframe').evaluate(node=>node.contentDocument===null),true);
  const scale=()=>page.locator('#preview-host iframe').evaluate(node=>node.parentElement.style.transform);
  const initial=await scale();await page.getByRole('button',{name:'Zoom in',exact:true}).click();
  await page.waitForFunction(initial=>document.querySelector('#preview-host iframe').parentElement.style.transform!==initial,initial);
  await page.getByRole('button',{name:'Reset',exact:true}).click();
  await page.waitForFunction(initial=>document.querySelector('#preview-host iframe').parentElement.style.transform===initial,initial);
  portrait.viewportMarker=await visibleMarker('Final synthetic page');
  evidence.portrait=portrait;evidence.assertions.push('real Word: embedded PNG/font/MathML, last page, opaque frame, toolbar zoom/reset');
  await page.screenshot({path:resolve(evidenceDir,'portrait.png')});
  await page.locator('#landscape').click();const landscape=await inspectDocument('Final landscape page');
  assert.ok(landscape.endVisible&&landscape.imageLoaded&&landscape.fontLoaded&&landscape.math>0&&landscape.pageWidth>portrait.pageWidth);
  const horizontal=await frame().locator('body').evaluate(()=>{window.scrollTo(document.documentElement.scrollWidth,window.scrollY);return {x:scrollX,max:document.documentElement.scrollWidth-innerWidth};});
  assert.ok(horizontal.max>0&&horizontal.x>0);
  landscape.viewportMarker=await visibleMarker('Final landscape page');
  evidence.landscape={...landscape,horizontal};evidence.assertions.push('document replacement, landscape final page and horizontal scrolling');
  await page.screenshot({path:resolve(evidenceDir,'landscape.png')});
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();await page.getByText('120%',{exact:true}).waitFor();
  await page.locator('#dark-fallback').click();await frame().getByText('Dark synthetic fallback',{exact:true}).waitFor();
  const dark=await frame().locator('body').evaluate(body=>({background:getComputedStyle(body).backgroundColor,color:getComputedStyle(body.querySelector('.word-preview-content')).color}));
  assert.equal(dark.background,'rgb(17, 24, 39)');assert.equal(dark.color,'rgb(229, 231, 235)');
  dark.viewportMarker=await visibleMarker('Dark synthetic fallback',false);
  dark.outerScroll=await page.locator('#preview-host div[aria-label]').evaluate(node=>node.scrollTop);
  assert.equal(dark.outerScroll,0);await page.getByText('100%',{exact:true}).waitFor();
  evidence.dark=dark;evidence.assertions.push('real WordPreview malformed DOCX text fallback and production dark theme');
  await page.screenshot({path:resolve(evidenceDir,'dark-fallback.png')});
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();await page.getByText('120%',{exact:true}).waitFor();
  await page.evaluate(()=>{window.__wordSmokeGate={armed:true};});
  await page.locator('#portrait').click();await page.waitForFunction(()=>window.__wordSmokeGate?.settled===true);
  assert.equal(await page.locator('#preview-host div[aria-label]').evaluate(node=>node.scrollTop),0,'fallback to loading restores original owner scroll reset');
  assert.doesNotMatch(await page.locator('#preview-host iframe').getAttribute('srcdoc'),/Dark synthetic fallback|Final synthetic page/);
  await page.evaluate(()=>window.__wordSmokeGate.release());await page.waitForFunction(()=>window.__wordSmokeGate.returned===true);
  await page.evaluate(()=>{delete window.__wordSmokeGate;});
  await inspectDocument('Final synthetic page');await page.getByText('100%',{exact:true}).waitFor();
  evidence.replacementMarker=await visibleMarker('Final synthetic page');
  evidence.assertions.push('actual parent-viewport clipped marker visibility; fallback/loading/new-DOCX owner resets scroll and zoom');
  await page.locator('#close').click();assert.equal(await page.locator('#preview-host iframe').count(),0);
  evidence.requests=requestLog;assert.deepEqual(requestLog,[],'renderer attempted an untrusted request');
  assert.deepEqual(routeErrors,[]);
  const unexpectedErrors=pageErrors.filter(error=>!isPinnedPlaywrightSwError(error));
  evidence.pageErrorClassification={pinnedPlaywrightSwErrors:pageErrors.length-unexpectedErrors.length,unexpectedErrors};
  assert.deepEqual(unexpectedErrors,[],'application errors or unknown injection signatures remain failures');
  assert.equal(context.serviceWorkers().length,0);
  assert.ok(evidence.csp.some(text=>text.includes('synthetic.invalid/pixel')),'external CSS must be rejected by child CSP');
  assert.ok(evidence.csp.some(text=>text.includes('/__word_probe__/document')),'same-origin CSS must be rejected by child CSP');
  evidence.assertions.push('zero untrusted requests across real render/prepare/iframe/replacement/close');
  evidence.status='passed';console.log(JSON.stringify(evidence,null,2));
}catch(error){evidence.status='failed';evidence.failure=String(error);throw error;}
finally {
  const cleanupErrors=[];
  for(const cleanup of [()=>context?.close(),()=>browser?.close(),()=>lifecycle?.close(),()=>server?.close()]){
    try{await cleanup();}catch(error){cleanupErrors.push(String(error));}
  }
  if(cleanupErrors.length){evidence.status='failed';evidence.cleanupErrors=cleanupErrors;}
  writeFileSync(resolve(evidenceDir,'evidence.json'),JSON.stringify(evidence,null,2));
  if(cleanupErrors.length)throw Error(`word_preview_cleanup_failed:${cleanupErrors.join('; ')}`);
}
