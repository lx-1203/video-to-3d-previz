/* Codex owns this short-lived stdio adapter; the authenticated local daemon is shared. */
const {Client}=require('@modelcontextprotocol/sdk/client/index.js');
const {StreamableHTTPClientTransport}=require('@modelcontextprotocol/sdk/client/streamableHttp.js');
const {Server}=require('@modelcontextprotocol/sdk/server/index.js');
const {StdioServerTransport}=require('@modelcontextprotocol/sdk/server/stdio.js');
const {ListToolsRequestSchema,CallToolRequestSchema}=require('@modelcontextprotocol/sdk/types.js');
const {ensureService}=require('./service.cjs');
const {version}=require('../package.json');
const {randomUUID}=require('node:crypto');
async function startAdapter({service=ensureService}={}){
  const {url,headers:sourceHeaders}=await service();
  const headers={...sourceHeaders};
  headers['X-Updream-Session']=randomUUID();
  const heartbeat=()=>fetch(url+'/session/heartbeat',{method:'POST',headers,signal:AbortSignal.timeout(2000)}).catch(()=>{});
  const timer=setInterval(()=>void heartbeat(),20000);timer.unref();await heartbeat();
  const client=new Client({name:'updream-codex-adapter',version});
  await client.connect(new StreamableHTTPClientTransport(new URL(url+'/mcp'),{requestInit:{headers}}));
  const server=new Server({name:'updream-node-helper',version},{capabilities:{tools:{}}});
  server.setRequestHandler(ListToolsRequestSchema,async()=>client.listTools());
  server.setRequestHandler(CallToolRequestSchema,async(request,extra)=>{
    try{return await client.callTool(request.params,undefined,{timeout:200000,signal:extra.signal});}
    catch{
      const value={ok:false,error:{code:'UNKNOWN_OUTCOME',message:'本机服务连接中断或超时，操作可能已执行。先查询节点状态，不要直接重复提交。'}};
      return{content:[{type:'text',text:JSON.stringify(value)}],structuredContent:value,isError:true};
    }
  });
  let closing=false;
  async function close(){if(closing)return;closing=true;clearInterval(timer);await fetch(url+'/session/close',{method:'POST',headers,signal:AbortSignal.timeout(2000)}).catch(()=>{});await client.close();await server.close();}
  const transport=new StdioServerTransport();
  transport.onclose=()=>{void close();};
  await server.connect(transport);
  // SDK StdioServerTransport listens to data/error, but not the parent's stdin EOF.
  process.stdin.once('end',()=>{void close();});
  process.once('SIGINT',()=>{void close();});process.once('SIGTERM',()=>{void close();});
  return{close};
}
module.exports={startAdapter};
if(require.main===module)startAdapter().catch(error=>{console.error('Updream MCP startup failed: '+error.message);process.exitCode=1;});
