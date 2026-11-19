const fs=require('node:fs');const path=require('node:path');const {z}=require('zod');
const {buildTools}=require('../server/tools.cjs');
const {AjvJsonSchemaValidator}=require('@modelcontextprotocol/sdk/validation/ajv-provider.js');
const tools=buildTools({status:()=>({}),request:async()=>null});
const skill=path.join(__dirname,'../references');
const examples=JSON.parse(fs.readFileSync(path.join(skill,'updream-examples.json'),'utf8'));
if(examples.length!==tools.length||new Set(examples.map(e=>e.name)).size!==tools.length)throw new Error('Every MCP tool needs one unique skill example.');
for(const tool of tools){
  const example=examples.find(e=>e.name===tool.name);
  if(!example||!tool.schema.safeParse(example.arguments).success)throw new Error(`Invalid skill example: ${tool.name}`);
  const validation=new AjvJsonSchemaValidator().getValidator(z.toJSONSchema(tool.schema))(example.arguments);
  if(!validation.valid)throw new Error(`Example contradicts advertised JSON Schema: ${tool.name}: ${validation.errorMessage}`);
}
let text=`# Updream MCP 接口列表 · ${require('../package.json').version}\n\n由 server/tools.cjs 自动生成。Codex 通过 MCP tools/list 获得同一套接口。\n\n`;
text+=`共 ${tools.length} 个接口：20 个直接接口、5 个效率接口和 5 个多工作区协作接口。需写入的工作先 open_workspace → claim_canvas；所有写入携带 workspaceId。不同项目并行，同项目跨标签互斥；结束后 release_canvas 或 close_workspace。只读检查无需新建工作区。所有节点接口必须指定真实 tabId。\n\n`;
text+='以下 name/arguments 是 MCP 调用示例，不是可直接执行的生产计划。示例 workspaceId、tabId、projectId、nodeId、connectionId 和提示词均为虚构，实际操作必须替换为当前接口返回的真实值及用户授权内容；生成示例不会被验证器执行。原生名称与能力查当前 list_tools，业务约束由网站继续校验。\n\n';
text+='| 接口 | 只读 | 用途 |\n| --- | --- | --- |\n';
for(const tool of tools)text+=`| ${tool.name} | ${tool.readOnly?'是':'否'} | ${tool.description} |\n`;
for(const tool of tools){
  const {$schema,...schema}=z.toJSONSchema(tool.schema);
  text+=`\n## ${tool.name}\n\n${tool.description}\n\n\`\`\`json\n${JSON.stringify(schema,null,2)}\n\`\`\`\n`;
  text+=`\n调用示例：\n\n\`\`\`json\n${JSON.stringify(examples.find(e=>e.name===tool.name),null,2)}\n\`\`\`\n`;
}
fs.writeFileSync(path.join(__dirname,'../TOOLS.md'),text);
console.log(`Generated ${tools.length} tool definitions in TOOLS.md`);
console.log('Standalone bridge examples validated without executing tools.');
