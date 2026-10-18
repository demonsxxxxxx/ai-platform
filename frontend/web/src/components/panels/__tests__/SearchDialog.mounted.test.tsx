import assert from 'node:assert/strict';
import test from 'node:test';
import {act,createElement} from 'react';
import {installSelectTestDom} from '../../common/__tests__/selectTestDom';
import {createLobeTestRoot} from '../../common/__tests__/lobeUiTestRoot';
const fixture=installSelectTestDom();
const observers=new Set<FakeObserver>();
class FakeObserver {
 targets=new Set<Element>(); thresholds=[0];
 constructor(private callback: IntersectionObserverCallback){observers.add(this);}
 observe=(el:Element)=>this.targets.add(el);
 unobserve=(el:Element)=>this.targets.delete(el);
 disconnect=()=>{this.targets.clear();observers.delete(this);};
}
Object.assign(globalThis,{IntersectionObserver:FakeObserver});
const {SearchDialog}=await import('../SearchDialog');
const {sessionApi}=await import('../../../services/api');
test.after(()=>fixture.close());
const result=(name:string,more=false)=>({sessions:[{id:name,name,agent_id:'general-agent',metadata:{},is_active:true,created_at:'2026-01-01',updated_at:'2026-01-01'}],has_more:more,total:1,limit:30,skip:0});
async function harness(){
 const requests:Array<{params:any;resolve:(value:any)=>void;reject:(error:Error)=>void}>=[];
 const original=sessionApi.list;
 sessionApi.list=((params:any)=>new Promise((resolve,reject)=>requests.push({params,resolve,reject}))) as any;
 const root=await createLobeTestRoot(document.createElement('div'));
 const selected:string[]=[];
 const render=async(open=true,key='a')=>{await act(async()=>root.render(createElement(SearchDialog,{key,isOpen:open,onClose(){},onSelectSession(id){selected.push(id);}})));};
 const tick=async(ms=240)=>{await act(async()=>{await new Promise(resolve=>setTimeout(resolve,ms));});};
 const query=async(value:string)=>{await act(async()=>{const input=document.querySelector('input')!;Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));});};
 const resolve=async(index:number,name:string,more=false)=>{await act(async()=>requests[index].resolve(result(name,more)));};
 const visible=(name:string)=>document.body.textContent?.includes(name)??false;
 const intersect=async()=>{await act(async()=>{for(const observer of observers){const entries=[...observer.targets].map(target=>({target,isIntersecting:true,intersectionRatio:1} as IntersectionObserverEntry));observer['callback'](entries,observer as unknown as IntersectionObserver);}});};
 const close=async()=>{await act(async()=>root.unmount());sessionApi.list=original;};
 await render();
 return {requests,render,tick,query,resolve,visible,intersect,close,selected};
}
test('one initial request; 200ms debounce; late query success and finally cannot own latest results/loading',async()=>{
 const h=await harness();try{
 assert.equal(h.requests.length,1);await h.tick();assert.equal(h.requests.length,1);
 await h.resolve(0,'initial');await h.query('alpha');await h.tick(80);assert.equal(h.requests.length,1);await h.tick(150);assert.equal(h.requests.length,2);
 await h.query('beta');await h.tick();assert.equal(h.requests.length,3);
 assert.equal(h.requests[2].params.search,'beta');await h.resolve(2,'beta-result');await h.resolve(1,'alpha-stale');assert.ok(h.visible('beta-result'));assert.ok(!h.visible('alpha-stale'));
 await act(async()=>{document.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));});
 await act(async()=>{document.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));});assert.deepEqual(h.selected,['beta-result']);
 await h.query('gamma');await h.tick();await h.query('delta');await h.tick();
 await h.resolve(3,'gamma-stale');assert.ok(!h.visible('gamma-stale'));assert.ok(document.querySelectorAll('[class*="grid-area:1/1"]')[0].classList.contains('opacity-100'), 'stale finally must not hide the latest skeleton');assert.ok(document.querySelectorAll('[class*="grid-area:1/1"]')[1].classList.contains('opacity-0'), 'stale finally must not reveal the loading results layer');
 await h.resolve(4,'delta-result');assert.ok(h.visible('delta-result'));
 }finally{await h.close();}
});
test('pagination belongs to its query; stale page cannot append or change latest cursor/hasMore',async()=>{
 const h=await harness();try{
 await h.resolve(0,'initial',true);await h.intersect();assert.equal(h.requests[1].params.skip,1);
 await h.query('beta');await h.tick();assert.equal(h.requests[2].params.skip,0);
 await h.resolve(2,'beta',true);await h.resolve(1,'stale-page',false);assert.ok(!h.visible('stale-page'));
 await h.intersect();assert.equal(h.requests[3].params.search,'beta');assert.equal(h.requests[3].params.skip,1);
 await h.resolve(3,'beta-page',false);assert.ok(h.visible('beta'));assert.ok(h.visible('beta-page'));await h.intersect();assert.equal(h.requests.length,4);
 }finally{await h.close();}
});
test('close/reopen and keyed tenant/user replacement invalidate outstanding receipts',async()=>{
 const h=await harness();try{
 await h.render(false);await h.render(true);assert.equal(h.requests.length,2);
 await h.resolve(1,'reopened');await h.resolve(0,'closed-stale');assert.ok(h.visible('reopened'));assert.ok(!h.visible('closed-stale'));
 await h.query('pending');await h.tick();await h.render(true,'tenant-b/user-b');assert.equal(h.requests.length,4);
 await h.resolve(3,'new-principal');await h.resolve(2,'old-principal');assert.ok(h.visible('new-principal'));assert.ok(!h.visible('old-principal'));
 }finally{await h.close();}
});
test('failed page stops sentinel loop and explicit retry retains cursor and existing results',async()=>{
 const h=await harness();try{
 await h.resolve(0,'initial',true);await h.intersect();await act(async()=>h.requests[1].reject(new Error('synthetic failure')));await h.tick();assert.equal(h.requests.length,2);assert.ok(h.visible('initial'));
 const retry=Array.from(document.querySelectorAll('button')).find(el=>/Retry|重试/.test(el.textContent??''));assert.ok(retry);await act(async()=>retry.click());assert.equal(h.requests[2].params.skip,1);await h.resolve(2,'page',false);assert.ok(h.visible('initial'));assert.ok(h.visible('page'));
 }finally{await h.close();}
});
test('failed initial load can retry page zero',async()=>{
 const h=await harness();try{
 await act(async()=>h.requests[0].reject(new Error('synthetic failure')));
 const retry=Array.from(document.querySelectorAll('button')).find(el=>/Retry|重试/.test(el.textContent??''));assert.ok(retry);await act(async()=>retry.click());assert.equal(h.requests[1].params.skip,0);await h.resolve(1,'retry-result');assert.ok(h.visible('retry-result'));
 }finally{await h.close();}
});
test('real SessionSidebar/AuthProvider tenant switch remounts search and discards old principal receipt',async()=>{
 const {installBrowserAuthTestDb}=await import('../../../hooks/__tests__/browserAuthTestDb');
 installBrowserAuthTestDb();localStorage.clear();sessionStorage.clear();
 Object.defineProperty(navigator,'locks',{configurable:true,value:{async request(_n:any,_o:any,fn:any){return fn();}}});
 const [{AuthProvider,useAuth},{SessionSidebar},{MemoryRouter},{authApi},{default:i18n}]=await Promise.all([import('../../../hooks/useAuth'),import('../SessionSidebar'),import('react-router-dom'),import('../../../services/api'),import('../../../i18n')]);
 const original={bootstrap:authApi.bootstrapAuthContext,user:authApi.getCurrentUser,list:sessionApi.list};
 let principal='a';let auth!:ReturnType<typeof useAuth>;
 authApi.bootstrapAuthContext=async(request)=>({status:'ready',protocol_version:2,generation:request.generation});
 authApi.getCurrentUser=async()=>({id:`user-${principal}`,tenant_id:`tenant-${principal}`,username:'Synthetic',email:'synthetic@example.test',roles:[],permissions:[],is_active:true,is_admin:false,created_at:'',updated_at:''});
 const requests:Array<{resolve:(value:any)=>void}>=[];
 sessionApi.list=(()=>new Promise(resolve=>requests.push({resolve}))) as any;
 function Probe(){auth=useAuth();return createElement(SessionSidebar,{currentSessionId:null,onSelectSession(){},onNewSession(){},sessionSource:{sessions:[],isLoading:false,isLoadingMore:false,hasMore:false,loadMore:async()=>{},softRefresh:async()=>{},prependSession(){},removeSession(){},updateSession(){}}});}
 const root=await createLobeTestRoot(document.createElement('div'));
 try{
 await act(async()=>root.render(createElement(MemoryRouter,null,createElement(AuthProvider,null,createElement(Probe)))));
 await act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});
 assert.equal(auth.user?.id,'user-a');
 const search=document.querySelector<HTMLButtonElement>(`button[aria-label="${i18n.t('sidebar.searchSessions')}"]`);assert.ok(search);
 await act(async()=>search.click());assert.equal(requests.length,1);
 principal='b';await act(async()=>{await auth.refreshUser();});assert.equal(auth.user?.tenant_id,'tenant-b');assert.equal(requests.length,2);
 await act(async()=>requests[1].resolve(result('current-tenant-result')));
 await act(async()=>requests[0].resolve(result('stale-tenant-result')));
 assert.ok(document.body.textContent?.includes('current-tenant-result'));assert.ok(!document.body.textContent?.includes('stale-tenant-result'));
 }finally{await act(async()=>root.unmount());authApi.bootstrapAuthContext=original.bootstrap;authApi.getCurrentUser=original.user;sessionApi.list=original.list;}
});
