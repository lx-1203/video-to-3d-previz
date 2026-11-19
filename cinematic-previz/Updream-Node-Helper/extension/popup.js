(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  async function send(message){
    const response=await chrome.runtime.sendMessage(message);
    if(!response?.ok)throw new Error(response?.error?.message||'插件后台没有响应，请重新加载扩展。');
    return response.result;
  }
  async function check(){
    try{
      const state=await send({type:'UPDREAM_CONNECTION_STATUS'});
      $('popup-status').textContent=state.connected?`本机 MCP 已连接 · 端口 ${state.port}`:state.paired?(state.error||'已配对，正在连接本机 MCP…'):'尚未配对，请导入本机生成的 connection.json。';
    }catch(error){$('popup-status').textContent=error.message;}
    try{
      const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
      const url=new URL(tab?.url||'about:blank');
      if(url.protocol!=='https:'||!['www.updream.cn','updream.cn','updream.bilibili.com'].includes(url.hostname)){$('page-status').textContent='切换到 updream 项目画布可查看页面接口。';return;}
      const page=await send({type:'UPDREAM_PAGE_STATUS',tabId:tab.id});
      $('page-status').textContent=page.enabled?`画布接口已启用 · ${page.tools??0} 个原生工具${page.buildHash?' · 网站 '+page.buildHash:''}${page.error?' · '+page.error:''}`:'当前画布尚未启用接口，请点击下方启用。';
    }catch(error){$('page-status').textContent=error.message;}
  }
  $('pair-file').addEventListener('change',async event=>{
    try{
      const file=event.target.files[0];if(!file)return;
      if(file.size>16384)throw new Error('配对文件过大，请选择本项目生成的 connection.json。');
      let config;try{config=JSON.parse(await file.text());}catch{throw new Error('配对文件格式无效，请重新选择 connection.json。');}
      await send({type:'UPDREAM_PAIR_SAVE',config});
      await check();
    }catch(error){$('popup-status').textContent=error.message;}
  });
  async function activate(enabled){
    try{
      const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
      const url=new URL(tab?.url||'about:blank');
      if(url.protocol!=='https:'||!['www.updream.cn','updream.cn','updream.bilibili.com'].includes(url.hostname))throw new Error('请先切换到 updream 项目画布标签页。');
      await send({type:'UPDREAM_ACTIVATE_TAB',tabId:tab.id,enabled});
      $('popup-status').textContent='正在刷新当前页。之后 Codex 可通过 MCP 查询画布状态。';
    }catch(error){$('popup-status').textContent=error.message;}
  }
  $('check-connection').addEventListener('click',check);
  $('enable-page').addEventListener('click',()=>activate(true));
  $('disable-page').addEventListener('click',()=>activate(false));
  void check();
  const timer=setInterval(()=>void check(),3000);
  window.addEventListener('unload',()=>clearInterval(timer));
})();
