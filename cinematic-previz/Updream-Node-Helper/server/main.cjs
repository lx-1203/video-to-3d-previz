const {loadConfig}=require('./config.cjs');const {createApp}=require('./app.cjs');
(async()=>{
  const config=loadConfig();const app=await createApp(config);
  console.error(`Updream MCP Bridge ${require('../package.json').version} listening at ${app.url}/mcp`);
  let closing=false;
  const close=async()=>{if(closing)return;closing=true;await app.close();process.exit(0);};
  process.once('SIGINT',()=>{void close();});process.once('SIGTERM',()=>{void close();});
})().catch(error=>{console.error(`Updream bridge startup failed: ${error.code||error.message}`);process.exitCode=1;});
