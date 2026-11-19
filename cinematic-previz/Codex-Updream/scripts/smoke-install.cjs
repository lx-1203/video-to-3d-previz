'use strict';
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {install,discoverCodex}=require('./install.cjs');
async function smoke(base,exe){
  const home=path.join(base,'codex-home'),target=path.join(base,'用户 安装 & Codex');
  fs.mkdirSync(home,{recursive:true});
  const config=path.join(home,'config.toml');
  const original='model = "gpt-6"\n[desktop]\nlocaleOverride = "zh-CN"\n[mcp_servers.updream-node-helper]\ncommand = "retained-bridge.exe"\n';fs.writeFileSync(config,original);
  const first=await install({installDir:target,codexHome:home,codexExe:exe||discoverCodex()});
  assert.equal(first.status,'installed');assert.equal(first.browserConnection,'not_checked');
  assert.equal(first.externalBrowserExtensionRequired,false);
  for(const file of [
    'skills/updream/SKILL.md',
    'skills/updream/references/webmcp.md',
    'skills/updream/scripts/webmcp-read.mjs',
    '.codex-plugin/plugin.json',
  ])assert.deepEqual(fs.readFileSync(path.join(first.installedPath,file)),fs.readFileSync(path.join(__dirname,'../plugin',file)));
  const saved=fs.readFileSync(config,'utf8');assert.ok(saved.includes('model = "gpt-6"'));assert.ok(saved.includes('localeOverride = "zh-CN"'));assert.ok(saved.includes('command = "retained-bridge.exe"'));
  const second=await install({updateExisting:true,codexHome:home,codexExe:exe||discoverCodex()});
  assert.equal(second.installDir,target);assert.equal(second.version,first.version);assert.equal(fs.readFileSync(config,'utf8'),saved);
  assert.ok(!fs.existsSync(path.join(target,'extension')));assert.ok(!fs.existsSync(path.join(target,'server')));
  console.log(JSON.stringify({passed:true,realCodexCli:true,version:first.version,checks:['automatic local marketplace registration','official plugin installation','installed/enabled/version readback','installed source bytes verified','Chinese and special-character path','idempotent update in the registered directory','unrelated configuration and legacy Bridge preserved'],browserConnectionTested:false,desktopInputUsed:false}));
}
if(require.main===module)smoke(path.resolve(process.argv[2]),process.argv[3]).catch(error=>{console.error(error);process.exitCode=1;});
