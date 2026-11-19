'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {createWorker}=require('../extension/background.js');
function environment(stored){
  const state={value:stored,loads:0,sockets:[]};
  const chrome={runtime:{getManifest:()=>({version:'0.3.1'})},storage:{local:{get:async()=>({updreamPairing:state.value}),set:async x=>{state.value=x.updreamPairing;}}},alarms:{create:async()=>{}}};
  class Socket{constructor(url){this.url=url;this.readyState=0;state.sockets.push(this);}close(){this.readyState=3;}send(value){this.sent=value;}}
  const config={protocol:1,port:49218,token:'a'.repeat(64)};
  const options={WebSocket:Socket,setTimeout:()=>1,clearTimeout:()=>{},setInterval:()=>1,clearInterval:()=>{},loadLocalPairing:async()=>{state.loads++;return config;}};
  return{state,chrome,options,config};
}
test('fresh extension automatically imports private local pairing and authenticates',async()=>{
  const env=environment();const worker=createWorker(env.chrome,env.options);
  await worker.connect();
  assert.deepEqual(env.state.value,env.config);
  assert.equal(env.state.loads,1);
  const socket=env.state.sockets[0];socket.readyState=1;socket.onopen();
  assert.equal(JSON.parse(socket.sent).token,env.config.token);
  socket.onmessage({data:JSON.stringify({type:'ready',protocol:1})});
  assert.equal(worker.status().connected,true);worker.stop();
});
test('existing browser pairing is retained, local bootstrap is not read',async()=>{
  const retained={protocol:1,port:49219,token:'c'.repeat(64)};
  const env=environment(retained);const worker=createWorker(env.chrome,env.options);await worker.connect();
  assert.equal(env.state.loads,0);assert.deepEqual(env.state.value,retained);worker.stop();
});
test('unprovisioned source extension remains available for manual pairing',async()=>{
  const env=environment();env.options.loadLocalPairing=async()=>null;
  const worker=createWorker(env.chrome,env.options);await worker.connect();
  assert.equal(worker.status().paired,false);assert.equal(env.state.sockets.length,0);
  await worker.configure(env.config);assert.equal(worker.status().paired,true);worker.stop();
});
test('invalid private pairing is rejected without sending a token',async()=>{
  const env=environment();env.options.loadLocalPairing=async()=>({protocol:1,port:80,token:'bad'});
  const worker=createWorker(env.chrome,env.options);
  await assert.rejects(worker.connect(),/配对文件/);assert.equal(env.state.sockets.length,0);
});
