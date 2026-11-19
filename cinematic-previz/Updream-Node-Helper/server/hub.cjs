const {randomUUID,timingSafeEqual}=require('node:crypto');
const failure=(code,message)=>Object.assign(new Error(message),{code});
function equalSecret(a,b){
  if(typeof a!=='string'||typeof b!=='string')return false;
  const aa=Buffer.from(a),bb=Buffer.from(b);
  return aa.length===bb.length&&timingSafeEqual(aa,bb);
}
function createHub({token,timeoutMs=185000}){
  let browser=null,extensionVersion=null;
  const pending=new Map();
  const sockets=new Set();
  function rejectAll(){
    for(const p of pending.values()){
      clearTimeout(p.timer);p.reject(failure('UNKNOWN_OUTCOME','浏览器连接中断；操作可能已执行。先查询节点状态，不要直接重复提交。'));
    }
    pending.clear();
  }
  function attach(socket){
    sockets.add(socket);
    let authenticated=false;
    let lastSeen=Date.now();
    const authTimer=setTimeout(()=>socket.close(1008,'Authentication required'),3000);authTimer.unref();
    const watchdog=setInterval(()=>{if(Date.now()-lastSeen>90000)socket.terminate();},30000);watchdog.unref();
    socket.on('error',()=>{});
    socket.on('message',raw=>{
      let message;
      try{message=JSON.parse(raw);}catch{socket.close(1008,'Invalid JSON');return;}
      if(!authenticated){
        if(message?.type!=='auth'||message.protocol!==1||!equalSecret(message.token,token)||browser){socket.close(1008,'Pairing rejected');return;}
        authenticated=true;browser=socket;clearTimeout(authTimer);
        extensionVersion=typeof message.extensionVersion==='string'?message.extensionVersion.slice(0,40):null;
        socket.send(JSON.stringify({type:'ready',protocol:1}));return;
      }
      lastSeen=Date.now();
      if(message?.type==='ping'){socket.send(JSON.stringify({type:'pong'}));return;}
      if(message?.type!=='response'||typeof message.id!=='string')return;
      const p=pending.get(message.id);if(!p||p.socket!==socket)return;
      pending.delete(message.id);clearTimeout(p.timer);
      if(message.error)p.reject(failure(message.error.code||'BROWSER_ERROR',String(message.error.message||'浏览器操作失败。')));
      else p.resolve(message.result);
    });
    socket.on('close',()=>{
      clearTimeout(authTimer);clearInterval(watchdog);sockets.delete(socket);
      if(browser===socket){browser=null;extensionVersion=null;rejectAll();}
    });
  }
  function request(payload){
    if(!browser||browser.readyState!==1)return Promise.reject(failure('BROWSER_NOT_CONNECTED','Bridge 浏览器扩展未连接；这不表示 Codex 内置浏览器的原生路线断开。使用 Codex 原生路线时请通过宿主浏览器工具读取网页；只有选择 Bridge 兼容路线时才检查扩展加载、配对和标签页启用。'));
    if(pending.size>=64)return Promise.reject(failure('BUSY','本机桥接已有过多请求，请等待已发出的请求返回。'));
    const socket=browser,id=randomUUID();
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{
        pending.delete(id);reject(failure('UNKNOWN_OUTCOME','等待浏览器结果超时；页面中的操作可能仍在执行。先查询状态，不要直接重试。'));
      },timeoutMs);
      pending.set(id,{resolve,reject,timer,socket});
      socket.send(JSON.stringify({type:'request',id,...payload}),error=>{
        if(error&&pending.has(id)){clearTimeout(timer);pending.delete(id);reject(failure('UNKNOWN_OUTCOME','发送时浏览器连接中断，请先核对节点状态。'));}
      });
    });
  }
  return{attach,request,status:()=>({connectionRoute:'legacy_bridge',nativeBrowserConnection:'not_checked',browserConnected:!!browser&&browser.readyState===1,pendingRequests:pending.size,extensionVersion}),close(){rejectAll();for(const s of sockets)s.terminate();}};
}
module.exports={createHub,equalSecret};
