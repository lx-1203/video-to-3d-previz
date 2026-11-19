'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {createApp}=require('../server/app.cjs');
const {checkIdle}=require('../scripts/install.cjs');
const {WebSocket}=require('ws');
const token='d'.repeat(64);
test('installer accepts an idle real daemon and rejects wrong credentials',async()=>{
  const app=await createApp({port:0,token});
  const port=Number(new URL(app.url).port);
  try{
    await checkIdle({port,token});
    await assert.rejects(checkIdle({port,token:'e'.repeat(64)}),/cannot be authenticated/);
  }finally{await app.close();}
});
test('service rejects website origins and unauthenticated MCP requests',async()=>{
  const app=await createApp({port:0,token});
  try{
    assert.equal((await fetch(app.url+'/health')).status,401);
    assert.equal((await fetch(app.url+'/health',{headers:{Origin:'https://www.updream.cn',Authorization:`Bearer ${token}`}})).status,403);
    const ws=new WebSocket(app.wsUrl,{origin:'https://www.updream.cn'});
    const rejected=await new Promise(resolve=>{ws.on('unexpected-response',(_,response)=>{response.resume();resolve(response.statusCode);ws.terminate();});ws.on('error',()=>{});});
    assert.equal(rejected,403);
  }finally{await app.close();}
});
test('installer refuses to overwrite a service with an in-flight browser request',async()=>{
  const app=await createApp({port:0,token});
  // Real pending request keeps the daemon busy, even before page response.
  const ws=new WebSocket(app.wsUrl,{origin:'chrome-extension://'+'b'.repeat(32)});
  try{
    await new Promise((resolve,reject)=>{ws.once('open',resolve);ws.once('error',reject);});
    ws.send(JSON.stringify({type:'auth',protocol:1,token,extensionVersion:'0.3.1'}));
    await new Promise(resolve=>ws.once('message',resolve));
    const pending=app.hub.request({method:'list_tabs'}).catch(()=>null);
    await assert.rejects(checkIdle({port:Number(new URL(app.url).port),token}),/busy/);
    ws.terminate();await pending;
  }finally{ws.terminate();await app.close();}
});
