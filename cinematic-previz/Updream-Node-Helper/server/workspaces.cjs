const {randomUUID}=require('node:crypto');
const failure=(code,message,details)=>Object.assign(new Error(message),{code,...(details?{details}:{})});
const sessionPattern=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
function createWorkspaces({now=Date.now}={}){
  const sessions=new Map(),workspaces=new Map(),claims=new Map();
  const alive=id=>{const session=sessions.get(id);return !!session&&!session.closed&&now()-session.lastSeen<=90000;};
  function touch(sessionId){
    if(!sessionPattern.test(sessionId||''))throw failure('CLIENT_SESSION_REQUIRED','连接缺少独立会话 ID，请更新并重新加载 MCP 入口。');
    if(!sessions.has(sessionId)&&sessions.size>=256){
      for(const [id]of sessions)if(!alive(id)&&![...workspaces.values()].some(w=>w.sessionId===id))sessions.delete(id);
      if(sessions.size>=256)throw failure('CAPACITY','会话数量过多，请关闭不用的工作区。');
    }
    sessions.set(sessionId,{lastSeen:now(),closed:false});return sessionId;
  }
  function owned(workspaceId,sessionId,recover=false){
    touch(sessionId);const w=workspaces.get(workspaceId);
    if(!w)throw failure('WORKSPACE_NOT_FOUND','工作区不存在，请先 open_workspace。');
    if(w.sessionId!==sessionId&&!(recover&&!alive(w.sessionId)))throw failure('WORKSPACE_OWNER_MISMATCH','该工作区属于其他连接，不能修改或释放。');
    return w;
  }
  const publicClaim=c=>({...c});
  function open(sessionId,label){
    touch(sessionId);if(workspaces.size>=256)throw failure('CAPACITY','工作区数量过多，请关闭已完成的工作。');
    const w={workspaceId:randomUUID(),sessionId,label,createdAt:now()};workspaces.set(w.workspaceId,w);return{...w};
  }
  function list(sessionId){
    if(sessionId)touch(sessionId);
    return{clientSessionId:sessionId||null,workspaces:[...workspaces.values()].map(w=>({...w,ownedByThisSession:w.sessionId===sessionId,sessionAlive:alive(w.sessionId),claims:[...claims.values()].filter(c=>c.workspaceId===w.workspaceId).map(publicClaim)}))};
  }
  function claim(workspaceId,sessionId,{tabId,projectId,projectName}){
    const w=owned(workspaceId,sessionId);projectId=String(projectId);
    const previous=claims.get(projectId);
    if(previous&&previous.workspaceId!==workspaceId)throw failure('CANVAS_OWNED','该项目已被其他工作认领；请使用另一画布或等待明确交接。',{owner:{workspaceId:previous.workspaceId,label:workspaces.get(previous.workspaceId)?.label,tabId:previous.tabId},projectId});
    if(previous?.active)throw failure('BUSY','该画布仍在执行操作，不能重新绑定。');
    if(previous?.uncertain)throw failure('UNKNOWN_OUTCOME_PENDING','先核对结果未知的操作，再确认释放原认领。');
    const oldTab=[...claims.values()].find(c=>c.tabId===tabId&&c.projectId!==projectId);
    if(oldTab)throw failure('TAB_ALREADY_CLAIMED','此标签页仍绑定另一项目，请先释放旧认领。',{projectId:oldTab.projectId});
    const c={workspaceId:w.workspaceId,projectId,projectName,tabId,claimedAt:previous?.claimedAt??now(),active:false,uncertain:false};claims.set(projectId,c);return publicClaim(c);
  }
  function releasable(c,acknowledge){
    if(c.active)throw failure('BUSY','操作仍在执行，不能释放或强行接管。');
    if(c.uncertain&&!acknowledge)throw failure('UNKNOWN_OUTCOME_PENDING','已发出操作的结果未知；先核对现场，再传 acknowledgeUnknownOutcome=true。');
  }
  function release(workspaceId,sessionId,projectId,acknowledge=false){
    owned(workspaceId,sessionId);projectId=String(projectId);const c=claims.get(projectId);
    if(!c||c.workspaceId!==workspaceId)throw failure('CANVAS_NOT_CLAIMED','该工作区未认领此项目。');
    releasable(c,acknowledge);claims.delete(projectId);return{released:true,projectId};
  }
  function close(workspaceId,sessionId,{recoverAbandoned=false,acknowledgeUnknownOutcome=false}={}){
    owned(workspaceId,sessionId,recoverAbandoned);
    const all=[...claims.values()].filter(c=>c.workspaceId===workspaceId);
    for(const c of all)releasable(c,acknowledgeUnknownOutcome);
    for(const c of all)claims.delete(c.projectId);
    workspaces.delete(workspaceId);return{closed:true,workspaceId};
  }
  async function run(workspaceId,sessionId,tabId,projectId,work){
    owned(workspaceId,sessionId);projectId=String(projectId);const c=claims.get(projectId);
    if(!c||c.workspaceId!==workspaceId||c.tabId!==tabId)throw failure('CANVAS_NOT_CLAIMED','当前标签页/项目未由此工作区认领；请先 claim_canvas。',{projectId,tabId});
    if(c.active)throw failure('BUSY','该工作区已有写入或批次尚未结束。');
    if(c.uncertain)throw failure('UNKNOWN_OUTCOME_PENDING','先核对先前结果未知的操作，再释放并重新认领。');
    c.active=true;
    try{return await work();}
    catch(error){if(error.code==='UNKNOWN_OUTCOME'||error.details?.writeMayHaveExecuted)c.uncertain=true;throw error;}
    finally{c.active=false;}
  }
  return{touch,open,list,claim,release,close,run,endSession(id){const s=sessions.get(id);if(s)s.closed=true;},get activeWrites(){return[...claims.values()].filter(c=>c.active).length;},get claimCount(){return claims.size;}};
}
module.exports={createWorkspaces,sessionPattern};
