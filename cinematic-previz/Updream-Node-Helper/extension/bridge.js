/* Runs in the website's MAIN world. No browser privileges or network requests. */
(() => {
  'use strict';
  if (window.__updreamNodeHelperBridgeV1) return;
  window.__updreamNodeHelperBridgeV1 = true;
  const MARKER = '__updream_node_helper__';
  const SURFACE = '__agent_surface__';
  const IN = 'updream-helper/content/v1';
  const OUT = 'updream-helper/page/v1';
  const tools = new Map();
  let enabled = false;
  let busy = null;
  let setupError = '';

  const fail = (code, message) => Object.assign(new Error(message), { code });
  const allowed = name => /^(canvas|gen)_[A-Za-z][A-Za-z0-9]*$/.test(name);
  const normalize = name => typeof name === 'string' ? name.replace(/\./g, '_') : '';
  const publicTool = t => ({ name:t.name, description:t.description || '', inputSchema:t.inputSchema || {}, annotations:t.annotations || {} });

  function remember(definition, options) {
    if (!definition || typeof definition.name !== 'string' || typeof definition.execute !== 'function') {
      throw fail('INVALID_TOOL', '网站注册了无效工具。');
    }
    const name = definition.name;
    if (options?.signal?.aborted) return;
    const entry = { ...publicTool(definition), execute: definition.execute };
    tools.set(name, entry);
    options?.signal?.addEventListener('abort', () => {
      if (tools.get(name) === entry) tools.delete(name);
    }, { once:true });
    return entry;
  }

  function observeNative(api) {
    const originalRegister = api.registerTool;
    const register = function (definition, options) {
      // Preserve native errors and return type. The site owns validation/confirmation.
      const result = originalRegister.call(this, definition, options);
      const entry = remember(definition, options);
      if (result?.then) Promise.resolve(result).catch(() => {
        if (tools.get(definition.name) === entry) tools.delete(definition.name);
      });
      return result;
    };
    api.registerTool = register;
    if (api.registerTool !== register) throw fail('NATIVE_API_LOCKED', '当前浏览器不允许旁观原生接口注册。');
    if (typeof api.unregisterTool === 'function') {
      const original = api.unregisterTool;
      api.unregisterTool = function (name) {
        const result = original.call(this, name);
        const entry = tools.get(name);
        const remove = () => { if (tools.get(name) === entry) tools.delete(name); };
        if (result?.then) Promise.resolve(result).then(remove, () => {});
        else remove();
        return result;
      };
    }
    if (typeof api.clearContext === 'function') {
      const original = api.clearContext;
      api.clearContext = function (...args) {
        const result = original.apply(this, args);
        if (result?.then) Promise.resolve(result).then(() => tools.clear(), () => {});
        else tools.clear();
        return result;
      };
    }
  }

  function setup() {
    const api = [document.modelContext, navigator.modelContext].find(x => typeof x?.registerTool === 'function');
    if (api) observeNative(api);
    else {
      // This is only a registration host for the site's own adapter, not an MCP server.
      const shim = {
        registerTool(definition, options) { remember(definition, options); },
        unregisterTool(name) { tools.delete(name); },
        clearContext() { tools.clear(); },
      };
      Object.defineProperty(document, 'modelContext', { value:shim, configurable:true });
    }
  }

  try {
    const marker = JSON.parse(localStorage.getItem(MARKER) || 'null');
    enabled = marker?.version === 1;
    if (enabled) {
      setup();
      localStorage.setItem(SURFACE, '1');
    }
  } catch (error) { setupError = String(error.message || error); }

  async function handle(request) {
    if (request.action === 'status') {
      let projectId=null;
      try { const url=new URL(window.location.href);projectId=url.searchParams.get('project')||url.searchParams.get('projectId')||url.searchParams.get('project_id'); } catch {}
      const buildHash=document.querySelector?.('meta[name="build-hash"]')?.content||null;
      return { enabled, tools:tools.size, busy, error:setupError, bridgeVersion:'0.3.0', protocol:1, readyState:document.readyState||null, buildHash, projectId };
    }
    if (request.action === 'enable') {
      if (!enabled) {
        localStorage.setItem(MARKER, JSON.stringify({version:1, originalSurface:localStorage.getItem(SURFACE)}));
      }
      localStorage.setItem(SURFACE, '1');
      enabled = true;
      window.location.reload();
      return { reloading:true };
    }
    if (request.action === 'disable') {
      if (busy) throw fail('BUSY', '操作尚未结束，请先处理网站确认框或等待结果。');
      const marker = JSON.parse(localStorage.getItem(MARKER) || 'null');
      if (marker?.version === 1 && localStorage.getItem(SURFACE) === '1') {
        if (marker.originalSurface === null) localStorage.removeItem(SURFACE);
        else localStorage.setItem(SURFACE, marker.originalSurface);
      }
      localStorage.removeItem(MARKER);
      window.location.reload();
      return { reloading:true };
    }
    if (request.action === 'list') return [...tools.values()].filter(t => allowed(t.name)).map(publicTool);
    if (request.action !== 'execute') throw fail('INVALID_ACTION', '未知操作。');
    const name = normalize(request.tool);
    if (!allowed(name)) throw fail('TOOL_NOT_ALLOWED', '仅允许当前网站注册的 canvas / gen 工具。');
    if (!enabled || setupError) throw fail('NOT_CONNECTED', setupError || '请先连接并刷新页面。');
    const tool = tools.get(name);
    if (!tool) throw fail('TOOL_UNAVAILABLE', '该工具尚未注册；请进入具体项目画布后刷新工具列表。');
    const args = request.arguments ?? {};
    if (typeof args !== 'object' || args === null || Array.isArray(args) || JSON.stringify(args).length > 1000000) {
      throw fail('INVALID_ARGS', '参数必须是 JSON 对象，且不能超过 1 MB。');
    }
    const readOnly = tool.annotations.readOnlyHint === true;
    if(!readOnly&&request.expectedProjectId===undefined)throw fail('WORKSPACE_REQUIRED','写入需要已认领的项目绑定，请更新 MCP 服务并先创建工作区。');
    if (busy && !readOnly) throw fail('BUSY', `仍在处理 ${busy}；请等待或先处理网站确认框，勿重复提交。`);
    if (!readOnly) busy = name;
    try {
      if(request.expectedProjectId!==undefined){
        const snapshot=tools.get('canvas_getSnapshot');
        if(!snapshot||snapshot.annotations.readOnlyHint!==true)throw fail('PROJECT_UNVERIFIED','不能核对当前项目，本次未调用目标接口。');
        const result=await snapshot.execute({limit:1},{});
        let value=result?.structuredContent??result;
        if(!result?.structuredContent&&Array.isArray(result?.content)){
          try{value=JSON.parse(result.content.find(c=>c.type==='text')?.text||'null');}catch{value=null;}
        }
        const data=value?.ok===true?value.data:value;
        if(result?.isError||value?.ok===false||data?.projectId===undefined||String(data.projectId)!==String(request.expectedProjectId))throw fail('PROJECT_CHANGED','当前项目与工作区认领不一致，未调用目标接口；请重新认领。');
      }
      // Important: invoke the site's registered WebMCP callback, never registry.execute
      // directly. Its confirmation, timeout and parameter validation remain in place.
      return await tool.execute(args, {});
    } finally { if (!readOnly) busy = null; }
  }

  window.addEventListener('message', event => {
    if (event.source !== window || event.origin !== window.location.origin) return;
    const request = event.data;
    if (!request || request.source !== IN || typeof request.id !== 'string' || request.id.length > 120) return;
    handle(request).then(result => {
      window.postMessage({source:OUT,id:request.id,result}, window.location.origin);
    }, error => {
      window.postMessage({source:OUT,id:request.id,error:{code:error.code || 'BRIDGE_ERROR',message:String(error.message || error)}}, window.location.origin);
    });
  });
})();
