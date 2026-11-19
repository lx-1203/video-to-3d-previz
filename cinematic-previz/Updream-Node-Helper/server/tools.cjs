const {Server}=require('@modelcontextprotocol/sdk/server/index.js');
const {ListToolsRequestSchema,CallToolRequestSchema}=require('@modelcontextprotocol/sdk/types.js');
const {z}=require('zod');
const {createOperations}=require('./operations.cjs');
const nodeId=z.number().int().positive().describe('从当前画布快照取得的真实节点 ID');
const tabId=z.number().int().nonnegative().describe('从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定');
const prompt=z.string().max(20000);
const str=z.string().min(1);
const workspaceId=z.uuid().describe('每个聊天/工作单独 open_workspace 获得，写入前先认领画布');
const definitions=[
  ['get_snapshot','canvas.getSnapshot','读取画布节点、连线与选中项。提示词为摘要；truncated 时用 nextOffset 继续分页。',true,{nodeIds:z.array(nodeId).max(200).optional(),includeEdits:z.boolean().optional(),limit:z.number().int().min(1).max(200).optional(),offset:z.number().int().min(0).optional()}],
  ['get_node','canvas.getNode','读取单节点详情、完整编辑草稿与生成历史。保存提示词前优先读 editDraft.prompt，摘要可能截断。',true,{nodeId}],
  ['create_node','canvas.createNode','新建 image/video/text 节点，可保存提示词草稿，不触发生成。返回 nodeId；核对 promptSaved。',false,{nodeType:z.enum(['image','video','text']),name:str.max(100).optional(),prompt:prompt.optional(),position:z.object({x:z.number(),y:z.number()}).strict().optional()}],
  ['update_node','canvas.updateNode','修改节点名称、提示词、模型、比例或参考图，至少提供一项。不会触发生成；修改后读回核对。',false,{nodeId,name:str.max(100).optional(),prompt:prompt.optional(),model:str.optional(),ratio:str.optional(),referenceImages:z.array(z.url()).max(20).optional()}],
  ['connect_nodes','canvas.connectNodes','从上游连接到下游节点，默认作为参考素材；先读取快照取得真实 ID。',false,{fromNodeId:nodeId,toNodeId:nodeId,isReference:z.boolean().optional()}],
  ['disconnect','canvas.disconnect','删除一条画布连线，可通过 undo 撤销；connectionId 来自快照。',false,{connectionId:nodeId}],
  ['group_nodes','canvas.groupNodes','将至少两个节点分组，最多 50 个；返回组合节点 ID。',false,{nodeIds:z.array(nodeId).min(2).max(50)}],
  ['move_node','canvas.moveNode','把节点移到画布坐标 x/y，非屏幕像素坐标。',false,{nodeId,x:z.number(),y:z.number()}],
  ['select_nodes','canvas.selectNodes','选择指定节点；focus=true 将视口聚焦到第一个节点。只改界面选中态。',false,{nodeIds:z.array(nodeId).max(200),focus:z.boolean().optional()}],
  ['undo','canvas.undo','撤销上一步画布操作；无可撤销操作时网站报错。',false,{}],
  ['redo','canvas.redo','重做上一次撤销的画布操作。',false,{}],
  ['list_models','gen.listModels','查询账号实际可用的图片/视频/文本模型和参数能力，不要猜模型名。',true,{kind:z.enum(['image','video','text'])}],
  ['create_image_task','gen.createImageTask','向图片节点提交生成，可能消耗积分，保留网站确认。返回任务号不是生成完成；禁止失败或超时后盲目重试。',false,{nodeId,prompt:prompt.min(1).optional(),model:str.optional(),ratio:str.optional(),referenceImages:z.array(z.url()).max(20).optional(),count:z.number().int().min(1).max(4).optional()}],
  ['create_video_task','gen.createVideoTask','向视频节点提交生成，可能消耗积分，保留网站确认。先查询模型能力，提交后用 get_task_status 查询。',false,{nodeId,prompt:prompt.min(1).optional(),model:str.optional(),duration:z.number().positive().optional(),resolution:str.optional(),aspectRatio:str.optional(),image:z.url().optional()}],
  ['create_text_task','gen.createTextTask','向文本节点提交生成，可能消耗积分，保留网站确认。完成后 get_node 读取 textContent。',false,{nodeId,prompt:prompt.min(1),model:str.optional()}],
  ['get_task_status','gen.getTaskStatus','读取画布生成任务状态、进度与结果。nodeId/taskId 至少一项；建议查询间隔不少于 5 秒，不重复创建任务。',true,{nodeId:nodeId.optional(),taskId:str.optional()}],
  ['cancel_task','gen.cancelTask','取消指定节点正在进行的生成任务；不自动重新生成。',false,{nodeId}],
];
function buildTools(hub,{sessionId}={}){
  const ops=createOperations(hub);
  const tools=definitions.map(([name,native,description,readOnly,shape])=>{
    let schema=z.object({tabId,...(!readOnly?{workspaceId}:{}),...shape}).strict();
    if(name==='update_node')schema=schema.refine(v=>['name','prompt','model','ratio','referenceImages'].some(k=>v[k]!==undefined),'至少修改一个字段');
    if(name==='get_task_status')schema=schema.refine(v=>v.nodeId!==undefined||v.taskId!==undefined,'nodeId/taskId 至少一项');
    return{name:`updream_${name}`,description,schema,readOnly,call:async(input,signal)=>{
      const {tabId,workspaceId,...args}=input;
      const call=()=>ops.native(tabId,native,args,signal);
      return readOnly?call():ops.mutate(tabId,call,{sessionId,workspaceId},signal);
    }};
  });
  tools.unshift(
    {name:'updream_open_workspace',description:'为当前聊天或独立工作新建工作区。即使共享 MCP 进程，每项工作也应各自创建 workspaceId，随后认领画布。',schema:z.object({label:str.max(120)}).strict(),readOnly:false,call:({label})=>ops.workspaces.open(sessionId,label)},
    {name:'updream_list_workspaces',description:'查看各工作区、任务名称、会话在线状态和画布归属；同项目跨标签页共享认领。用于分工与交接。',schema:z.object({}).strict(),readOnly:true,call:()=>ops.workspaces.list(sessionId)},
    {name:'updream_claim_canvas',description:'把实际项目画布认领给本工作区，跨多次调用保留。其他工作区不能同时写同项目，即使使用不同标签页。不同项目可并行。',schema:z.object({workspaceId,tabId}).strict(),readOnly:false,call:(input,signal)=>ops.claimCanvas(input,sessionId,signal)},
    {name:'updream_release_canvas',description:'结束本项目操作后释放认领交接。进行中的写入不能释放；结果未知时先读回核对，再显式 acknowledgeUnknownOutcome。',schema:z.object({workspaceId,projectId:str,acknowledgeUnknownOutcome:z.boolean().default(false)}).strict(),readOnly:false,call:({workspaceId,projectId,acknowledgeUnknownOutcome})=>ops.workspaces.release(workspaceId,sessionId,projectId,acknowledgeUnknownOutcome)},
    {name:'updream_close_workspace',description:'工作结束后关闭并释放所有认领。recoverAbandoned 仅能清理已断开或 90 秒无心跳的其他会话；仍在写入时拒绝清理。结果未知需先核对再确认。',schema:z.object({workspaceId,recoverAbandoned:z.boolean().default(false),acknowledgeUnknownOutcome:z.boolean().default(false)}).strict(),readOnly:false,call:({workspaceId,...options})=>ops.workspaces.close(workspaceId,sessionId,options)},
    {name:'updream_connection_status',description:'仅检查独立 Bridge MCP 与 Edge/Chrome 扩展的连接；不能检测 Codex 内置浏览器的原生连接，不返回配对凭证。',schema:z.object({}).strict(),readOnly:true,call:async()=>({...hub.status(),activeMutations:ops.activeWrites})},
    {name:'updream_list_tabs',description:'列出已打开的 updream 标签页及其接口状态。先取真实 tabId，再读取快照和操作节点。',schema:z.object({}).strict(),readOnly:true,call:async()=>hub.request({method:'list_tabs'})},
    {name:'updream_list_tools',description:'列出指定 updream 标签页当前实际注册的原生工具及完整参数格式。',schema:z.object({tabId}).strict(),readOnly:true,call:async({tabId})=>hub.request({method:'page',tabId,action:'list'})},
  );
  const nativeName=z.string().regex(/^(canvas|gen)[._][A-Za-z][A-Za-z0-9]*$/).describe('来自实时工具目录的 canvas/gen 名称');
  const nativeArgs=z.record(z.string(),z.json());
  const edits=z.object({name:str.max(100).optional(),prompt:prompt.optional()}).strict();
  tools.push(
    {name:'updream_find_nodes',description:'跨页搜索节点名称、ID、提示词摘要，支持类型和状态筛选。返回 nextOffset 继续扫描；达到 maxScan 时明确标记。不是全文搜索或原子快照。',schema:z.object({tabId,query:z.string().max(500).default(''),type:str.optional(),status:str.optional(),offset:z.number().int().min(0).default(0),limit:z.number().int().min(1).max(200).default(50),maxScan:z.number().int().min(1).max(10000).default(2000)}).strict(),readOnly:true,call:(input,signal)=>ops.findNodes(input,signal)},
    {name:'updream_call_native_tool',description:'按实时目录调用已注册的 canvas/gen 工具，先验证网页 JSON Schema，再保留网站业务校验与确认。写入仍要求工作区认领。',schema:z.object({workspaceId,tabId,tool:nativeName,arguments:nativeArgs.default({})}).strict(),readOnly:false,destructive:true,call:({workspaceId,tabId,tool,arguments:args},signal)=>ops.callNativeChecked(tabId,tool,args,signal,{workspaceId,sessionId})},
    {name:'updream_batch',description:'最多 20 步本工作区画布顺序调用。先验证全部实时 JSON Schema，业务校验仍在执行时进行。遇错、拒绝或取消停止并返回 completed/stoppedAt。不是事务，不回滚或自动重试。',schema:z.object({workspaceId,tabId,steps:z.array(z.object({tool:nativeName,arguments:nativeArgs.default({})}).strict()).min(1).max(20)}).strict(),readOnly:false,destructive:true,call:(input,signal)=>ops.batch(input,signal,{workspaceId:input.workspaceId,sessionId})},
    {name:'updream_update_node_checked',description:'在已认领画布修改名称/完整提示词前比较 expected，写后读回核对。每个 changes 字段都需对应 expected；提示词必须来自完整 editDraft.prompt。仍非网站原子条件更新。',schema:z.object({workspaceId,tabId,nodeId,expected:edits,changes:edits}).strict().refine(v=>Object.keys(v.changes).length>0&&Object.keys(v.changes).every(k=>v.expected[k]!==undefined),'每个修改字段必须提供对应 expected，且至少修改一项'),readOnly:false,call:(input,signal)=>ops.updateChecked(input,signal,{workspaceId:input.workspaceId,sessionId})},
    {name:'updream_diagnose',description:'仅诊断独立 Bridge 服务、Edge/Chrome 扩展和可选标签页；不能检测 Codex 内置浏览器的原生连接，不返回令牌。',schema:z.object({tabId:tabId.optional()}).strict(),readOnly:true,call:({tabId},signal)=>ops.diagnose(tabId,signal)},
  );
  return tools;
}
function createMcpServer(hub,context={}){
  const server=new Server({name:'updream-node-helper',version:require('../package.json').version},{capabilities:{tools:{}}});
  const tools=buildTools(hub,context);
  server.setRequestHandler(ListToolsRequestSchema,async()=>({tools:tools.map(t=>{
    const {$schema,...inputSchema}=z.toJSONSchema(t.schema);
    return{name:t.name,description:t.description,inputSchema,annotations:{readOnlyHint:t.readOnly,destructiveHint:!!t.destructive,idempotentHint:t.readOnly,openWorldHint:false}};
  })}));
  server.setRequestHandler(CallToolRequestSchema,async(request,extra)=>{
    try{
      const tool=tools.find(t=>t.name===request.params.name);
      if(!tool)throw Object.assign(new Error('未知工具，请先读取 MCP 工具列表。'),{code:'UNKNOWN_TOOL'});
      const parsed=tool.schema.safeParse(request.params.arguments??{});
      if(!parsed.success)throw Object.assign(new Error(parsed.error.issues.map(i=>`${i.path.join('.')}: ${i.message}`).join('; ')),{code:'INVALID_ARGS'});
      const signal=context.signal?AbortSignal.any([context.signal,extra.signal]):extra.signal;
      if(signal.aborted)throw Object.assign(new Error('请求已取消。'),{code:'ABORTED'});
      const data=await tool.call(parsed.data,signal);
      const value={ok:true,data};
      return{content:[{type:'text',text:JSON.stringify(value)}],structuredContent:value,isError:false};
    }catch(error){
      const value={ok:false,error:{code:error.code||'TOOL_ERROR',message:String(error.message||error),...(error.details?{details:error.details}:{})}};
      return{content:[{type:'text',text:JSON.stringify(value)}],structuredContent:value,isError:true};
    }
  });
  return server;
}
module.exports={createMcpServer,buildTools};
