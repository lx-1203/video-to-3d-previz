'use strict';
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {createHash}=require('node:crypto');
const {spawnSync}=require('node:child_process');
const PLUGIN='updream-codex',MARKETPLACE='xingji-updream';
function safePath(root,name){
  if(typeof name!=='string'||!name||name.includes('\\')||name.includes(':')||path.posix.isAbsolute(name)||name.split('/').some(part=>!part||['.','..','.local','.git','.env','auth.json','connection.json','pairing.local.json'].includes(part)))throw new Error('Unsafe bundle path: '+name);
  let current=path.resolve(root);
  for(const part of name.split('/')){current=path.join(current,part);if(fs.lstatSync(current,{throwIfNoEntry:false})?.isSymbolicLink())throw new Error('Unsafe symbolic link: '+name);}
  return current;
}
function hash(file){return createHash('sha256').update(fs.readFileSync(file)).digest('hex');}
function verifyBundle(root){
  const manifest=JSON.parse(fs.readFileSync(path.join(root,'bundle-manifest.json'),'utf8'));
  if(!manifest.version||!manifest.files||Array.isArray(manifest.files)||typeof manifest.files!=='object')throw new Error('Invalid bundle manifest');
  for(const [name,expected]of Object.entries(manifest.files))if(!/^[a-f0-9]{64}$/.test(expected)||hash(safePath(root,name))!==expected)throw new Error('Bundle checksum mismatch: '+name);
  return manifest;
}
function runCodex(exe,args,{codexHome,run=spawnSync}={}){
  const result=run(exe,args,{env:{...process.env,...(codexHome?{CODEX_HOME:codexHome}:{})},windowsHide:true,encoding:'utf8',timeout:60000,maxBuffer:8*1024*1024});
  if(result.error||result.status!==0)throw new Error('Codex command failed: '+String(result.stderr||result.error?.message||result.stdout||'unknown error').trim().slice(0,2000));
  try{return JSON.parse(result.stdout);}catch{throw new Error('Codex did not return valid JSON; update Codex and retry');}
}
function desktopCandidates(){
  const script="@(Get-AppxPackage -Name OpenAI.Codex | ForEach-Object { Join-Path $_.InstallLocation 'app/resources/codex.exe' }) | ConvertTo-Json -Compress";
  const result=spawnSync('powershell.exe',['-NoProfile','-NonInteractive','-Command',script],{windowsHide:true,encoding:'utf8',timeout:15000});
  try{const values=JSON.parse(result.stdout);return Array.isArray(values)?values:typeof values==='string'?[values]:[];}catch{return[];}
}
function discoverCodex({candidates,probe=desktopCandidates}={}){
  const options=candidates||[...probe(),...(process.env.CODEX_CLI_PATH?[process.env.CODEX_CLI_PATH]:[])];
  for(const exe of options)if(typeof exe==='string'&&exe.toLowerCase().endsWith('.exe')&&fs.existsSync(exe)&&fs.statSync(exe).isFile())return exe;
  throw new Error('Codex desktop app is not installed or its CLI is unavailable. Install/update Codex and double-click again.');
}
function assertTarget(target){
  if(target===path.parse(target).root)throw new Error('Refusing to install into a drive root');
  for(let p=target;;p=path.dirname(p)){
    if(fs.lstatSync(p,{throwIfNoEntry:false})?.isSymbolicLink())throw new Error('Installation directory contains a junction or symbolic link');
    if(path.dirname(p)===p)break;
  }
}
async function install({sourceRoot=path.join(__dirname,'..'),installDir,codexHome,codexExe,progress=console.log}={}){
  progress('[1/5] Checking the offline package...');
  const manifest=verifyBundle(sourceRoot);
  for(const name of ['install.bat','runtime/node.exe','.agents/plugins/marketplace.json','plugin/.codex-plugin/plugin.json','plugin/skills/updream/SKILL.md'])if(!Object.hasOwn(manifest.files,name))throw new Error('Required bundle file missing: '+name);
  const plugin=JSON.parse(fs.readFileSync(path.join(sourceRoot,'plugin/.codex-plugin/plugin.json'),'utf8'));
  if(plugin.name!==PLUGIN||plugin.version!==manifest.version)throw new Error('Plugin and package versions do not match');
  const exe=codexExe||discoverCodex();
  const target=path.resolve(installDir||path.join(process.env.LOCALAPPDATA||path.join(os.homedir(),'AppData','Local'),'UpdreamCodex'));
  const home=path.resolve(codexHome||process.env.CODEX_HOME||path.join(os.homedir(),'.codex'));
  assertTarget(target);fs.mkdirSync(target,{recursive:true});
  progress('[2/5] Copying plugin files to a stable local directory...');
  for(const [name,expected]of Object.entries(manifest.files)){
    const destination=safePath(target,name);fs.mkdirSync(path.dirname(destination),{recursive:true});
    if(!fs.existsSync(destination)||hash(destination)!==expected)fs.copyFileSync(path.join(sourceRoot,...name.split('/')),destination);
  }
  if(path.resolve(sourceRoot)!==target)fs.copyFileSync(path.join(sourceRoot,'bundle-manifest.json'),path.join(target,'bundle-manifest.json'));
  fs.mkdirSync(path.join(target,'.local'),{recursive:true});
  const config=path.join(home,'config.toml');
  if(fs.existsSync(config))fs.copyFileSync(config,path.join(target,'.local',`codex-config-backup-${Date.now()}-${process.pid}.toml`),fs.constants.COPYFILE_EXCL);
  progress('[3/5] Registering the local marketplace with the official Codex CLI...');
  const marketplace=runCodex(exe,['plugin','marketplace','add',target,'--json'],{codexHome:home});
  if(marketplace.marketplaceName!==MARKETPLACE)throw new Error('Codex registered an unexpected marketplace');
  progress('[4/5] Installing Updream for Codex automatically...');
  const added=runCodex(exe,['plugin','add',`${PLUGIN}@${MARKETPLACE}`,'--json'],{codexHome:home});
  if(added.name!==PLUGIN||added.version!==manifest.version)throw new Error('Codex installed an unexpected plugin or version');
  progress('[5/5] Reading back the installed and enabled plugin...');
  const listed=runCodex(exe,['plugin','list','--marketplace',MARKETPLACE,'--json'],{codexHome:home});
  const entry=listed.installed?.find(value=>value.name===PLUGIN&&value.marketplaceName===MARKETPLACE);
  if(!entry||entry.installed!==true||entry.enabled!==true||entry.version!==manifest.version)throw new Error('Plugin registration could not be verified as installed and enabled');
  const result={status:'installed',plugin:PLUGIN,marketplace:MARKETPLACE,version:manifest.version,installedPath:added.installedPath,installDir:target,codexHome:home,browserConnection:'not_checked',externalBrowserExtensionRequired:false,desktopInputUsed:false};
  fs.writeFileSync(path.join(target,'.local','install-result.json'),JSON.stringify(result,null,2)+'\n');
  progress('SUCCESS: Updream for Codex is installed and enabled.');
  progress('Open a new Codex chat (restart Codex if it still shows the old plugin list).');
  progress('Ask Codex to use the Updream plugin. Website login and browser permissions still apply.');
  return result;
}
module.exports={verifyBundle,runCodex,discoverCodex,install};
if(require.main===module){
  const options={},args=process.argv.slice(2);
  for(let i=0;i<args.length;i++){
    const key={'--install-dir':'installDir','--codex-home':'codexHome','--codex-exe':'codexExe'}[args[i]];
    if(!key||!args[i+1]){console.error('Unknown installer option: '+args[i]);process.exitCode=1;return;}
    options[key]=args[++i];
  }
  install(options).then(result=>console.log(JSON.stringify(result))).catch(error=>{console.error('INSTALLATION FAILED: '+error.message);process.exitCode=1;});
}
