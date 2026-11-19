/* Shared protocol client; loaded in the extension's isolated world. */
((root) => {
  'use strict';
  const failure = (code, message) => Object.assign(new Error(message), {code});
  function unwrap(result) {
    let value = result?.structuredContent ?? result;
    if (!result?.structuredContent && Array.isArray(result?.content)) {
      const text = result.content.find(c => c.type === 'text')?.text;
      if (text) { try { value = JSON.parse(text); } catch { value = {text}; } }
    }
    if (result?.isError || value?.ok === false) {
      throw failure(value?.error?.code || 'SITE_ERROR', value?.error?.message || '网站返回操作失败。');
    }
    return value?.ok === true && Object.hasOwn(value, 'data') ? value.data : value;
  }

  function createClient(win, options = {}) {
    const pending = new Map();
    const receive = event => {
      if (event.source !== win || event.origin !== win.location.origin) return;
      const message = event.data;
      if (message?.source !== 'updream-helper/page/v1') return;
      const p = pending.get(message.id);
      if (!p) return;
      clearTimeout(p.timer);
      pending.delete(message.id);
      if (message.error) p.reject(failure(message.error.code, message.error.message));
      else p.resolve(message.result);
    };
    win.addEventListener('message', receive);
    function request(action, data = {}) {
      const id = win.crypto.randomUUID();
      return new Promise((resolve, reject) => {
        const timeout = options.timeoutMs ?? (action === 'execute' ? 180000 : 5000);
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(failure(action === 'execute' ? 'UNKNOWN_OUTCOME' : 'BRIDGE_TIMEOUT', action === 'execute'
            ? '等待结果超时，网站中的操作可能仍在执行。请先处理网站确认框或查询节点状态，勿重复提交。'
            : '没有收到网页桥接响应。安装扩展后请手动刷新 updream 页面。'));
        }, timeout);
        pending.set(id, {resolve,reject,timer});
        win.postMessage({source:'updream-helper/content/v1',id,action,...data}, win.location.origin);
      });
    }
    const execute = async (tool, args = {}) => unwrap(await request('execute', {tool,arguments:args}));
    return {
      request, execute,
      dispose() {
        win.removeEventListener('message', receive);
        for (const p of pending.values()) { clearTimeout(p.timer); p.reject(failure('DISPOSED','页面连接已关闭。')); }
        pending.clear();
      },
    };
  }
  const api = {createClient,unwrap};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.UpdreamHelperClient = api;
})(globalThis);
