'use strict';
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const net=require('node:net');
const {createHash}=require('node:crypto');
const {spawnSync}=require('node:child_process');
const {configureCodex,configureCodexText,NAME,MARKER}=require('./configure-codex.cjs');
const {loadConfig}=require('../server/config.cjs');

function safeRelative(name){
  return typeof name==='string'&&name.length>0&&!name.includes('\\')&&!path.posix.isAbsolute(name)&&!name.includes(':')&&name.split('/').every(p=>p&&p!=='.'&&p!=='..'&&!['.local','.git','pairing.local.json','connection.json'].includes(p));
}
function regularPath(root,name){
  if(!safeRelative(name))throw new Error('Unsafe package path: '+name);
  let current=root;
  for(const part of name.split('/')){current=path.join(current,part);if(fs.lstatSync(current).isSymbolicLink())throw new Error('Unsafe package symlink: '+name);}
  if(!fs.statSync(current).isFile())throw new Error('Package file missing: '+name);
  return current;
}
function verifyBundle(root){
  const manifest=JSON.parse(fs.readFileSync(path.join(root,'bundle-manifest.json'),'utf8'));
  if(!manifest.version||!manifest.files||typeof manifest.files!=='object'||Array.isArray(manifest.files))throw new Error('Invalid bundle manifest');
  for(const [name,expected]of Object.entries(manifest.files)){
    const file=regularPath(root,name);
    if(!/^[a-f0-9]{64}$/.test(expected)||createHash('sha256').update(fs.readFileSync(file)).digest('hex')!==expected)throw new Error('Package checksum mismatch: '+name);
  }
  return manifest;
}
function assertNoSymlinkAncestors(target){
  for(let p=path.resolve(target);;p=path.dirname(p)){
    if(fs.existsSync(p)&&fs.lstatSync(p).isSymbolicLink())throw new Error('Installation path contains a symbolic link/junction: '+p);
    if(path.dirname(p)===p)break;
  }
}
function provisionExtension(root,config){
  const target=path.join(root,'.local','extension');assertNoSymlinkAncestors(target);
  fs.mkdirSync(target,{recursive:true});
  for(const file of fs.readdirSync(path.join(root,'extension'))){
    const source=path.join(root,'extension',file);
    if(!fs.lstatSync(source).isFile())throw new Error('Unexpected extension entry');
    fs.copyFileSync(source,path.join(target,file));
  }
  fs.writeFileSync(path.join(target,'pairing.local.json'),JSON.stringify(config)+'\n',{mode:0o600});
  return target;
}
async function availablePort(preferred=47631){
  for(let port=preferred;port<Math.min(preferred+100,65536);port++){
    const server=net.createServer();
    const free=await new Promise(resolve=>{server.once('error',()=>resolve(false));server.listen(port,'127.0.0.1',()=>server.close(()=>resolve(true)));});
    if(free)return port;
  }
  throw new Error('No free local bridge port');
}
async function checkIdle(config){
  try{
    const response=await fetch(`http://127.0.0.1:${config.port}/health`,{headers:{Authorization:`Bearer ${config.token}`},signal:AbortSignal.timeout(1500)});
    if(!response.ok)throw new Error('Existing bridge port cannot be authenticated; preserve pairing and resolve port conflict');
    const state=await response.json();
    if(state.name!=='updream-node-helper')throw new Error('Unexpected service on bridge port');
    if(state.pendingRequests||state.activeMutations||state.claimedCanvases)throw new Error('Bridge is busy; finish active workspaces before installing');
  }catch(error){if(error instanceof TypeError&&error.cause?.code==='ECONNREFUSED')return;throw error;}
}
function previousPairing(configFile){
  if(!fs.existsSync(configFile))return null;
  const text=fs.readFileSync(configFile,'utf8');
  const table=require('@iarna/toml').parse(text).mcp_servers?.[NAME];
  if(!table||!text.includes(MARKER)||!Array.isArray(table.args)||typeof table.args[0]!=='string')return null;
  const old=path.join(path.dirname(path.dirname(table.args[0])),'.local','connection.json');
  if(!fs.existsSync(old))return null;
  const config=JSON.parse(fs.readFileSync(old,'utf8'));
  if(config.protocol!==1||!Number.isInteger(config.port)||config.port<1024||config.port>65535||!/^[a-f0-9]{64}$/.test(config.token||''))throw new Error('Existing bridge pairing is invalid; refusing to replace credentials');
  return config;
}
function escapeHtml(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function writeGuide(root,extensionPath){
  const guide=fs.readFileSync(path.join(root,'scripts','install-guide.html'),'utf8').replaceAll('{{EXTENSION_PATH}}',escapeHtml(extensionPath));
  const file=path.join(root,'.local','安装引导.html');fs.writeFileSync(file,guide);return file;
}
function openGuide(file){
  const script='Start-Process -FilePath $env:UPDREAM_GUIDE_PATH';
  const result=spawnSync('powershell.exe',['-NoProfile','-NonInteractive','-Command',script],{env:{...process.env,UPDREAM_GUIDE_PATH:file},windowsHide:true,encoding:'utf8'});
  if(result.status!==0)throw new Error('Could not open install guide. Open it manually: '+file);
}
function createShortcut(root){
  const script="$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Updream MCP Bridge.lnk'));$s.TargetPath=$env:UPDREAM_INSTALL_BAT;$s.WorkingDirectory=$env:UPDREAM_INSTALL_ROOT;$s.Save()";
  const result=spawnSync('powershell.exe',['-NoProfile','-NonInteractive','-Command',script],{env:{...process.env,UPDREAM_INSTALL_BAT:path.join(root,'install.bat'),UPDREAM_INSTALL_ROOT:root},windowsHide:true,encoding:'utf8'});
  return result.status===0;
}
async function install({sourceRoot=path.join(__dirname,'..'),installDir,codexHome,open=true,shortcut=true,port=47631}={}){
  if(process.platform!=='win32')throw new Error('This installer requires Windows x64');
  const manifest=verifyBundle(sourceRoot);
  for(const name of ['install.bat','runtime/node.exe','server/stdio.cjs','server/main.cjs','scripts/setup.cjs','scripts/install-guide.html','extension/manifest.json']){
    if(!Object.hasOwn(manifest.files,name))throw new Error('Required package file missing: '+name);
  }
  const target=path.resolve(installDir||path.join(process.env.LOCALAPPDATA||path.join(os.homedir(),'AppData','Local'),'UpdreamMCPBridge'));
  if(target===path.parse(target).root)throw new Error('Refusing to install into a drive root');
  assertNoSymlinkAncestors(target);
  const configDir=path.resolve(codexHome||process.env.CODEX_HOME||path.join(os.homedir(),'.codex'));
  const configFile=path.join(configDir,'config.toml');
  const launch={command:path.join(target,'runtime','node.exe'),entry:path.join(target,'server','stdio.cjs')};
  configureCodexText(fs.existsSync(configFile)?fs.readFileSync(configFile,'utf8'):'',launch);
  let config;
  const pairingFile=path.join(target,'.local','connection.json');
  if(fs.existsSync(pairingFile))config=loadConfig(target);
  else config=previousPairing(configFile);
  if(config)await checkIdle(config);
  fs.mkdirSync(target,{recursive:true});
  for(const name of Object.keys(manifest.files)){
    const destination=path.join(target,...name.split('/'));assertNoSymlinkAncestors(destination);
    // Windows locks a running node.exe. Reuse identical files on reinstallation.
    if(fs.existsSync(destination)&&fs.statSync(destination).isFile()&&createHash('sha256').update(fs.readFileSync(destination)).digest('hex')===manifest.files[name])continue;
    if(path.resolve(sourceRoot)!==target){fs.mkdirSync(path.dirname(destination),{recursive:true});fs.copyFileSync(path.join(sourceRoot,...name.split('/')),destination);}
  }
  if(path.resolve(sourceRoot)!==target)fs.copyFileSync(path.join(sourceRoot,'bundle-manifest.json'),path.join(target,'bundle-manifest.json'));
  fs.mkdirSync(path.join(target,'.local'),{recursive:true});
  if(config&&!fs.existsSync(pairingFile))fs.writeFileSync(pairingFile,JSON.stringify(config)+'\n',{flag:'wx',mode:0o600});
  if(!config)config=loadConfig(target,{port:await availablePort(port)});
  const extensionPath=provisionExtension(target,config);
  const setup=require(path.join(target,'scripts','setup.cjs'));
  await setup.verifyService({nodePath:launch.command});
  const registered=configureCodex(configFile,launch);
  const guide=writeGuide(target,extensionPath);
  const shortcutCreated=shortcut?createShortcut(target):false;
  const result={version:manifest.version,installDir:target,extensionPath,guide,codexConfig:configFile,configurationChanged:registered.changed,shortcutCreated,browserConnected:false,status:'browser_confirmation_required'};
  fs.writeFileSync(path.join(target,'.local','install-result.json'),JSON.stringify(result,null,2)+'\n');
  if(open)openGuide(guide);
  return result;
}
module.exports={verifyBundle,provisionExtension,install,availablePort,previousPairing,checkIdle};
if(require.main===module){
  const argv=process.argv.slice(2),options={};
  for(let i=0;i<argv.length;i++){
    const key=argv[i];if(key==='--no-open')options.open=false;else if(key==='--no-shortcut')options.shortcut=false;
    else if(['--install-dir','--codex-home','--port'].includes(key)&&argv[i+1])options[{'--install-dir':'installDir','--codex-home':'codexHome','--port':'port'}[key]]=key==='--port'?Number(argv[++i]):argv[++i];
    else{console.error('Unknown installer option: '+key);process.exitCode=1;return;}
  }
  console.log('Installing Updream MCP Bridge. No browser extension is installed silently.');
  install(options).then(result=>{
    console.log('Local service and Codex registration are ready.');
    console.log('Load this folder in Edge/Chrome: '+result.extensionPath);
    console.log('Pairing will be automatic after the extension is loaded.');
    console.log(JSON.stringify(result));
  }).catch(error=>{console.error('Installation failed: '+error.message);process.exitCode=1;});
}
