import assert from 'node:assert/strict';
import test from 'node:test';
import { CdpClient } from './browser-smoke-harness.mjs';

class FakeSocket extends EventTarget {
  static current;
  sent=[];
  constructor() { super();FakeSocket.current=this;queueMicrotask(()=>this.dispatchEvent(new Event('open'))); }
  send(value) { this.sent.push(JSON.parse(value)); }
  close() {}
  respond(value) { this.dispatchEvent(new MessageEvent('message',{data:JSON.stringify(value)})); }
}

test('CDP events and flattened sessions preserve the existing response owner', async()=>{
  const old=globalThis.WebSocket;globalThis.WebSocket=FakeSocket;
  try {
    const client=new CdpClient('ws://synthetic.invalid',{timeoutMs:1000});await client.connect();
    const socket=FakeSocket.current;const events=[];
    const off=client.on('Fetch.requestPaused',(params,sessionId)=>events.push({params,sessionId}));
    socket.respond({method:'Fetch.requestPaused',params:{requestId:'child-request'},sessionId:'child'});
    assert.deepEqual(events,[{params:{requestId:'child-request'},sessionId:'child'}]);
    const first=client.send('Fetch.failRequest',{requestId:'child-request',errorReason:'BlockedByClient'},'child');
    assert.equal(socket.sent[0].sessionId,'child');
    socket.respond({id:socket.sent[0].id,result:{},sessionId:'child'});await first;
    off();socket.respond({method:'Fetch.requestPaused',params:{requestId:'ignored'}});assert.equal(events.length,1);
    const evaluation=client.evaluate('document.body.textContent',{contextId:9,sessionId:'child'});
    assert.equal(socket.sent[1].params.contextId,9);assert.equal(socket.sent[1].sessionId,'child');
    socket.respond({id:socket.sent[1].id,result:{result:{value:'synthetic'}},sessionId:'child'});
    assert.equal(await evaluation,'synthetic');
    const legacy=client.send('Page.enable');assert.ok(!('sessionId' in socket.sent[2]));
    socket.respond({id:socket.sent[2].id,result:{}});await legacy;
    client.close();assert.equal(client.listeners.size,0);
  } finally {globalThis.WebSocket=old;}
});

test('CDP child errors reject their own request and close rejects remaining work',async()=>{
  const old=globalThis.WebSocket;globalThis.WebSocket=FakeSocket;
  try {
    const client=new CdpClient('ws://synthetic.invalid',{timeoutMs:1000});await client.connect();
    const p=client.send('Fetch.enable',{},'child');
    const rejection=assert.rejects(p,/unsupported/);
    FakeSocket.current.respond({id:1,error:{message:'unsupported'},sessionId:'child'});await rejection;
    const pending=client.send('Page.enable');const closed=assert.rejects(pending,/cdp_closed/);client.close();await closed;
  } finally {globalThis.WebSocket=old;}
});
