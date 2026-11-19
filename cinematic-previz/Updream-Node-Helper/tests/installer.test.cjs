'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {createHash}=require('node:crypto');
const {configureCodexText,configureCodex}=require('../scripts/configure-codex.cjs');
const {verifyBundle,provisionExtension}=require('../scripts/install.cjs');
const temp=()=>fs.mkdtempSync(path.join(os.tmpdir(),'updream-test-'));
test('double-click entry is ASCII CRLF and references the bundled executable literally',()=>{
  const bytes=fs.readFileSync(path.join(__dirname,'../install.bat'));
  assert.ok(bytes.every(value=>value<128));
  assert.ok(!bytes.toString().replaceAll('\r\n','').includes('\n'));
  assert.ok(bytes.toString().includes('"%~dp0runtime\\node.exe" "%~dp0scripts\\install.cjs"'));
  assert.ok(bytes.toString().includes('\r\npause\r\n'));
});
test('MCP registration preserves unrelated settings and quoted Windows paths',()=>{
  const source='model = "gpt-6"\r\n[desktop]\r\nlocaleOverride = "zh-CN"\r\n[mcp_servers.other]\r\ncommand = "other"\r\n';
  const launch={command:'C:\\Program Files\\Node\\node.exe',entry:'D:\\用户 资料\\server\\stdio.cjs'};
  const updated=configureCodexText(source,launch);
  assert.ok(updated.startsWith(source));
  const toml=require('@iarna/toml').parse(updated);
  assert.deepEqual(toml.desktop,{localeOverride:'zh-CN'});
  assert.equal(toml.mcp_servers['updream-node-helper'].args[0],launch.entry);
  assert.equal(configureCodexText(updated,launch),updated);
});
test('registration refuses unmanaged tables and malformed TOML',()=>{
  const launch={command:'node.exe',entry:'stdio.cjs'};
  assert.throws(()=>configureCodexText('[mcp_servers.updream-node-helper]\ncommand="custom"\n',launch),/not managed/);
  assert.throws(()=>configureCodexText('[invalid',launch),/TOML|parse|Expected/i);
});
test('managed replacement preserves following tables and creates exact backup',()=>{
  const dir=temp();const file=path.join(dir,'config.toml');
  const old='[mcp_servers.updream-node-helper]\n# Managed by Updream MCP Bridge\ncommand="old"\nargs=["old.cjs"]\n\n[desktop]\nlocaleOverride="zh-CN"\n';
  fs.writeFileSync(file,old);
  const launch={command:process.execPath,entry:__filename};
  const result=configureCodex(file,launch);
  assert.equal(fs.readFileSync(result.backup,'utf8'),old);
  assert.deepEqual(require('@iarna/toml').parse(fs.readFileSync(file,'utf8')).desktop,{localeOverride:'zh-CN'});
  assert.equal(configureCodex(file,launch).changed,false);
});
test('bundle verification rejects tampering, missing files and parent paths',()=>{
  const root=temp();fs.writeFileSync(path.join(root,'install.bat'),'safe');
  const files={'install.bat':createHash('sha256').update('safe').digest('hex')};
  const manifest={version:'0.3.1',files};
  fs.writeFileSync(path.join(root,'bundle-manifest.json'),JSON.stringify(manifest));
  assert.equal(verifyBundle(root).version,'0.3.1');
  fs.writeFileSync(path.join(root,'install.bat'),'modified');
  assert.throws(()=>verifyBundle(root),/mismatch/);
  fs.unlinkSync(path.join(root,'install.bat'));
  assert.throws(()=>verifyBundle(root),/missing|ENOENT/);
  manifest.files={'../secret.txt':files['install.bat']};fs.writeFileSync(path.join(root,'bundle-manifest.json'),JSON.stringify(manifest));
  assert.throws(()=>verifyBundle(root),/Unsafe/);
});
test('local extension provisioning keeps credentials out of distributed source',()=>{
  const root=temp();fs.mkdirSync(path.join(root,'extension'));
  fs.writeFileSync(path.join(root,'extension','manifest.json'),'{}');
  const config={protocol:1,port:49218,token:'b'.repeat(64)};
  const result=provisionExtension(root,config);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(result,'pairing.local.json'),'utf8')),config);
  assert.ok(!fs.existsSync(path.join(root,'extension','pairing.local.json')));
});
