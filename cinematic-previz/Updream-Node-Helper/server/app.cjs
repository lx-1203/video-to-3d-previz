const http=require('node:http');
const {WebSocketServer}=require('ws');
const {StreamableHTTPServerTransport}=require('@modelcontextprotocol/sdk/server/streamableHttp.js');
const {createHub,equalSecret}=require('./hub.cjs');
const {createMcpServer}=require('./tools.cjs');
const {createOperations}=require('./operations.cjs');
const {version}=require('../package.json');
const {sessionPattern}=require('./workspaces.cjs');

async function createApp({port=47631,token,timeoutMs}={}){
  if(!/^[a-f0-9]{64}$/.test(token||''))throw new Error('Bridge requires a 32-byte pairing token.');
  const hub=createHub({token,timeoutMs});
  const active=new Set(),requests=new Map();
  const operations=createOperations(hub);let closing=null,shuttingDown=false;
  const send=(res,code,value)=>{res.writeHead(code,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
  const server=http.createServer((req,res)=>{void handle(req,res).catch(()=>{if(!res.headersSent)send(res,500,{error:'Internal bridge error'});else res.end();});});
  function validHost(req){return req.headers.host===`127.0.0.1:${server.address()?.port}`;}
  async function handle(req,res){
    if(!validHost(req)||req.headers.origin){send(res,403,{error:'Origin or host rejected'});return;}
    if(!equalSecret(req.headers.authorization,`Bearer ${token}`)){send(res,401,{error:'Pairing required'});return;}
    if(req.url==='/health'&&req.method==='GET'){send(res,200,{name:'updream-node-helper',version,protocol:1,...hub.status(),activeMutations:operations.activeWrites,claimedCanvases:operations.workspaces.claimCount});return;}
    if(req.url==='/shutdown'&&req.method==='POST'){
      if(hub.status().pendingRequests||operations.activeWrites||operations.workspaces.claimCount){send(res,409,{error:'BUSY'});return;}
      shuttingDown=true;res.once('finish',()=>{void close();});send(res,202,{stopping:true});return;
    }
    if(shuttingDown){send(res,503,{error:'Service is stopping'});return;}
    const sessionId=req.headers['x-updream-session'];
    if(sessionId!==undefined&&!sessionPattern.test(sessionId)){send(res,400,{error:'Invalid client session'});return;}
    if(sessionId)operations.workspaces.touch(sessionId);
    if(req.url==='/session/heartbeat'&&req.method==='POST'&&sessionId){send(res,200,{alive:true});return;}
    if(req.url==='/session/close'&&req.method==='POST'&&sessionId){
      operations.workspaces.endSession(sessionId);
      for(const entry of active)if(entry.sessionId===sessionId)entry.controller.abort();
      send(res,200,{closed:true});return;
    }
    if(req.url!=='/mcp'){send(res,404,{error:'Not found'});return;}
    if(req.method!=='POST'){send(res,405,{error:'Method not allowed'});return;}
    if(!req.headers['content-type']?.startsWith('application/json')){send(res,415,{error:'JSON required'});return;}
    const chunks=[];let bytes=0;
    for await(const chunk of req){bytes+=chunk.length;if(bytes>1024*1024){send(res,413,{error:'Request too large'});return;}chunks.push(chunk);}
    let body;
    try{body=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{send(res,400,{error:'Invalid JSON'});return;}
    if(body?.method==='notifications/cancelled'){
      if(sessionId)requests.get(JSON.stringify([sessionId,body.params?.requestId]))?.abort();
      send(res,202,{});return;
    }
    const key=sessionId&&body?.id!==undefined?JSON.stringify([sessionId,body.id]):null;
    if(key&&requests.has(key)){send(res,409,{error:'Duplicate active request ID'});return;}
    const controller=new AbortController();if(key)requests.set(key,controller);
    const mcp=createMcpServer(hub,{sessionId,signal:controller.signal});
    const transport=new StreamableHTTPServerTransport({sessionIdGenerator:undefined,enableJsonResponse:true});
    const entry={mcp,transport,sessionId,controller};active.add(entry);
    res.once('close',()=>{controller.abort();if(key)requests.delete(key);active.delete(entry);void transport.close().catch(()=>{});void mcp.close().catch(()=>{});});
    await mcp.connect(transport);
    await transport.handleRequest(req,res,body);
  }
  // A 200-node snapshot with full 20k-character edit drafts can exceed 16 MB.
  const wss=new WebSocketServer({noServer:true,maxPayload:32*1024*1024,perMessageDeflate:false});
  wss.on('connection',socket=>hub.attach(socket));
  server.on('upgrade',(req,socket,head)=>{
    if(req.url!=='/bridge'||!validHost(req)||!/^chrome-extension:\/\/[a-p]{32}$/.test(req.headers.origin||'')){
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');return;
    }
    wss.handleUpgrade(req,socket,head,ws=>wss.emit('connection',ws,req));
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,'127.0.0.1',resolve);});
  const url=`http://127.0.0.1:${server.address().port}`;
  function close(){
    if(!closing)closing=(async()=>{
      shuttingDown=true;hub.close();
      for(const {mcp,transport}of active){await transport.close();await mcp.close();}
      await new Promise(resolve=>wss.close(resolve));
      server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
    })();
    return closing;
  }
  return{server,hub,url,wsUrl:url.replace('http:','ws:')+'/bridge',close};
}
module.exports={createApp};
