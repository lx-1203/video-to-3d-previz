'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {createHash}=require('node:crypto');
const {verifyBundle,runCodex,discoverCodex,install}=require('../scripts/install.cjs');
const temp=()=>fs.mkdtempSync(path.join(os.tmpdir(),'updream-codex-test-'));
test('bundle verification detects changed files and refuses traversal',()=>{
  const dir=temp();fs.writeFileSync(path.join(dir,'README.md'),'ready');
  const manifest={version:'0.3.2',files:{'README.md':createHash('sha256').update('ready').digest('hex')}};
  fs.writeFileSync(path.join(dir,'bundle-manifest.json'),JSON.stringify(manifest));
  assert.equal(verifyBundle(dir).version,'0.3.2');fs.writeFileSync(path.join(dir,'README.md'),'changed');
  assert.throws(()=>verifyBundle(dir),/checksum/);
  manifest.files={'../outside':manifest.files['README.md']};fs.writeFileSync(path.join(dir,'bundle-manifest.json'),JSON.stringify(manifest));
  assert.throws(()=>verifyBundle(dir),/Unsafe/);
});
test('Codex failure and malformed JSON are not reported as installation success',()=>{
  assert.throws(()=>runCodex('codex.exe',['plugin','add','example'],{run:()=>({status:1,stderr:'Installation blocked by policy'})}),/blocked by policy/);
  assert.throws(()=>runCodex('codex.exe',['plugin','list','--json'],{run:()=>({status:0,stdout:'not json'})}),/JSON/);
});
test('bundle verification refuses a dangling directory junction',()=>{
  if(process.platform!=='win32')return;
  const dir=temp();fs.symlinkSync(path.join(dir,'missing'),path.join(dir,'link'),'junction');
  fs.writeFileSync(path.join(dir,'bundle-manifest.json'),JSON.stringify({version:'0.3.2',files:{'link/private.txt':'a'.repeat(64)}}));
  assert.throws(()=>verifyBundle(dir),/Unsafe/);
});
test('missing Codex is a clear prerequisite failure',()=>{
  assert.throws(()=>discoverCodex({candidates:[],probe:()=>[]} ),/Codex.*installed|install.*Codex/i);
});
test('native plugin has no external browser-extension or daemon requirements',()=>{
  const root=path.join(__dirname,'..');
  const manifest=JSON.parse(fs.readFileSync(path.join(root,'plugin/.codex-plugin/plugin.json'),'utf8'));
  assert.equal(manifest.name,'updream-codex');assert.equal(manifest.skills,'./skills/');
  assert.ok(!manifest.mcpServers&&!manifest.apps);
  const skill=fs.readFileSync(path.join(root,'plugin/skills/updream/SKILL.md'),'utf8');
  assert.match(skill,/Site tools \/ WebMCP/);assert.match(skill,/not proof of page readability/);
});
test('launcher is ASCII CRLF and invokes the bundled runtime',()=>{
  const bytes=fs.readFileSync(path.join(__dirname,'../install.bat'));
  assert.ok(bytes.every(b=>b<128));assert.ok(!bytes.toString().replaceAll('\r\n','').includes('\n'));
  assert.match(bytes.toString(),/runtime\\node\.exe/);
});
