const fs=require('node:fs');const path=require('node:path');const {spawn}=require('node:child_process');
const {loadConfig}=require('./config.cjs');
const {version}=require('../package.json');
async function ensureService({nodePath=process.execPath}={}){
  const root=path.join(__dirname,'..'),config=loadConfig(root);
  const url=`http://127.0.0.1:${config.port}`,headers={Authorization:`Bearer ${config.token}`};
  async function health(){
    try{const response=await fetch(url+'/health',{headers,signal:AbortSignal.timeout(1000)});return response.ok?await response.json():null;}catch{return null;}
  }
  let state=await health();
  if(state?.name==='updream-node-helper'&&state.version!==version){
    if(state.pendingRequests||state.activeMutations||state.claimedCanvases)throw new Error('旧版桥接仍有工作未释放，请结束工作后再启动。');
    const response=await fetch(url+'/shutdown',{method:'POST',headers,signal:AbortSignal.timeout(2000)});
    if(!response.ok)throw new Error('旧版本机服务不能安全自动升级，请核对其进程后关闭，再运行 setup。');
    for(let i=0;i<30&&state;i++){await new Promise(resolve=>setTimeout(resolve,100));state=await health();}
    if(state)throw new Error('旧版桥接尚未退出，请稍后重试。');
  }
  if(!state){
    const log=fs.openSync(path.join(root,'.local/server.log'),'a');
    const child=spawn(nodePath,[path.join(root,'server/main.cjs')],{cwd:root,detached:true,windowsHide:true,stdio:['ignore',log,log]});
    child.once('error',()=>{});child.unref();fs.closeSync(log);
    for(let i=0;i<25&&!state;i++){await new Promise(resolve=>setTimeout(resolve,100));state=await health();}
  }
  if(state?.name!=='updream-node-helper'||state.version!==version)throw new Error('Local bridge failed to start or port is occupied; inspect .local/server.log.');
  return{url,headers};
}
module.exports={ensureService};
