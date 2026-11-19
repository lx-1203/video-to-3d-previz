const fs=require('node:fs');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const base=path.join(__dirname,'../extension');
const manifest=JSON.parse(fs.readFileSync(path.join(base,'manifest.json'),'utf8'));
const assert=require('node:assert/strict');
assert.equal(manifest.manifest_version,3);
assert.deepEqual(manifest.permissions,['storage','alarms']);
assert.equal(manifest.version,require('../package.json').version);
assert.ok(fs.existsSync(path.join(base,manifest.background.service_worker)));
assert.equal(manifest.content_scripts[0].world,'MAIN');
assert.equal(manifest.content_scripts[0].run_at,'document_start');
const allowed=new Set(['https://www.updream.cn/*','https://updream.cn/*','https://updream.bilibili.com/*']);
assert.deepEqual(new Set(manifest.host_permissions),new Set([...allowed,'http://127.0.0.1/*']));
for(const entry of [...manifest.content_scripts,...(manifest.web_accessible_resources??[])]){
  for(const url of entry.matches)assert.ok(allowed.has(url),`Unexpected match ${url}`);
  for(const file of [...(entry.js??[]),...(entry.css??[]),...(entry.resources??[])])assert.ok(fs.existsSync(path.join(base,file)),`Missing ${file}`);
}
assert.ok(fs.existsSync(path.join(base,manifest.action.default_popup)));
let count=0;
for(const file of fs.readdirSync(base)){
  const source=fs.readFileSync(path.join(base,file),'utf8');
  if(file.endsWith('.js')){
    const r=spawnSync(process.execPath,['--check',path.join(base,file)],{encoding:'utf8'});
    assert.equal(r.status,0,r.stderr);count++;
    assert.ok(!/\beval\s*\(|new\s+Function\s*\(|XMLHttpRequest/.test(source),`Unexpected code execution API in ${file}`);
    if(file==='background.js'){
      assert.equal((source.match(/\bfetch\s*\(/g)||[]).length,1);
      assert.ok(source.includes("fetch(chrome.runtime.getURL('pairing.local.json')"),'Pairing fetch must target a private extension resource');
      assert.ok(!(manifest.web_accessible_resources||[]).length,'Private pairing cannot be web accessible');
    }else assert.ok(!/\bfetch\s*\(/.test(source),`Unexpected network API in ${file}`);
  }
  if(file.endsWith('.html')){
    for(const m of source.matchAll(/(?:src|href)="([^"]+)"/g)){
      assert.ok(!/^(https?:)?\/\//.test(m[1]),'Remote resource in popup');
      assert.ok(fs.existsSync(path.join(base,m[1])),`Missing popup resource ${m[1]}`);
    }
    assert.ok(!/<script(?![^>]*\bsrc=)[^>]*>/i.test(source),'Inline popup script');
  }
}
for(const folder of ['server','scripts']){
  for(const file of fs.readdirSync(path.join(__dirname,'..',folder))){
    if(!file.endsWith('.cjs'))continue;
    const r=spawnSync(process.execPath,['--check',path.join(__dirname,'..',folder,file)],{encoding:'utf8'});
    assert.equal(r.status,0,r.stderr);count++;
  }
}
assert.ok(fs.readFileSync(path.join(base,'background.js'),'utf8').includes('ws://127.0.0.1:'));
console.log(`PASS: Manifest scope and references; ${count} production JavaScript syntax checks; only private pairing resource fetch allowed; bridge socket fixed to loopback.`);
