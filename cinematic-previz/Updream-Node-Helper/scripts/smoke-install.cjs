'use strict';
// This smoke test writes only to its supplied disposable directory. No UI input.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {install}=require('./install.cjs');
const {WebSocket}=require('ws');
const {createWorker}=require('../extension/background.js');
async function smoke(base){
  const installDir=path.join(base,'用户 安装 & test'),codexHome=path.join(base,'codex-home');
  fs.mkdirSync(codexHome,{recursive:true});
  const configFile=path.join(codexHome,'config.toml');
  const original='model = "gpt-6"\n[desktop]\nlocaleOverride = "zh-CN"\n[mcp_servers.other]\ncommand="other"\n';
  fs.writeFileSync(configFile,original);
  let pairing,worker;
  try{
    const first=await install({installDir,codexHome,open:false,shortcut:false,port:49631});
    assert.equal(first.status,'browser_confirmation_required');
    assert.equal(first.shortcutCreated,false);
    pairing=JSON.parse(fs.readFileSync(path.join(installDir,'.local','connection.json'),'utf8'));
    const registered=fs.readFileSync(configFile,'utf8');assert.ok(registered.startsWith(original));
    const second=await install({installDir,codexHome,open:false,shortcut:false,port:49631});
    assert.equal(second.configurationChanged,false);
    assert.equal(fs.readFileSync(configFile,'utf8'),registered);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(installDir,'.local','connection.json'),'utf8')),pairing);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(first.extensionPath,'pairing.local.json'),'utf8')),pairing);
    assert.ok(!fs.existsSync(path.join(installDir,'extension','pairing.local.json')));
    const state=await (await fetch(`http://127.0.0.1:${pairing.port}/health`,{headers:{Authorization:`Bearer ${pairing.token}`}})).json();
    assert.equal(state.name,'updream-node-helper');assert.equal(state.browserConnected,false);
    let stored;
    const chrome={runtime:{getManifest:()=>({version:require('../package.json').version})},storage:{local:{get:async()=>({updreamPairing:stored}),set:async value=>{stored=value.updreamPairing;}}},alarms:{create:async()=>{}}};
    class LocalSocket extends WebSocket{constructor(url){super(url,{origin:'chrome-extension://'+'a'.repeat(32)});}}
    worker=createWorker(chrome,{WebSocket:LocalSocket,loadLocalPairing:async()=>JSON.parse(fs.readFileSync(path.join(first.extensionPath,'pairing.local.json'),'utf8'))});
    await worker.connect();
    for(let i=0;i<50&&!worker.status().connected;i++)await new Promise(resolve=>setTimeout(resolve,50));
    assert.equal(worker.status().connected,true,'Simulated extension must authenticate with actual local daemon');
    // This verifies extension/service protocol, not real browser loading or site support.
    worker.stop();worker=null;
    console.log(JSON.stringify({passed:true,checks:['offline install','real MCP catalog','Chinese and special-character paths','idempotent reinstall','configuration preservation','private extension provisioning','simulated extension real-daemon authentication'],browserUiTested:false}));
  }finally{
    worker?.stop();
    if(pairing)await fetch(`http://127.0.0.1:${pairing.port}/shutdown`,{method:'POST',headers:{Authorization:`Bearer ${pairing.token}`},signal:AbortSignal.timeout(3000)}).catch(()=>{});
  }
}
if(require.main===module)smoke(path.resolve(process.argv[2])).catch(error=>{console.error(error);process.exitCode=1;});
