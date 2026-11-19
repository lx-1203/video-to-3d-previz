(() => {
  'use strict';
  if(globalThis.__updreamMcpContent)return;
  globalThis.__updreamMcpContent=true;
  const client=globalThis.UpdreamHelperClient.createClient(window);
  chrome.runtime.onMessage.addListener((message,sender,reply)=>{
    if(sender.id!==chrome.runtime.id||message?.type!=='UPDREAM_MCP_PAGE')return false;
    if(!['status','list','execute','enable','disable'].includes(message.action)){
      reply({ok:false,error:{code:'INVALID_ACTION',message:'不支持的页面操作。'}});return false;
    }
    const {action,tool,arguments:args,expectedProjectId}=message;
    client.request(action,{tool,arguments:args,expectedProjectId}).then(result=>reply({ok:true,result}),error=>reply({ok:false,error:{code:error.code||'PAGE_ERROR',message:error.message}}));
    return true;
  });
})();
