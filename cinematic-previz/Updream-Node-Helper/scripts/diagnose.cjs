/* Read-only stdio diagnosis. Never creates pairing, starts/restarts the daemon, or opens a workspace. */
'use strict';
const fs=require('node:fs/promises'),path=require('node:path');
const {performance}=require('node:perf_hooks');
const {version}=require('../package.json');
const root=path.join(__dirname,'..');
const fail=code=>Object.assign(new Error(code),{code});
const codeOf=error=>typeof error?.code==='string'&&/^[A-Z][A-Z_]{1,60}$/.test(error.code)?error.code:'DIAGNOSTIC_FAILED';
const versionOf=value=>typeof value==='string'&&/^\d+\.\d+\.\d+(?:[-+.][\w.-]+)?$/.test(value)?value:null;

async function existingService({root:project=root,fetch:request=globalThis.fetch}={}){
  let config;
  try{config=JSON.parse(await fs.readFile(path.join(project,'.local/connection.json'),'utf8'));}
  catch(error){throw fail(error.code==='ENOENT'?'PAIRING_NOT_CONFIGURED':'INVALID_PAIRING');}
  if(config?.protocol!==1||!Number.isSafeInteger(config.port)||config.port<1024||config.port>65535||!/^[a-f0-9]{64}$/.test(config.token||''))throw fail('INVALID_PAIRING');
  const url=`http://127.0.0.1:${config.port}`,headers={Authorization:`Bearer ${config.token}`};
  let response,state;
  try{response=await request(url+'/health',{method:'GET',headers,signal:AbortSignal.timeout(2000)});if(response.ok)state=await response.json();}
  catch{throw fail('SERVICE_NOT_RUNNING');}
  if(!response.ok)throw fail('SERVICE_AUTH_OR_HEALTH_FAILED');
  if(state?.name!=='updream-node-helper')throw fail('UNEXPECTED_SERVICE');
  if(state.version!==version)throw fail('SERVICE_VERSION_MISMATCH');
  return{url,headers};
}

async function connectReadOnly(){
  // Preflight avoids creating credentials or invoking ensureService's upgrade/start path.
  await existingService();
  const {Client}=require('@modelcontextprotocol/sdk/client/index.js');
  const {StdioClientTransport}=require('@modelcontextprotocol/sdk/client/stdio.js');
  const entry=path.join(root,'server/stdio.cjs');
  const source=`require(${JSON.stringify(entry)}).startAdapter({service:require(${JSON.stringify(__filename)}).existingService}).catch(()=>{process.exitCode=1;});`;
  const transport=new StdioClientTransport({command:process.execPath,args:['-e',source],cwd:root,stderr:'pipe'});
  // Do not relay child stderr: unexpected errors may contain local paths or credentials.
  transport.stderr?.resume();
  const client=new Client({name:'updream-readonly-diagnose',version});
  try{await client.connect(transport,{timeout:10000});return client;}
  catch(error){await client.close().catch(()=>{});await transport.close().catch(()=>{});throw fail('STDIO_CONNECT_FAILED');}
}

async function runDiagnose({connect=connectReadOnly,now=()=>performance.now()}={}){
  const start=now(),report={connectionRoute:'legacy_bridge',nativeBrowserConnection:'not_checked',status:'error',serviceAvailable:false,browserConnected:false};let client;
  const read=async name=>{
    const result=await client.callTool({name,arguments:{}},undefined,{timeout:10000});
    let value=result.structuredContent;
    if(!value){try{value=JSON.parse(result.content?.find(c=>c.type==='text')?.text);}catch{throw fail('INVALID_RESULT');}}
    if(value?.ok!==true)throw fail(codeOf(value?.error));
    return value.data;
  };
  try{
    client=await connect();report.serviceAvailable=true;
    const diagnosis=await read('updream_diagnose');
    if(!diagnosis||typeof diagnosis.browserConnected!=='boolean')throw fail('INVALID_RESULT');
    Object.assign(report,{mcpVersion:versionOf(diagnosis.mcpVersion),extensionVersion:versionOf(diagnosis.extensionVersion),browserConnected:diagnosis.browserConnected,pendingRequests:diagnosis.pendingRequests??0,activeMutations:diagnosis.activeMutations??0,issues:(diagnosis.issues??[]).map(i=>({code:codeOf(i),...(i.component==='extension'||i.component==='page'?{component:i.component}:{})}))});
    if(!report.browserConnected){
      report.status='browser_not_connected';
      report.guidance='Bridge 扩展未连接；Codex 内置浏览器的原生连接未检查。使用原生路线时通过宿主浏览器工具读取网页；仅在选择 Bridge 兼容路线时检查扩展加载、配对和标签页启用。';
    }
    else{
      const results=await Promise.allSettled([read('updream_list_tabs'),read('updream_list_workspaces')]);
      const [tabs,workspaces]=results;
      if(tabs.status==='fulfilled'){
        if(!Array.isArray(tabs.value))throw fail('INVALID_RESULT');
        report.tabCount=tabs.value.length;
        report.tabs=tabs.value.slice(0,20).map(t=>({tabId:t.tabId,enabled:t.enabled===true,tools:Number.isSafeInteger(t.tools)?t.tools:null,...(t.error?{error:codeOf(t.error)}:{})}));
      }
      if(workspaces.status==='fulfilled'){
        if(!Array.isArray(workspaces.value?.workspaces))throw fail('INVALID_RESULT');
        const all=workspaces.value.workspaces,claims=all.flatMap(w=>w.claims??[]);
        report.workspaces={count:all.length,claimedCanvases:claims.length,active:claims.filter(c=>c.active).length,uncertain:claims.filter(c=>c.uncertain).length};
      }
      const failed=results.find(r=>r.status==='rejected');if(failed)throw failed.reason;
      const all=tabs.value;
      report.status=report.issues.some(i=>i.code==='BRIDGE_UPGRADE_REQUIRED')?'upgrade_required':!all.length?'no_tabs':all.some(t=>t.enabled&&t.tools>0)?'ready':all.some(t=>t.enabled)?'native_tools_unavailable':'canvas_not_enabled';
    }
  }catch(error){report.status='error';report.error={code:codeOf(error)};}
  finally{
    if(client)try{await client.close();}catch{report.status='error';report.closeError={code:'CLIENT_CLOSE_FAILED'};}
    report.elapsedMs=Math.round((now()-start)*100)/100;
  }
  return report;
}
module.exports={existingService,runDiagnose};
if(require.main===module){
  if(process.argv.length>2){console.log(JSON.stringify({status:'error',error:{code:'INVALID_ARGS'}}));process.exitCode=1;}
  else void runDiagnose().then(result=>{console.log(JSON.stringify(result));process.exitCode=result.status==='ready'?0:result.status==='error'?1:2;});
}
