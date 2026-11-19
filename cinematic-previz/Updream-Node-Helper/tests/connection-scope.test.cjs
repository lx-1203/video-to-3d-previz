'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {createHub}=require('../server/hub.cjs');
const {buildTools}=require('../server/tools.cjs');
const {runDiagnose}=require('../scripts/diagnose.cjs');
const {createApp}=require('../server/app.cjs');
const {WebSocket}=require('ws');
const {Client}=require('@modelcontextprotocol/sdk/client/index.js');
const {StreamableHTTPClientTransport}=require('@modelcontextprotocol/sdk/client/streamableHttp.js');

test('Bridge status and diagnosis never claim to inspect the Codex native browser',async()=>{
  const hub=createHub({token:'a'.repeat(64)});
  try{
    const tools=buildTools(hub);
    for(const name of ['updream_connection_status','updream_diagnose']){
      const tool=tools.find(t=>t.name===name),state=await tool.call({});
      assert.equal(state.connectionRoute,'legacy_bridge');
      assert.equal(state.nativeBrowserConnection,'not_checked');
      assert.equal(state.browserConnected,false);
      assert.match(tool.description,/Bridge/);
      assert.match(tool.description,/Codex/);
      if(name==='updream_diagnose')assert.match(state.issues[0].message,/Codex.*原生/);
    }
    await assert.rejects(hub.request({method:'list_tabs'}),error=>{
      assert.equal(error.code,'BROWSER_NOT_CONNECTED');
      assert.match(error.message,/Bridge/);
      assert.match(error.message,/Codex.*原生/);
      return true;
    });
  }finally{hub.close();}
});

test('read-only CLI identifies the route even with an older daemon and never probes tabs offline',async()=>{
  const calls=[];let closed=false;
  const report=await runDiagnose({connect:async()=>({
    callTool:async({name})=>{
      calls.push(name);
      return{structuredContent:{ok:true,data:{mcpVersion:'0.3.1',browserConnected:false,extensionVersion:null,issues:[{code:'BROWSER_NOT_CONNECTED'}]}}};
    },
    close:async()=>{closed=true;},
  })});
  assert.equal(report.status,'browser_not_connected');
  assert.equal(report.connectionRoute,'legacy_bridge');
  assert.equal(report.nativeBrowserConnection,'not_checked');
  assert.match(report.guidance,/Codex.*原生/);
  assert.deepEqual(calls,['updream_diagnose']);
  assert.equal(closed,true);
});

test('failed service diagnosis still leaves the native browser unverified',async()=>{
  const report=await runDiagnose({connect:async()=>{throw Object.assign(new Error('offline'),{code:'SERVICE_NOT_RUNNING'});}});
  assert.equal(report.status,'error');
  assert.equal(report.connectionRoute,'legacy_bridge');
  assert.equal(report.nativeBrowserConnection,'not_checked');
});

test('real MCP with an authenticated extension still does not claim native browser access',async()=>{
  const token='b'.repeat(64),app=await createApp({port:0,token});
  const ws=new WebSocket(app.wsUrl,{origin:'chrome-extension://'+'c'.repeat(32)});
  const client=new Client({name:'scope-regression',version:'1.0.0'});
  try{
    await new Promise((resolve,reject)=>{ws.once('open',resolve);ws.once('error',reject);});
    const ready=new Promise(resolve=>ws.once('message',resolve));
    ws.send(JSON.stringify({type:'auth',protocol:1,token,extensionVersion:require('../package.json').version}));
    await ready;
    await client.connect(new StreamableHTTPClientTransport(new URL(app.url+'/mcp'),{requestInit:{headers:{Authorization:`Bearer ${token}`}}}));
    for(const name of ['updream_connection_status','updream_diagnose']){
      const result=await client.callTool({name,arguments:{}});
      assert.equal(result.structuredContent.ok,true);
      const state=result.structuredContent.data;
      assert.equal(state.browserConnected,true);
      assert.equal(state.connectionRoute,'legacy_bridge');
      assert.equal(state.nativeBrowserConnection,'not_checked');
    }
    assert.equal(app.hub.status().pendingRequests,0);
  }finally{await client.close();ws.terminate();await app.close();}
});
