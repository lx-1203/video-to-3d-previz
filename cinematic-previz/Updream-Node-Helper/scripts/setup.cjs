const path=require('node:path');
const os=require('node:os');
const {ensureService}=require('../server/service.cjs');
const {configureCodex}=require('./configure-codex.cjs');
const {Client}=require('@modelcontextprotocol/sdk/client/index.js');
const {StreamableHTTPClientTransport}=require('@modelcontextprotocol/sdk/client/streamableHttp.js');
const root=path.join(__dirname,'..');
async function verifyService(options){
  const {url,headers}=await ensureService(options);
  const client=new Client({name:'updream-setup',version:require('../package.json').version});
  try{
    await client.connect(new StreamableHTTPClientTransport(new URL(url+'/mcp'),{requestInit:{headers}}));
    const {tools}=await client.listTools();
    const expected=require('../server/tools.cjs').buildTools({status:()=>({})}).map(t=>t.name).sort();
    if(JSON.stringify(tools.map(t=>t.name).sort())!==JSON.stringify(expected))throw new Error('MCP tool catalog verification failed.');
    return{toolCount:tools.length};
  }finally{await client.close();}
}
module.exports={verifyService};
if(require.main===module){
  (async()=>{
    const result=await verifyService();
    const config=path.join(process.env.CODEX_HOME||path.join(os.homedir(),'.codex'),'config.toml');
    configureCodex(config,{command:process.execPath,entry:path.join(root,'server','stdio.cjs')});
    console.log(`MCP verified: ${result.toolCount} tools. Configured ${config}`);
    console.log('For automatic browser pairing use the Windows one-click installer.');
  })().catch(error=>{console.error('Setup failed: '+error.message);process.exitCode=1;});
}
