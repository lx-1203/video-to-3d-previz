/* Extension service worker. The pairing token never enters the website world. */
'use strict';
function createWorker(chrome,options={}){
  const Socket=options.WebSocket||globalThis.WebSocket;
  const every=options.setInterval||setInterval,cancelEvery=options.clearInterval||clearInterval;
  const later=options.setTimeout||setTimeout,cancelLater=options.clearTimeout||clearTimeout,now=options.now||Date.now;
  const extensionVersion=chrome.runtime?.getManifest?.().version||'unknown';
  const patterns=['https://www.updream.cn/*','https://updream.cn/*','https://updream.bilibili.com/*'];
  const fail=(code,message)=>Object.assign(new Error(message),{code});
  let socket=null,paired=false,port=null,authenticated=false,heartbeat=null,lastError='';
  let generation=0,handshake=null,lastPong=0;
  function validTab(tab){
    try{const url=new URL(tab?.url);return url.protocol==='https:'&&['www.updream.cn','updream.cn','updream.bilibili.com'].includes(url.hostname);}catch{return false;}
  }
  async function page(request,stillConnected=()=>true){
    if(!Number.isSafeInteger(request.tabId)||request.tabId<0)throw fail('INVALID_TAB','需要显式指定真实 tabId。');
    let tab;
    try{tab=await chrome.tabs.get(request.tabId);}catch{throw fail('INVALID_TAB','目标标签页已关闭，请重新读取标签页列表。');}
    if(!validTab(tab))throw fail('INVALID_TAB','目标标签页不是受支持的 updream 页面。');
    if(!['status','list','execute','enable','disable'].includes(request.action))throw fail('INVALID_ACTION','不支持的页面操作。');
    if(request.action==='execute'&&!/^(canvas|gen)[._][A-Za-z][A-Za-z0-9]*$/.test(request.tool||''))throw fail('TOOL_NOT_ALLOWED','只允许 canvas/gen 节点工具。');
    if(!stillConnected())throw fail('DISCONNECTED','连接已中断，本次尚未转发到页面。');
    let response;
    try{response=await chrome.tabs.sendMessage(request.tabId,{type:'UPDREAM_MCP_PAGE',action:request.action,tool:request.tool,arguments:request.arguments,expectedProjectId:request.expectedProjectId});}
    catch{throw fail(request.action==='execute'?'UNKNOWN_OUTCOME':'PAGE_UNAVAILABLE','没有收到页面桥接响应；请刷新 updream，并在插件中启用当前画布。已发出的操作结果可能不明确，先核对节点。');}
    if(!response?.ok)throw fail(response?.error?.code||'PAGE_ERROR',response?.error?.message||'页面接口返回错误。');
    return response.result;
  }
  async function route(request,stillConnected){
    if(request.method==='page')return page(request,stillConnected);
    if(request.method==='list_tabs'){
      const tabs=(await chrome.tabs.query({url:patterns})).filter(validTab);
      return Promise.all(tabs.map(async tab=>{
        const info={tabId:tab.id,title:tab.title||'',url:tab.url};
        try{return{...info,...await page({tabId:tab.id,action:'status'})};}
        catch(error){return{...info,enabled:false,error:{code:error.code,message:error.message}};}
      }));
    }
    throw fail('INVALID_METHOD','未知桥接方法。');
  }
  const status=()=>({paired,connected:authenticated&&socket?.readyState===1,port,extensionVersion,protocol:1,error:lastError});
  function clearTimers(){cancelEvery(heartbeat);cancelLater(handshake);heartbeat=null;handshake=null;}
  function stop(){
    generation++;authenticated=false;clearTimers();
    const old=socket;socket=null;if(old)old.close();
  }
  async function connect(){
    if(socket&&(socket.readyState===0||socket.readyState===1))return status();
    const attempt=++generation;
    const stored=await chrome.storage.local.get('updreamPairing');
    if(attempt!==generation)return status();
    let config=stored.updreamPairing;
    if(!config&&options.loadLocalPairing){
      config=await options.loadLocalPairing();
      if(attempt!==generation)return status();
      if(config){validate(config);await chrome.storage.local.set({updreamPairing:config});}
    }
    if(!config){paired=false;return status();}
    validate(config);paired=true;port=config.port;
    await chrome.alarms.create('updream-reconnect',{periodInMinutes:.5});
    if(attempt!==generation)return status();
    const ws=new Socket(`ws://127.0.0.1:${config.port}/bridge`);socket=ws;
    const ids=new Set(),completed=new Map();
    const current=()=>socket===ws&&authenticated&&ws.readyState===1;
    handshake=later(()=>{if(socket===ws)ws.close();},10000);
    ws.onopen=()=>{if(socket===ws)ws.send(JSON.stringify({type:'auth',protocol:1,token:config.token,extensionVersion}));};
    ws.onmessage=event=>{
      if(socket!==ws)return;
      let request;try{request=JSON.parse(event.data);}catch{ws.close();return;}
      if(request?.type==='ready'){
        if(request.protocol!==1){ws.close();return;}
        authenticated=true;lastError='';clearTimers();lastPong=now();
        heartbeat=every(()=>{if(!current())return;if(now()-lastPong>45000){ws.close();return;}ws.send(JSON.stringify({type:'ping'}));},20000);return;
      }
      if(current()&&request?.type==='pong'){lastPong=now();return;}
      if(!current()||request?.type!=='request'||typeof request.id!=='string'||request.id.length>120||ids.has(request.id))return;
      if(completed.has(request.id)){ws.send(JSON.stringify(completed.get(request.id)));return;}
      ids.add(request.id);
      const respond=payload=>{
        // Retain IDs only: replaying a large snapshot must not retain 256 full drafts.
        completed.set(request.id,{type:'response',id:request.id,error:{code:'DUPLICATE_REQUEST',message:'该请求已处理，不能重复执行；请查询节点状态。'}});
        if(completed.size>256)completed.delete(completed.keys().next().value);
        if(current())ws.send(JSON.stringify({type:'response',id:request.id,...payload}));
      };
      route(request,current).then(result=>respond({result}),error=>{
        respond({error:{code:error.code||'BRIDGE_ERROR',message:String(error.message||error)}});
      }).finally(()=>ids.delete(request.id));
    };
    ws.onclose=event=>{
      if(socket!==ws)return;
      socket=null;authenticated=false;clearTimers();
      lastError=event.code===1008?'配对被拒绝。确认只连接一个浏览器，并重新导入本机配对文件。':'本机服务未连接，稍后自动重连；也可点击检查连接。';
    };
    ws.onerror=()=>{if(socket===ws)lastError='无法连接本机 MCP 服务，请运行项目设置脚本。';};
    return status();
  }
  function validate(config){
    if(config?.protocol!==1||!Number.isSafeInteger(config.port)||config.port<1024||config.port>65535||!/^[a-f0-9]{64}$/.test(config.token||''))throw new Error('配对文件无效，请选择本项目生成的 connection.json。');
  }
  async function configure(config){validate(config);await chrome.storage.local.set({updreamPairing:{protocol:1,port:config.port,token:config.token}});stop();return connect();}
  return{route,connect,configure,status,stop};
}

if(typeof module!=='undefined'&&module.exports)module.exports={createWorker};
else{
  const worker=createWorker(chrome,{loadLocalPairing:async()=>{
    // This resource exists only in a private, locally provisioned extension copy.
    // It is never exposed to websites and is excluded from source/release archives.
    try{
      const response=await fetch(chrome.runtime.getURL('pairing.local.json'),{cache:'no-store'});
      return response.ok?await response.json():null;
    }catch{return null;}
  }});
  const reconnect=()=>{void worker.connect().catch(()=>{});};
  chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name==='updream-reconnect')reconnect();});
  chrome.runtime.onInstalled.addListener(reconnect);
  chrome.runtime.onStartup.addListener(reconnect);
  chrome.runtime.onMessage.addListener((message,sender,reply)=>{
    if(sender.id!==chrome.runtime.id||sender.tab)return false;
    let task;
    if(message?.type==='UPDREAM_PAIR_SAVE')task=worker.configure(message.config);
    else if(message?.type==='UPDREAM_CONNECTION_STATUS')task=worker.connect().then(()=>worker.status());
    else if(message?.type==='UPDREAM_ACTIVATE_TAB')task=worker.route({method:'page',tabId:message.tabId,action:message.enabled?'enable':'disable'});
    else if(message?.type==='UPDREAM_PAGE_STATUS')task=worker.route({method:'page',tabId:message.tabId,action:'status'});
    else return false;
    task.then(result=>reply({ok:true,result}),error=>reply({ok:false,error:{message:error.message,code:error.code}}));return true;
  });
  reconnect();
}
