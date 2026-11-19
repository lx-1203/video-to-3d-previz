'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {isDeepStrictEqual}=require('node:util');
const toml=require('@iarna/toml');
const NAME='updream-node-helper';
const MARKER='# Managed by Updream MCP Bridge';
function configureCodexText(text,{command,entry}){
  if(!command||!entry)throw new Error('Invalid local launch configuration');
  const before=toml.parse(text),newline=text.includes('\r\n')?'\r\n':'\n';
  const block=[`[mcp_servers.${NAME}]`,MARKER,`command = ${JSON.stringify(command)}`,`args = [${JSON.stringify(entry)}]`,'startup_timeout_sec = 40','tool_timeout_sec = 240',''].join(newline);
  let updated;
  if(before.mcp_servers?.[NAME]){
    const match=/^\[mcp_servers\.updream-node-helper\][ \t]*\r?\n/m.exec(text);
    if(!match)throw new Error('Existing bridge table is not managed by this installer');
    const next=/^\[/m.exec(text.slice(match.index+match[0].length));
    const end=next?match.index+match[0].length+next.index:text.length;
    if(!text.slice(match.index,end).includes(MARKER))throw new Error('Existing bridge table is not managed by this installer');
    updated=text.slice(0,match.index)+block+(end<text.length?newline:'')+text.slice(end);
  }else updated=text+(text&&!text.endsWith('\n')?newline:'')+newline+block;
  const after=toml.parse(updated);
  delete before.mcp_servers?.[NAME];delete after.mcp_servers?.[NAME];
  for(const value of [before,after])if(value.mcp_servers&&Object.keys(value.mcp_servers).length===0)delete value.mcp_servers;
  if(!isDeepStrictEqual(before,after))throw new Error('Refusing to change unrelated Codex settings');
  return updated;
}
function configureCodex(file,launch){
  const existed=fs.existsSync(file),original=existed?fs.readFileSync(file,'utf8'):'';
  const updated=configureCodexText(original,launch);
  if(original===updated)return{changed:false,backup:null};
  fs.mkdirSync(path.dirname(file),{recursive:true});
  const suffix=`${Date.now()}-${process.pid}`,temporary=file+'.updream-tmp-'+suffix;
  const backup=existed?file+'.updream-backup-'+suffix:null;
  if(backup)fs.copyFileSync(file,backup,fs.constants.COPYFILE_EXCL);
  fs.writeFileSync(temporary,updated,{flag:'wx',mode:0o600});
  if(fs.existsSync(file)!==existed||(existed&&fs.readFileSync(file,'utf8')!==original)){
    fs.unlinkSync(temporary);throw new Error('Codex configuration changed during installation; retry after other updates finish');
  }
  fs.renameSync(temporary,file);
  return{changed:true,backup};
}
module.exports={configureCodexText,configureCodex,NAME,MARKER};
