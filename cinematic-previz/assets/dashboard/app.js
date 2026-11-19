'use strict';
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const short = value => String(value || '').slice(0, 12) || '—';
  const titles = {overview:['技能运行总览','看清当前规则的版本、更新和阅读入口。'],routes:['AI 如何读取规则','选择任务，查看读取器解析出的顺序、依赖与实际章节。'],updates:['每一次更新，都有来处','从当前 CHANGELOG 读取维护记录，查看规则如何变化。'],files:['规则文件，随时查阅','搜索当前安装的文件，直接查看原文和内容指纹。']};
  let state = null, view = 'overview', route = null, requested = [], selectedPreset = null;
  let refreshing = false, remoteBusy = false, routeRequest = 0, docRequest = 0;
  let documentData = null, documentMode = 'preview', findIndex = -1, toastTimer;
  let fileQuery = '', fileFilter = 'all', pickerQuery = '', pickerSelection = [];
  const localLabels = {current:['已同步','good','来源与安装内容一致。'],needs_sync:['有待同步内容','warn','注册来源与当前安装存在内容差异。'],metadata_stale:['登记信息待更新','warn','文件相同，但同步登记指纹已经过期。'],standalone:['独立安装','muted','未注册本机来源，直接读取当前安装。'],error:['检查失败','bad','暂时无法确认本机同步状态。']};
  const remoteLabels = {unchecked:['尚未核验','muted','点击核验，从 GitHub 读取远端并逐文件比对。'],verified:['内容已核验','good','已比对当前来源与远端内容，请留意核验时间。'],different:['仍有内容差异','warn','本机来源与远端存在差异，详情见下方。'],error:['核验未完成','bad','远端检查失败，保留实际错误信息。'],stale:['核验结果已过期','warn','本机内容在上次核验后发生了变化，请重新核验。']};

  function date(value, includeTime = true) {
    if (!value) return '未记录';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString('zh-CN', includeTime ? {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false} : {year:'numeric',month:'2-digit',day:'2-digit'});
  }
  function size(bytes) { return bytes > 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${bytes || 0} B`; }
  function notify(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 2700); }
  function showError(error) { $('#error-banner').textContent = String(error?.message || error); $('#error-banner').hidden = false; }
  async function api(path, options = {}) {
    const response = await fetch(path, {cache:'no-store',...options});
    let data;
    try { data = await response.json(); } catch { throw new Error('服务返回了无法读取的内容，请刷新页面。'); }
    if (!response.ok || data.error && !data.status) throw new Error(typeof data.error === 'string' ? data.error : '请求未完成。');
    return data;
  }
  function safeLink(href, label) {
    if (/^https?:\/\//i.test(href)) {
      try { const u = new URL(href); if (u.username || u.password) return esc(label); return `<a href="${esc(u.href)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`; } catch { return esc(label); }
    }
    if (href.startsWith('#')) return `<a href="${esc(href)}" data-anchor="${esc(href.slice(1))}">${esc(label)}</a>`;
    if (/^(?:[a-z][a-z0-9+.-]*:|\/\/|[\/\\])/i.test(href)) return esc(label);
    return `<a href="#" data-doc-link="${esc(href)}">${esc(label)}</a>`;
  }
  function inline(text) {
    const token = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^\n)]+\))/g;
    let output = '', end = 0, match;
    while ((match = token.exec(text))) {
      output += esc(text.slice(end,match.index));
      const value = match[0];
      if (value.startsWith('`')) output += `<code>${esc(value.slice(1,-1))}</code>`;
      else if (value.startsWith('**')) output += `<strong>${esc(value.slice(2,-2))}</strong>`;
      else { const link = /^\[([^\]]+)\]\((.+)\)$/.exec(value); output += safeLink(link[2].replace(/^<|>$/g,''),link[1]); }
      end = match.index + value.length;
    }
    return output + esc(text.slice(end));
  }
  function slug(value) { return value.toLowerCase().replace(/[^\p{L}\p{N}_\- ]/gu,'').replace(/ /g,'-'); }
  function markdown(text) {
    const lines = String(text || '').replace(/\r\n/g,'\n').split('\n');
    let out = '', list = null, paragraph = [], fence = null, code = [], seenHeadings = {};
    const flush = () => { if (paragraph.length) { out += `<p>${inline(paragraph.join('\n')).replace(/\n/g,'<br>')}</p>`; paragraph = []; } };
    const endList = () => { if (list) { out += `</${list}>`; list = null; } };
    for (let i=0; i<lines.length; i++) {
      const line = lines[i], trim = line.trim();
      const delimiter = /^(?:`{3,}|~{3,})/.exec(trim);
      if (fence) { if (delimiter && delimiter[0][0] === fence[0] && delimiter[0].length >= fence.length) { out += `<pre><code>${esc(code.join('\n'))}</code></pre>`; code=[]; fence=null; } else code.push(line); continue; }
      if (delimiter) { flush(); endList(); fence=delimiter[0]; continue; }
      if (!trim) { flush(); endList(); continue; }
      if (/^#{1,6} /.test(line)) {
        flush(); endList(); const [,marks,title] = /^(#{1,6}) (.+)$/.exec(line); const base = slug(title); const count = seenHeadings[base] || 0; seenHeadings[base] = count+1;
        out += `<h${marks.length} id="doc-${esc(base + (count ? '-'+count : ''))}">${inline(title)}</h${marks.length}>`; continue;
      }
      if (/^\|.*\|$/.test(trim) && i+1<lines.length && /^\|?\s*:?-{3,}/.test(lines[i+1].trim())) {
        flush(); endList(); const cells = l => l.trim().replace(/^\||\|$/g,'').split('|').map(s=>s.trim());
        out += `<div class="table-wrap"><table><thead><tr>${cells(line).map(c=>`<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>`; i++;
        while (i+1<lines.length && /^\|.*\|$/.test(lines[i+1].trim())) { i++; out += `<tr>${cells(lines[i]).map(c=>`<td>${inline(c)}</td>`).join('')}</tr>`; }
        out += '</tbody></table></div>'; continue;
      }
      if (/^([-*+] |\d+\. )/.test(trim)) {
        flush(); const type = /^\d/.test(trim)?'ol':'ul'; if (list !== type) { endList(); out += `<${type}>`; list=type; }
        out += `<li>${inline(trim.replace(/^([-*+] |\d+\. )/,''))}</li>`; continue;
      }
      if (/^>/.test(trim)) { flush(); endList(); out += `<blockquote>${inline(trim.replace(/^>\s?/,''))}</blockquote>`; continue; }
      if (/^(---+|\*\*\*+)$/.test(trim)) { flush(); endList(); out += '<hr>'; continue; }
      paragraph.push(line);
    }
    flush(); endList(); if(fence) out += `<pre><code>${esc(code.join('\n'))}</code></pre>`;
    return out;
  }
  function label(map, status) { return map[status] || [status || '未知','muted','请查看检查结果。']; }
  function differences(items) {
    if (!items?.length) return '';
    return `<details class="inline-details"><summary>查看 ${items.length} 项差异</summary><ul class="difference-list">${items.map(item=>`<li>${esc(typeof item === 'string' ? item : item.path)}${item.kind ? ' · '+esc(item.kind) : ''}</li>`).join('')}</ul></details>`;
  }
  function renderOverview() {
    const l=state.local || {}, r=state.remote || {}, ll=label(localLabels,l.status), rl=label(remoteLabels,r.status);
    const rules = String(state.entry_text || '').replace(/\r\n/g,'\n').replace(/^---\n[\s\S]*?\n---\n/,'').split(/\n\n/).filter(p=>p.trim()&&!p.trim().startsWith('#')).slice(0,2);
    const latest = state.changelog?.[0];
    $('#view-overview').innerHTML = `<div class="cards">
      <article class="status-card"><div class="card-label"><span class="number">A</span>本机同步</div><p class="status-title ${ll[1]}">${esc(ll[0])}</p><p class="card-description">${esc(l.error || ll[2])}</p><div class="fingerprint"><span>安装内容指纹</span><code>${esc(short(l.installed_revision))}</code></div></article>
      <article class="status-card"><div class="card-label"><span class="number">B</span>GitHub · 来源备份</div><p class="status-title ${rl[1]}">${esc(rl[0])}</p><p class="card-description">${esc(r.error || rl[2])}</p><div class="fingerprint"><span>${r.checked_at ? '核验 '+esc(date(r.checked_at)) : '远端独立核验'}</span><button class="text-action" data-remote ${remoteBusy?'disabled':''}>${remoteBusy?'正在核验…':'核验远端 ↗'}</button></div></article>
      <article class="status-card"><div class="card-label"><span class="number">C</span>当前内容版本</div><p class="status-title stat-number">${esc(state.version || '未声明')}</p><p class="card-description">${esc(state.catalog?.nodes?.filter(n=>n.readable).length || 0)} 个可读模块 · ${esc(state.files?.length || 0)} 个文件<br>按任务加载需要的规则。</p><div class="fingerprint"><span>模块索引指纹</span><code>${esc(short(state.catalog?.sha256))}</code></div></article>
    </div><div class="split"><article class="panel"><div class="panel-header"><div><h2>当前入口规则</h2><p>直接摘自正在使用的 SKILL.md</p></div><button class="text-action" data-document="SKILL.md">查看全文 ↗</button></div><div class="rule-excerpt">${rules.map((p,i)=>`<div class="excerpt-item"><span class="rule-number">0${i+1}</span><p>${inline(p)}</p></div>`).join('') || '<p>入口中尚无可展示的段落。</p>'}</div></article>
    <article class="panel"><div class="panel-header"><div><h2>最近更新</h2><p>以当前安装的更新记录为准</p></div><button class="text-action" data-go="updates">全部记录 ↗</button></div><div class="update-preview">${latest ? `<span class="date-label">${esc(latest.date || '日期未标注')}</span><h3>${esc(latest.title)}</h3><div class="markdown">${markdown(latest.body)}</div>` : '<p class="section-description">尚无更新记录。</p>'}</div></article></div>
    <div class="quick-entry"><div><h3>从任务出发，展开 AI 的读取路径</h3><p>顺序由现有模块读取器计算，每一步都能点开原文。</p></div><button class="button primary" data-go="routes">查看读取链 <span>→</span></button></div>
    <details class="source-details"><summary>查看来源、同步指纹与核验详情</summary><dl><dt>当前安装</dt><dd>${esc(state.root)}</dd><dt>注册来源</dt><dd>${esc(state.source_root || '独立安装，未注册来源')}</dd><dt>来源指纹</dt><dd>${esc(l.source_revision || '未记录')}</dd><dt>安装指纹</dt><dd>${esc(l.installed_revision)}</dd><dt>远端提交</dt><dd>${esc(r.commit || '尚未核验')}</dd><dt>核验时间</dt><dd>${esc(date(r.checked_at))}</dd><dt>远端分支</dt><dd>${esc(r.branch || '尚未核验')}</dd></dl>${differences(l.differences)}${differences(r.differences)}</details>`;
  }
  function renderRoutesShell() {
    $('#view-routes').innerHTML = `<div class="section-toolbar"><div><h2>选择这次要做的事</h2><p>先完成调用前检查，再按依赖读取专项规则。</p></div><span class="badge muted">规则解析 · 非历史日志</span></div>
      <div class="preset-bar" id="preset-bar">${(state.presets || []).map(p=>`<button class="preset ${p.id===selectedPreset?'selected':''}" data-preset="${esc(p.id)}" aria-pressed="${p.id===selectedPreset}">${esc(p.title)}</button>`).join('')}</div>
      <div id="route-result">${route ? routeMarkup() : '<div class="loading">正在计算模块依赖…</div>'}</div>
      <details class="module-picker"><summary>自选模块 / 查看模块分类</summary><div class="picker-tools"><label for="module-search" class="sr-only">搜索模块</label><input class="search-input" id="module-search" type="search" placeholder="搜索模块名称或 ID" value="${esc(pickerQuery)}"><button id="clear-modules" class="button subtle">清空选择</button><button id="apply-modules" class="button primary">生成读取链</button></div><div id="module-options" class="picker-grid">${pickerMarkup()}</div></details>`;
  }
  function pickerMarkup() {
    return (state.catalog?.nodes || []).filter(n=>n.readable).filter(n=>(n.id+' '+n.title).toLowerCase().includes(pickerQuery.toLowerCase())).map(n=>`<label class="module-choice"><input type="checkbox" name="module" value="${esc(n.id)}" ${pickerSelection.includes(n.id)?'checked':''}><span>${esc(n.title)}<small>${esc(n.id)}${n.parent ? ' / '+esc(n.parent) : ''}</small></span></label>`).join('') || '<p class="section-description">没有匹配的模块。</p>';
  }
  function routeMarkup() {
    const preset = state.presets?.find(p=>p.id===selectedPreset);
    return `<div class="route-toolbar"><div><strong>${esc(preset?.title || '自选模块')} <span class="count-detail">/ ${route.modules.length} 个模块</span></strong><p>${esc(preset?.description || '依赖由 module-index.json 和原有读取器确定。')}</p><p class="route-command">read_modules.py read ${esc(requested.join(' '))}</p></div><button id="copy-command" class="button">复制读取命令</button></div>
      <div class="route-layout route-layout-spaced"><div><div class="route-intro"><div class="gate-step"><span class="step-number">前置</span><div><h3>调用前同步检查</h3><p>current / updated 后继续；失败保留错误。</p></div></div><div class="gate-step"><span class="step-number">入口</span><div><h3>读取 SKILL.md</h3><p>确定范围、边界与专项入口。</p></div><button class="button subtle" data-document="SKILL.md">查看</button></div></div>
      <div class="route-chain" id="route-chain">${route.modules.map((m,i)=>`<button class="step-card ${m.reason==='requested'?'requested':''}" data-module="${esc(m.id)}"><div class="step-top"><span class="step-number">${String(i+1).padStart(2,'0')}</span><span class="step-tag">${m.reason==='required'?'每次必读':m.reason==='requested'?'本次选用':'依赖带入'}</span></div><h3>${esc(m.title)}</h3><div class="step-id">${esc(m.id)}</div><div class="step-source">${esc(m.source)}${m.sections?.length?`<br>指定 ${m.sections.length} 个章节`:'<br>读取完整文件'}</div>${m.required_by?.length?`<div class="step-relation">关联：${esc(m.required_by.join('、'))}</div>`:''}</button>`).join('')}</div>
      <p class="route-end">已解析 ${route.modules.length} 个模块，正文共 ${Number(route.content_chars || 0).toLocaleString('zh-CN')} 字符；点击任一步查看实际读取内容。</p></div></div><p class="route-note">分类父子关系与执行依赖分别来自 parent / requires；模块顺序沿用读取器结果。按规则解析不代表某次对话已实际读过。</p>`;
  }
  async function loadRoute(modules, presetId = null) {
    if (!modules.length) { notify('请至少选择一个可读模块。'); return; }
    const request = ++routeRequest; requested=[...new Set(modules)]; pickerSelection=[...requested]; selectedPreset=presetId; route=null;
    if ($('#route-result')) $('#route-result').innerHTML='<div class="loading">正在计算模块依赖…</div>';
    $$('.preset').forEach(b=>{const on=b.dataset.preset===selectedPreset; b.classList.toggle('selected',on);b.setAttribute('aria-pressed',String(on));});
    try { const result=await api('/api/route?modules='+encodeURIComponent(requested.join(','))); if(request!==routeRequest)return; route=result; if(view==='routes')renderRoutesShell(); }
    catch(error){ if(request!==routeRequest)return; if($('#route-result'))$('#route-result').innerHTML=`<div class="banner error">${esc(error.message)}</div>`; }
  }
  function renderUpdates() {
    $('#view-updates').innerHTML=`<div class="section-toolbar"><div><h2>维护记录</h2><p>展示文件中的记录顺序；日期为记录标注，并非远端发布证明。</p></div><button class="button" data-document="CHANGELOG.md">打开 CHANGELOG.md</button></div><div class="timeline">${(state.changelog||[]).map((item,i)=>`<article class="timeline-item"><div class="timeline-date">${esc(item.date || '日期未标注')}${i===0?'<br>最近一条':''}</div><div class="timeline-body"><h3>${esc(item.title)}</h3><div class="markdown update-body">${markdown(item.body)}</div></div></article>`).join('') || '<div class="empty">当前文件没有可解析的更新记录。</div>'}</div>`;
  }
  function renderFiles() {
    $('#view-files').innerHTML=`<div class="file-tools"><label class="sr-only" for="file-search">搜索文件</label><input class="search-input" id="file-search" type="search" placeholder="搜索文件名、路径或标题" value="${esc(fileQuery)}"><label class="sr-only" for="file-filter">文件类型</label><select id="file-filter"><option value="all">全部文件</option><option value="rules">规则文档</option><option value="scripts">脚本工具</option><option value="config">配置与界面</option></select><span class="file-count" id="file-count"></span></div><div id="file-results"></div>`;
    $('#file-filter').value=fileFilter; renderFileList();
  }
  function renderFileList() {
    const files=(state.files||[]).filter(f=>(f.path+' '+f.title).toLowerCase().includes(fileQuery.toLowerCase())).filter(f=>fileFilter==='all'||fileFilter==='rules'&&f.path.endsWith('.md')||fileFilter==='scripts'&&/\.(py|bat|ps1)$/.test(f.path)||fileFilter==='config'&&!/\.(md|py|bat|ps1)$/.test(f.path));
    $('#file-count').textContent=`${files.length} 个文件`;
    $('#file-results').innerHTML=files.length?`<div class="file-list">${files.map(f=>`<button class="file-row" data-document="${esc(f.path)}"><span class="file-name"><span class="file-symbol">${esc(f.path.split('.').pop().toUpperCase().slice(0,4))}</span><span><strong>${esc(f.title || f.path.split('/').pop())}</strong><small>${esc(f.path)}</small></span></span><span class="file-date">${esc(date(f.modified_at))}</span><span class="file-size">${esc(size(f.bytes))}</span></button>`).join('')}</div>`:'<div class="empty">没有匹配的文件，试试其他关键词。</div>';
  }
  function go(next) {
    if(!titles[next])return; view=next; location.hash=next;
    $$('.view').forEach(el=>{el.hidden=el.id!=='view-'+view;});
    $$('.nav-item').forEach(el=>{const active=el.dataset.view===view;el.classList.toggle('active',active);if(active)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
    $('#page-heading').textContent=titles[view][0];$('#page-description').textContent=titles[view][1];$('#breadcrumb-current').textContent=({overview:'状态总览',routes:'读取路径',updates:'更新记录',files:'规则文件'})[view];
    if(!state)return;
    if(view==='overview')renderOverview();if(view==='updates')renderUpdates();if(view==='files')renderFiles();
    if(view==='routes'){renderRoutesShell();if(!route){const preset=state.presets?.[0];if(preset)loadRoute(preset.modules,preset.id);}}
  }
  async function refresh(manual=false) {
    if(refreshing)return;refreshing=true;$('#refresh-button').disabled=true;
    try {
      const next=await api('/api/state');const changed=state?.catalog?.sha256!==next.catalog?.sha256 || state?.local?.installed_revision!==next.local?.installed_revision;
      state=next;$('#error-banner').hidden=true;const ll=label(localLabels,state.local?.status);$('#local-badge').className='badge '+ll[1];$('#local-badge').textContent='本机 · '+ll[0];$('#side-version').textContent='v'+(state.version || '—');$('#checked-time').textContent='最近检查 '+date(state.checked_at);
      if(view==='overview')renderOverview();if(view==='updates')renderUpdates();if(view==='files'){if($('#file-results'))renderFileList();else renderFiles();}
      if(view==='routes'&&!$('#preset-bar'))renderRoutesShell();if(view==='routes'&&(changed||manual)&&requested.length)loadRoute(requested,selectedPreset);
      if(manual)notify('已重新读取本机文件。');
    }catch(error){showError(error);$('#local-badge').className='badge bad';$('#local-badge').textContent='本机 · 读取失败';}
    finally{refreshing=false;$('#refresh-button').disabled=false;}
  }
  async function checkRemote() {
    if(remoteBusy)return;remoteBusy=true;if(view==='overview')renderOverview();
    try {const result=await api('/api/remote-check',{method:'POST'});if(state)state.remote=result;notify(result.status==='verified'?'远端核验完成，内容一致。':result.status==='different'?'远端核验完成，发现内容差异。':result.error || '远端检查已返回结果。');await refresh();}
    catch(error){showError(error);}finally{remoteBusy=false;if(view==='overview'&&state)renderOverview();}
  }
  function renderDocument() {
    if(!documentData)return;
    const d=documentData;$('#document-title').textContent=d.title || d.path?.split('/').pop() || '文档';$('#document-path').textContent=d.path || '';
    $('#document-type').textContent=d.module ? '本次读取 · '+d.module : '当前安装 · 原文件';
    $('#document-meta').innerHTML=`${d.sections?.length?'本模块只读取以下章节：'+esc(d.sections.join('；')):d.module?'本模块读取完整文件。':'直接读取当前安装文件。'}<br>SHA-256 <code>${esc(d.sha256 || '未提供')}</code>${d.modified_at?' · 文件修改 '+esc(date(d.modified_at)):''}`;
    $('#full-document').hidden=!d.module;$('#source-mode').classList.toggle('selected',documentMode==='source');$('#preview-mode').classList.toggle('selected',documentMode==='preview');$('#source-mode').setAttribute('aria-pressed',String(documentMode==='source'));$('#preview-mode').setAttribute('aria-pressed',String(documentMode==='preview'));
    $('#document-content').innerHTML=documentMode==='source'||d.path&&!/\.md$/i.test(d.path)?`<pre class="source-text">${esc(d.content)}</pre>`:markdown(d.content);
    findIndex=-1;$('#find-count').textContent='';
  }
  function showDocument(data, anchor) {
    documentData=data;documentMode='preview';$('#document-search').value='';renderDocument();const dialog=$('#document-dialog');if(!dialog.open)dialog.showModal();$('#document-content').scrollTop=0;
    if(anchor)scrollAnchor(anchor);
  }
  async function openFile(path, anchor) {
    const request=++docRequest;
    try { const data=await api('/api/document?path='+encodeURIComponent(path));if(request!==docRequest)return;showDocument(data,anchor); }
    catch(error){notify(error.message);}
  }
  function scrollAnchor(anchor) { const target=document.getElementById('doc-'+anchor);if(target&&$('#document-content').contains(target))target.scrollIntoView({block:'start'}); }
  function openLink(href) {
    const base=documentData?.path || 'SKILL.md';
    try { const u=new URL(href,new URL('/'+base,location.origin));if(u.origin!==location.origin)return;openFile(decodeURIComponent(u.pathname.slice(1)),decodeURIComponent(u.hash.slice(1))); }catch{notify('无法解析这个文档链接。');}
  }
  function findText(next=false) {
    if(!documentData)return;const query=$('#document-search').value.trim();
    renderDocument();if(!query){$('#find-count').textContent='';return;}
    const root=$('#document-content'),walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT), nodes=[];let node;
    while((node=walker.nextNode()))if(!['SCRIPT','STYLE'].includes(node.parentElement?.tagName))nodes.push(node);
    let total=0;const lower=query.toLocaleLowerCase();
    for(const textNode of nodes){const value=textNode.nodeValue,hay=value.toLocaleLowerCase();let at=0,pos=hay.indexOf(lower),fragment=document.createDocumentFragment();if(pos<0)continue;while(pos>=0){fragment.append(document.createTextNode(value.slice(at,pos)));const mark=document.createElement('mark');mark.textContent=value.slice(pos,pos+query.length);fragment.append(mark);total++;at=pos+query.length;pos=hay.indexOf(lower,at);}fragment.append(document.createTextNode(value.slice(at)));textNode.replaceWith(fragment);}
    const matches=$$('mark',root);findIndex=total?(next?(Number($('#document-search').dataset.index || -1)+1)%total:0):-1;$('#document-search').dataset.index=String(findIndex);$('#find-count').textContent=total?`${findIndex+1} / ${total}`:'没有匹配';
    if(matches[findIndex]){matches[findIndex].classList.add('current-match');matches[findIndex].scrollIntoView({block:'center'});}
  }
  async function copyCommand() {
    const command='python "'+state.root.replace(/\\/g,'/')+'/scripts/read_modules.py" read '+requested.join(' ')+' --json';
    try{await navigator.clipboard.writeText(command);notify('读取命令已复制。');}catch{notify('浏览器不支持自动复制，可在读取路径中选中文本。');}
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('button,a');if(!button)return;
    if(button.dataset.view){go(button.dataset.view);return;}if(button.dataset.go){go(button.dataset.go);return;}
    if(button.hasAttribute('data-remote')){checkRemote();return;}if(button.dataset.document){openFile(button.dataset.document);return;}
    if(button.dataset.preset){const preset=state.presets.find(p=>p.id===button.dataset.preset);if(preset)loadRoute(preset.modules,preset.id);return;}
    if(button.dataset.module){const m=route?.modules.find(m=>m.id===button.dataset.module);if(m){++docRequest;showDocument({path:m.source,title:m.title,module:m.id,content:m.content,sections:m.sections,sha256:m.source_sha256});}return;}
    if(button.dataset.docLink){event.preventDefault();openLink(button.dataset.docLink);return;}if(button.hasAttribute('data-anchor')){event.preventDefault();scrollAnchor(button.dataset.anchor);}
    if(button.id==='refresh-button')refresh(true);
    if(button.id==='close-document'){$('#document-dialog').close();++docRequest;}
    if(button.id==='source-mode'||button.id==='preview-mode'){documentMode=button.id==='source-mode'?'source':'preview';renderDocument();if($('#document-search').value)findText();}
    if(button.id==='full-document')openFile(documentData.path);
    if(button.id==='find-next')findText(true);
    if(button.id==='copy-command')copyCommand();
    if(button.id==='clear-modules'){pickerSelection=[];$$('input[name="module"]').forEach(c=>{c.checked=false;});}
    if(button.id==='apply-modules')loadRoute(pickerSelection,null);
  });
  document.addEventListener('input',event=>{
    if(event.target.id==='file-search'){fileQuery=event.target.value;renderFileList();}
    if(event.target.id==='module-search'){pickerQuery=event.target.value;$('#module-options').innerHTML=pickerMarkup();}
    if(event.target.name==='module'){if(event.target.checked&&!pickerSelection.includes(event.target.value))pickerSelection.push(event.target.value);if(!event.target.checked)pickerSelection=pickerSelection.filter(id=>id!==event.target.value);}
    if(event.target.id==='document-search'){$('#document-search').dataset.index='-1';}
  });
  document.addEventListener('change',event=>{if(event.target.id==='file-filter'){fileFilter=event.target.value;renderFileList();}});
  $('#document-search').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();findText(true);}});
  $('#document-dialog').addEventListener('cancel',()=>{++docRequest;});
  window.addEventListener('hashchange',()=>{const next=location.hash.slice(1);if(titles[next]&&next!==view)go(next);});
  async function start(){const initial=location.hash.slice(1);if(titles[initial])view=initial;go(view);await refresh();go(view);setInterval(()=>refresh(),15000);}
  start();
})();
