const {AjvJsonSchemaValidator}=require('@modelcontextprotocol/sdk/validation/ajv-provider.js');
const {unwrap}=require('../extension/client.js');
const {createWorkspaces}=require('./workspaces.cjs');
const {AsyncLocalStorage}=require('node:async_hooks');
const instances=new WeakMap();
const failure=(code,message,details)=>Object.assign(new Error(message),{code,...(details?{details}:{})});
const normalize=name=>name.replace('.','_');
function assertActive(signal){if(signal?.aborted)throw failure('ABORTED','请求已取消，未发送后续操作。');}

function createOperations(hub){
  if(instances.has(hub))return instances.get(hub);
  const workspaces=createWorkspaces(),binding=new AsyncLocalStorage(),schemas=new Map();
  async function identity(tabId,signal){
    const snapshot=await native(tabId,'canvas.getSnapshot',{limit:1},signal);
    if(!['string','number'].includes(typeof snapshot?.projectId)||String(snapshot.projectId).length===0)throw failure('INVALID_PROJECT','无法确认当前画布项目，未执行写入。');
    return{tabId,projectId:String(snapshot.projectId),projectName:snapshot.projectName};
  }
  async function claimCanvas({tabId,workspaceId},sessionId,signal){
    assertActive(signal);
    const page=await hub.request({method:'page',tabId,action:'status'});
    if(page?.bridgeVersion!==require('../package.json').version)throw failure('BRIDGE_UPGRADE_REQUIRED','网页桥接版本过旧；请重新加载扩展并刷新 updream，再认领画布。');
    const target=await identity(tabId,signal);assertActive(signal);
    return workspaces.claim(workspaceId,sessionId,target);
  }
  async function mutate(tabId,work,context={},signal){
    const {workspaceId,sessionId}=context;
    if(!workspaceId)throw failure('WORKSPACE_REQUIRED','写入必须指定独立 workspaceId，并先认领画布。');
    const target=await identity(tabId,signal);assertActive(signal);
    return workspaces.run(workspaceId,sessionId,tabId,target.projectId,()=>binding.run(target,work));
  }
  async function native(tabId,tool,args={},signal){
    assertActive(signal);
    const project=binding.getStore();
    try{return unwrap(await hub.request({method:'page',tabId,action:'execute',tool,arguments:args,...(project?{expectedProjectId:project.projectId}:{})}));}
    catch(error){
      if(project&&/TIMEOUT|ABORT/i.test(error.code||''))throw failure('UNKNOWN_OUTCOME','页面操作已发出，但超时或中止，必须先核对实际状态。',{cause:error.code,writeMayHaveExecuted:true});
      throw error;
    }
  }
  async function catalog(tabId,signal){
    assertActive(signal);const list=await hub.request({method:'page',tabId,action:'list'});
    if(!Array.isArray(list))throw failure('INVALID_CATALOG','页面没有返回有效的工具目录。');
    return new Map(list.filter(t=>/^(canvas|gen)[._][A-Za-z][A-Za-z0-9]*$/.test(t.name)).map(t=>[normalize(t.name),t]));
  }
  function validate(list,tool,args){
    if(!/^(canvas|gen)[._][A-Za-z][A-Za-z0-9]*$/.test(tool))throw failure('TOOL_NOT_ALLOWED','只允许 canvas/gen 节点接口。');
    const descriptor=list.get(normalize(tool));
    if(!descriptor)throw failure('TOOL_UNAVAILABLE',`页面当前未注册 ${tool}，请检查画布和实时目录。`);
    const key=JSON.stringify(descriptor.inputSchema);
    let validator=schemas.get(key);
    if(!validator){
      try{validator=new AjvJsonSchemaValidator().getValidator(descriptor.inputSchema);}
      catch{throw failure('SCHEMA_UNSUPPORTED',`无法验证 ${tool} 的参数格式，本次未执行。`);}
      if(schemas.size>=64)schemas.delete(schemas.keys().next().value);
      schemas.set(key,validator);
    }
    const result=validator(args);
    if(!result.valid)throw failure('INVALID_ARGS',`${tool}: ${result.errorMessage}`);
    return descriptor;
  }
  async function callNativeChecked(tabId,tool,args,signal,context){
    const descriptor=validate(await catalog(tabId,signal),tool,args);
    const work=()=>native(tabId,tool,args,signal);
    return descriptor.annotations?.readOnlyHint===true?work():mutate(tabId,work,context,signal);
  }
  async function batch({tabId,steps},signal,context){
    const list=await catalog(tabId,signal);
    const descriptors=steps.map((step,index)=>{
      try{return validate(list,step.tool,step.arguments??{});}
      catch(error){error.details={...error.details,completed:[],stoppedAt:index+1,preflight:true};throw error;}
    });
    const execute=async()=>{
      const completed=[];
      for(let index=0;index<steps.length;index++){
        const step=steps[index];
        try{const data=await native(tabId,step.tool,step.arguments??{},signal);completed.push({step:index+1,tool:step.tool,data});}
        catch(error){error.details={...error.details,completed,stoppedAt:index+1,remaining:steps.length-index-1};throw error;}
      }
      return{completed,completedCount:completed.length};
    };
    return descriptors.every(d=>d.annotations?.readOnlyHint===true)?execute():mutate(tabId,execute,context,signal);
  }
  async function findNodes({tabId,query='',type,status,offset=0,limit=50,maxScan=2000},signal){
    const nodes=[],seen=new Set(),needle=query.trim().toLocaleLowerCase();let scanned=0,projectId,projectName,totalNodes;
    function result(truncated,nextOffset){return{projectId,projectName,totalNodes,nodes,scanned,truncated,scanLimitReached:truncated&&scanned>=maxScan,...(truncated?{nextOffset}:{})};}
    while(scanned<maxScan){
      const count=Math.min(200,maxScan-scanned);
      const data=await native(tabId,'canvas.getSnapshot',{offset,limit:count},signal);
      if(!data||!Array.isArray(data.nodes)||data.nodes.length>count||data.projectId===undefined||data.projectId===null)throw failure('INVALID_SNAPSHOT','页面快照缺少节点或项目身份，未返回可能混淆的搜索结果。');
      if(projectId!==undefined&&data.projectId!==projectId)throw failure('PROJECT_CHANGED','搜索途中项目已变化，请重新读取标签页。');
      projectId=data.projectId;projectName=data.projectName;totalNodes=data.totalNodes;
      if(data.truncated&&(!Number.isSafeInteger(data.nextOffset)||data.nextOffset<=offset||data.nodes.length===0))throw failure('INVALID_CURSOR','页面分页位置没有前进，搜索已停止。');
      for(let index=0;index<data.nodes.length;index++){
        const node=data.nodes[index];scanned++;
        const matches=(!type||node.type===type)&&(!status||node.status===status)&&[node.id,node.name,node.prompt].join(' ').toLocaleLowerCase().includes(needle);
        if(matches&&!seen.has(node.id)){nodes.push(node);seen.add(node.id);}
        if(nodes.length===limit){const more=index+1<data.nodes.length||!!data.truncated;return result(more,offset+index+1);}
      }
      if(!data.truncated)return result(false);
      offset=data.nextOffset;
    }
    return result(true,offset);
  }
  async function updateChecked({tabId,nodeId,expected,changes},signal,context){
    return mutate(tabId,async()=>{
      const before=await native(tabId,'canvas.getNode',{nodeId},signal);
      if(before?.id!==nodeId)throw failure('INVALID_NODE','页面返回的节点身份与请求不一致。');
      const fields=Object.keys(changes);
      if(fields.includes('prompt')&&typeof before.editDraft?.prompt!=='string')throw failure('DRAFT_UNAVAILABLE','没有完整提示词草稿，不能把摘要当作完整内容覆盖。');
      const conflicts=fields.filter(key=>(key==='prompt'?before.editDraft.prompt:before[key])!==expected[key]);
      if(conflicts.length)throw failure('CONFLICT','节点内容已变化，本次未写入。',{nodeId,fields:conflicts});
      await native(tabId,'canvas.updateNode',{nodeId,...changes},signal);
      let after;
      try{after=await native(tabId,'canvas.getNode',{nodeId},signal);}
      catch(error){throw failure('VERIFY_FAILED','修改已提交，但无法核对读回内容；先检查实际节点，不要直接重试。',{nodeId,cause:error.code,writeMayHaveExecuted:true});}
      const mismatch=fields.filter(key=>(key==='prompt'?after?.editDraft?.prompt:after?.[key])!==changes[key]);
      if(after?.id!==nodeId||mismatch.length)throw failure('VERIFY_FAILED','修改已提交，但读回内容未一致；请核对节点实际状态。',{nodeId,fields:mismatch,writeMayHaveExecuted:true});
      return{nodeId,verified:true,node:after};
    },context,signal);
  }
  async function diagnose(tabId,signal){
    const state={mcpVersion:require('../package.json').version,protocol:1,...hub.status(),activeMutations:workspaces.activeWrites,claimedCanvases:workspaces.claimCount,issues:[]};
    if(!state.browserConnected){state.issues.push({code:'BROWSER_NOT_CONNECTED',message:'Bridge 浏览器扩展未连接；Codex 内置浏览器的原生连接未检查。原生路线请使用宿主浏览器工具；仅在选择 Bridge 兼容路线时检查扩展加载、配对和标签页启用。'});return state;}
    if(typeof state.extensionVersion==='string'&&state.extensionVersion!==state.mcpVersion)state.issues.push({code:'BRIDGE_UPGRADE_REQUIRED',component:'extension',message:'浏览器扩展版本与本机服务不一致；请重新加载扩展并刷新 updream。'});
    if(tabId===undefined)return state;
    assertActive(signal);
    const [page,list]=await Promise.allSettled([hub.request({method:'page',tabId,action:'status'}),hub.request({method:'page',tabId,action:'list'})]);
    state.tabId=tabId;
    if(page.status==='fulfilled')state.page=page.value;else state.issues.push({code:page.reason.code||'PAGE_ERROR',message:page.reason.message});
    if(typeof state.page?.bridgeVersion==='string'&&state.page.bridgeVersion!==state.mcpVersion)state.issues.push({code:'BRIDGE_UPGRADE_REQUIRED',component:'page',message:'网页桥接版本与本机服务不一致；请重新加载扩展并刷新 updream。'});
    if(list.status==='fulfilled'&&Array.isArray(list.value)){state.nativeToolCount=list.value.length;state.nativeToolNames=list.value.map(t=>t.name);}
    else state.issues.push({code:'CATALOG_UNAVAILABLE',message:'无法读取页面工具目录。'});
    if(state.page?.enabled===false)state.issues.push({code:'PAGE_NOT_ENABLED',message:'请在插件中启用当前画布。'});
    if(state.nativeToolCount===0)state.issues.push({code:'NATIVE_TOOLS_UNAVAILABLE',message:'页面未注册节点接口，请进入项目画布后刷新。'});
    return state;
  }
  const api={native,mutate,callNativeChecked,batch,findNodes,updateChecked,diagnose,claimCanvas,workspaces,get activeWrites(){return workspaces.activeWrites;}};
  instances.set(hub,api);return api;
}
module.exports={createOperations};
