  // ---- App shell: side navigation, AI status chip, and the Overview cards that answer
  // "how is it built?", "where do I start?" and "ask about it". Built from the loaded graph,
  // so they work without the server too (except asking, which needs the local server).
  function currentNav() {
    if (state.page === 'dashboard') return 'overview';
    if (state.page === 'settings') return 'settings';
    if (state.page === 'ask' || state.page === 'health' || state.page === 'system' || state.page === 'agents' || state.page === 'documentation') return state.page;
    return state.lens === 'architecture' ? 'architecture' : state.lens === 'flow' ? 'flow' : 'graph';
  }
  function updateNav() {
    const active = currentNav();
    document.querySelectorAll('.nav-item[data-nav]').forEach(b => { const on = b.dataset.nav === active; b.classList.toggle('active', on); if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
    const changes = $('.nav-item[data-nav="changes"]');
    if (changes) changes.disabled = !(serverToken && state.projectPath);
  }
  function openGraph(lens = 'full') {
    showPage('graph');
    if (state.lens !== lens) setLens(lens);
    updateNav();
  }
  function askFromOverview(text, mode = serverToken ? 'agent' : 'query') {
    showPage('ask');
    setAskMode(mode);
    $('#ask-a').value = text;
    if (text.trim()) runAsk(); else $('#ask-a').focus();
  }
  let claudeAgentsRequest = 0;
  const benchmarkRows = new Map();
  let benchmarkProject = null;
  async function loadBenchModels() {
    const select = $('#bench-model');
    if (!serverToken) { select.replaceChildren(new Option('Local server required', '')); return; }
    try {
      const result = await api('/api/agent/status');
      const models = Array.isArray(result.models) ? result.models : [];
      const previous = select.value;
      select.replaceChildren(...(models.length ? models : ['']).map(name => new Option(name || 'No local model installed', name)));
      if (models.includes(previous)) select.value = previous;
      else if (models.includes('qwen3:14b-q4_K_M')) select.value = 'qwen3:14b-q4_K_M';
    } catch (_) { select.replaceChildren(new Option('Local model unavailable', '')); }
  }
  function updateBenchFields() {
    const agent = $('#bench-agent').value, bridge = agent === 'bridge';
    document.querySelectorAll('.bench-log').forEach(el => el.classList.toggle('hidden', agent !== 'log'));
    document.querySelectorAll('.bench-bridge').forEach(el => el.classList.toggle('hidden', !bridge));
    document.querySelectorAll('.bench-argo').forEach(el => el.classList.toggle('hidden', !bridge || $('#bench-bridge-source').value !== 'argocd'));
    const noLlm = $('#bench-no-llm');
    noLlm.disabled = agent === 'graph' || agent === 'explorer' || bridge;
    if (noLlm.disabled) noLlm.checked = agent === 'graph';
    $('#bench-model').disabled = agent === 'graph' || noLlm.checked;
  }
  function renderBenchResults() {
    const rows = [...benchmarkRows.values()];
    $('#bench-results').innerHTML = rows.length ? `<table class="agent-bench-table"><thead><tr><th>Agent</th><th>Mode</th><th>Median</th><th>Range</th><th>Runs</th></tr></thead><tbody>${rows.map(row => `<tr><td>${escapeHtml(row.name)}</td><td>${escapeHtml(row.mode)}</td><td>${row.median === null ? '—' : `${row.median.toFixed(3)} s`}</td><td>${row.min === null ? '—' : `${row.min.toFixed(3)}–${row.max.toFixed(3)} s`}</td><td>${row.success} passed${row.failed ? `, ${row.failed} failed` : ''}</td></tr>`).join('')}</tbody></table>` : '';
  }
  async function runAgentBenchmark(event) {
    event.preventDefault();
    const project = state.projectPath, agent = $('#bench-agent').value, button = $('#bench-run'), status = $('#bench-status');
    if (!serverToken || !project) { status.textContent = 'Open a project using the local server first.'; return; }
    if (benchmarkProject !== project) { benchmarkRows.clear(); benchmarkProject = project; renderBenchResults(); }
    const noLlm = $('#bench-no-llm').checked;
    const body = {project, agent, question: $('#bench-question').value.trim(), model: $('#bench-model').value,
      repeats: Number($('#bench-repeats').value), no_llm: noLlm, log: $('#bench-log').value.trim(), log_source: $('#bench-log-source').value,
      bridge_source: $('#bench-bridge-source').value, service: $('#bench-service').value.trim(), application: $('#bench-application').value.trim(),
      app_namespace: $('#bench-app-namespace').value.trim(), namespace: $('#bench-namespace').value.trim()};
    const name = $('#bench-agent').selectedOptions[0].textContent;
    button.disabled = true; status.textContent = `Running ${name}… This can take several minutes.`;
    try {
      const result = await api('/api/agent/benchmark', body);
      if (state.projectPath !== project) return;
      const row = result.results?.[agent];
      if (!row) throw new Error('No benchmark result was returned.');
      benchmarkRows.set(agent, {name, mode: agent === 'graph' || noLlm ? 'No model' : body.model,
        median: row.median_seconds, min: row.min_seconds, max: row.max_seconds, success: row.successful_runs, failed: row.failed_runs});
      renderBenchResults();
      status.textContent = row.failed_runs ? `${row.failed_runs} run(s) failed or timed out. Check the local model and inputs.` : `Finished ${name}. Times include process startup; model cache state is not controlled.`;
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  }
  async function loadClaudeAgents() {
    const seq = ++claudeAgentsRequest, project = state.projectPath;
    const status = $('#claude-agents-status'), list = $('#claude-agents-list');
    if (!serverToken) {
      status.textContent = 'Open Token Graph through its local server to see Claude Code agents.';
      list.innerHTML = ''; return;
    }
    status.textContent = 'Loading Claude Code agents…';
    try {
      const result = await api(`/api/claude-agents${project ? `?project=${encodeURIComponent(project)}` : ''}`);
      if (seq !== claudeAgentsRequest) return;
      const agents = Array.isArray(result.agents) ? result.agents : [];
      status.textContent = agents.length ? `${agents.length} Claude Code agent${agents.length === 1 ? '' : 's'} found. These run in Claude Code.` : 'No Claude Code agent definitions found.';
      list.innerHTML = agents.map(agent => `<article class="dash-card w-6 agent-card"><header><h2>${escapeHtml(agent.name)}</h2><span class="agent-badge">Claude Code · ${escapeHtml(agent.scope)}</span></header><div class="dash-body"><p>${escapeHtml(agent.description || 'No description provided.')}</p><dl>${agent.model ? `<dt>Model</dt><dd>${escapeHtml(agent.model)}</dd>` : ''}${agent.effort ? `<dt>Effort</dt><dd>${escapeHtml(agent.effort)}</dd>` : ''}${agent.tools ? `<dt>Tools</dt><dd>${escapeHtml(agent.tools)}</dd>` : ''}</dl></div></article>`).join('');
    } catch (error) {
      if (seq !== claudeAgentsRequest) return;
      list.innerHTML = ''; status.textContent = `Could not load Claude Code agents: ${error.message}`;
    }
  }
  function onNav(target) {
    const nav = target.dataset.nav;
    if (nav === 'overview') showPage('dashboard');
    else if (nav === 'graph') { openGraph('full'); setView('all'); }
    else if (nav === 'documentation') showPage('documentation');
    else if (nav === 'architecture') openGraph('architecture');
    else if (nav === 'flow') openGraph('flow');
    else if (nav === 'ask') askFromOverview('');
    else if (nav === 'changes') { openGraph('full'); showDiff(); }
    else if (nav === 'docs') { showPage('documentation'); generateDocs(); }
    else if (nav === 'settings') showPage('settings');
    else if (nav === 'health') showPage('health');
    else if (nav === 'system') showPage('system');
    else if (nav === 'agents') showPage('agents');
    updateNav();
  }
  let serverCheckPending = false;
  async function checkServerConnection() {
    const badge = $('#server-connection');
    if (!serverToken) { badge.textContent = 'Standalone file'; badge.title = 'Open Token Graph with its local server for project tools.'; return; }
    if (serverCheckPending || document.visibilityState === 'hidden') return;
    serverCheckPending = true;
    try {
      await api('/api/ping');
      badge.textContent = 'Server connected'; badge.className = 'server-pill online'; badge.title = 'Local Token Graph server is responding.';
    } catch (_) {
      badge.textContent = 'Server unavailable'; badge.className = 'server-pill offline'; badge.title = 'Restart Token Graph and open the new address it provides.';
    } finally { serverCheckPending = false; }
  }
  checkServerConnection();
  setInterval(checkServerConnection, 15000);
  // AI colour: every local model gets its own colour (--ai-color: chip border, Ask borders), so switching
  // models is visible at a glance; while AI works (a question, or an AI graph build) they pulse in it.
  const aiWork = { asks: 0, jobModel: '' };
  function modelColor(name) {
    if (!name) return '#2c3953';
    let h = 7; for (const c of String(name)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return `hsl(${h % 360} 72% 62%)`;
  }
  function activeModel() { return aiWork.jobModel || $('#llm-model')?.value || $('#agent-model')?.value || dash.ai?.chat_models?.[0] || ''; }
  // The Ask page's LLM model picker: installed local chat models, last choice remembered.
  function fillLlmModels() {
    const select = $('#llm-model'), models = dash.ai?.chat_models || [];
    if (!select || select.dataset.models === models.join('|')) return;
    let saved = ''; try { saved = localStorage.getItem('tokenGraph.llmModel') || ''; } catch (_) { /* per-viewer convenience only */ }
    const pick = models.includes(select.value) ? select.value : models.includes(saved) ? saved : models[0] || '';
    const emptyLabel = serverToken ? 'No local model installed' : 'Open local server to see models';
    select.replaceChildren(...(models.length ? models : ['']).map(name => new Option(name || emptyLabel, name)));
    select.value = pick; select.dataset.models = models.join('|');
  }
  function setAiBusy(on) { aiWork.asks = Math.max(0, aiWork.asks + (on ? 1 : -1)); updateAiChip(); }
  function noteAiJob(job) { const model = job?.state === 'running' && job.model ? String(job.model) : ''; if (model !== aiWork.jobModel) { aiWork.jobModel = model; updateAiChip(); } }
  function updateAiChip() {
    fillLlmModels();
    const chip = $('#ai-chip'), ai = dash.ai, model = activeModel(), busy = aiWork.asks > 0 || Boolean(aiWork.jobModel);
    document.documentElement?.style.setProperty('--ai-color', serverToken && model ? modelColor(model) : '#2c3953');
    $('.app-shell').classList.toggle('ai-busy', busy);
    if (!chip) return;
    const ok = Boolean(ai?.reachable && (ai.chat_models || []).length);
    chip.className = `ai-chip ${!serverToken ? 'off' : busy ? 'busy' : ok ? 'good' : ai ? 'warning' : ''}`;
    chip.innerHTML = `<span class="ai-dot" aria-hidden="true"></span>${escapeHtml(!serverToken ? 'AI · server off' : busy ? `AI ⟳ ${model || 'local model'} working…` : !ai ? 'AI · checking' : ok ? `AI ✓ ${model}` : ai.reachable ? 'AI · no model' : 'AI · offline')}`;
    chip.title = busy ? `The local model ${model} is working` : 'Local AI status · open Settings';
  }

  // Shared by the overview cards.
  const shortModule = id => String(id).startsWith('(') ? String(id) : String(id).split('/').filter(Boolean).pop() || id;
  const degreeOf = node => state.adjacency.get(node.id)?.length || 0;
  function keyComponents(limit = 8) {
    return state.nodes.filter(n => n.source_file && passesFilters(n) && !['file', 'external', 'document'].includes(n._kind))
      .sort((a, b) => degreeOf(b) - degreeOf(a)).slice(0, limit);
  }
  function moduleLinks() {
    const links = new Map();
    for (const link of state.links) {
      if (relationKind(link.relation) === 'structure') continue;
      const a = state.nodeById.get(link._src ?? link.source), b = state.nodeById.get(link._tgt ?? link.target);
      if (!a || !b || a._module === b._module || !passesFilters(a) || !passesFilters(b)) continue;
      const k = `${a._module}\u0000${b._module}`;
      links.set(k, (links.get(k) || 0) + 1);
    }
    return [...links].map(([k, n]) => { const [from, to] = k.split('\u0000'); return { from, to, count: n }; }).sort((x, y) => y.count - x.count);
  }

  function renderAskCard() {
    if (!serverToken) { setCard('ask', serverOnlyHtml('Asking questions about the code'), pill('off', 'Server off')); return; }
    const name = $('#graph-selector').selectedOptions?.[0]?.textContent || 'this project';
    const [c1, c2] = keyComponents(2), m1 = state.modules.find(m => !m.id.startsWith('('));
    const suggestions = [
      ['Explain the architecture of this project', 'query'],
      c1 && [`How does ${c1.label} work?`, 'query'],
      c2 && [`What breaks if ${c2.label} changes?`, 'affected', c2.label],
      m1 && [`What is in ${shortModule(m1.id)}?`, 'query'],
    ].filter(Boolean);
    const model = dash.ai?.reachable && dash.ai.chat_models?.[0];
    setCard('ask', `<form class="dash-ask-form" data-ask-form><input class="ask-input" id="dash-ask-input" autocomplete="off" placeholder="Ask about ${escapeHtml(name)}, e.g. where is the balance updated?" /><button type="submit" class="dash-action primary" title="Graph search: fast, exact, no model"><span aria-hidden="true">✦</span>Ask AI</button><button type="button" class="dash-action" data-dash="ask-llm" title="Local model answers in its own words with file:line references"><span aria-hidden="true">◆</span>Ask LLM</button></form>
      <div class="dash-suggest">${suggestions.map(([q, mode, value]) => `<button type="button" class="dash-link" data-dash="ask-suggest" data-mode="${mode}" data-q="${escapeHtml(value || q)}">${escapeHtml(q)}</button>`).join('')}</div>
      <p class="dash-note">Answers come from this project's graph and source files on this computer, with file and line references${model ? ` · local model available: ${escapeHtml(model)}` : ''}.</p>`);
  }

  function renderModuleMap() {
    // Project modules only; symbols with no source file (frameworks, unresolved types) are counted separately.
    const outside = state.modules.filter(m => m.id === '(external / unresolved)').reduce((n, m) => n + m.nodes.filter(passesFilters).length, 0);
    const allMods = state.modules.filter(m => m.id !== '(external / unresolved)' && m.nodes.some(passesFilters));
    const mods = allMods.slice(0, 14);
    if (!mods.length) { setCard('architecture', '<div class="dash-empty"><p>No source files to group into modules.</p></div>'); return; }
    const ids = new Set(mods.map(m => m.id)), links = moduleLinks().filter(l => ids.has(l.from) && ids.has(l.to));
    const W = 480, H = 250, R = mods.length === 1 ? 0 : 88, max = Math.max(1, ...mods.map(m => m.nodes.length));
    const pos = new Map(mods.map((m, i) => { const a = -Math.PI / 2 + i / mods.length * Math.PI * 2; return [m.id, { x: W / 2 + Math.cos(a) * R * 1.2, y: H / 2 + Math.sin(a) * R, a, r: 6 + 16 * Math.sqrt(m.nodes.length / max) }]; }));
    const maxLink = Math.max(1, ...links.map(l => l.count));
    const lines = links.slice(0, 30).map(l => {
      const a = pos.get(l.from), b = pos.get(l.to), dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
      const x2 = b.x - dx / len * (b.r + 3), y2 = b.y - dy / len * (b.r + 3);
      return `<line x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke-width="${(.8 + 2.6 * l.count / maxLink).toFixed(2)}" marker-end="url(#dash-arrow)"><title>${escapeHtml(`${l.from} → ${l.to}: ${l.count} links`)}</title></line>`;
    }).join('');
    const dots = mods.map(m => {
      const p = pos.get(m.id), right = Math.cos(p.a) >= -0.01, below = Math.sin(p.a) > .6, above = Math.sin(p.a) < -.6;
      const tx = above || below ? p.x : p.x + (right ? p.r + 5 : -p.r - 5), ty = above ? p.y - p.r - 6 : below ? p.y + p.r + 13 : p.y + 4;
      return `<g class="dash-mod" data-dash="module" data-id="${escapeHtml(m.id)}" tabindex="0" role="button" aria-label="${escapeHtml(`${m.id}: ${m.nodes.length} nodes`)}"><circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${p.r.toFixed(1)}" fill="${colorFor(state.moduleIndex.get(m.id))}"><title>${escapeHtml(`${m.id} · ${m.nodes.length} nodes`)}</title></circle><text x="${tx.toFixed(1)}" y="${ty.toFixed(1)}" text-anchor="${above || below ? 'middle' : right ? 'start' : 'end'}">${escapeHtml(m.id.slice(0, 26))}</text></g>`;
    }).join('');
    const svg = `<svg class="dash-modmap" viewBox="0 0 ${W} ${H}" role="img" aria-label="Modules and their dependencies"><defs><marker id="dash-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z"></path></marker></defs><g class="dash-modlinks">${lines}</g>${dots}</svg>`;
    const strongest = links.length
      ? `<div class="dash-rows">${links.slice(0, 5).map(l => row(`${l.from} →`, escapeHtml(l.to), count(l.count, 'link'))).join('')}</div>`
      : '<p class="dash-note">No dependencies between these modules.</p>';
    setCard('architecture', `<p class="dash-note">${allMods.length > mods.length ? `Showing ${mods.length} of ${allMods.length} folder groups` : `${count(mods.length, 'folder group')}`}${outside ? `, plus ${format(outside)} external or unresolved symbols` : ''}. Arrows summarize detected calls, imports and references between folders; verify important links in the source. Click a group to open it.</p>${svg}<p class="dash-sub">Most detected links</p>${strongest}<div class="dash-footer"><button type="button" class="dash-link" data-dash="open-architecture">Open architecture view</button></div>`, pill('info', count(allMods.length, 'group')));
  }

  function renderComponents() {
    const top = keyComponents(8);
    if (!top.length) { setCard('components', '<div class="dash-empty"><p>No connected code symbols yet.</p></div>'); return; }
    const kind = n => ({ type: 'type', method: 'method', function: 'function' })[n._kind] || n._kind;
    setCard('components', `<p class="dash-note">The most connected code: a good place to start reading. Click one to open it in the graph.</p><ol class="dash-components">${top.map(n => `<li><button type="button" data-dash="node" data-id="${escapeHtml(n.id)}" title="${escapeHtml(`${n.source_file}${n.source_location ? ':' + n.source_location : ''}`)}"><span class="name">${escapeHtml(n.label)}</span><span class="meta">${escapeHtml(kind(n))} · ${escapeHtml(shortModule(n._module))}</span><span class="links">${format(degreeOf(n))}</span></button></li>`).join('')}</ol>`, pill('info', 'by links'));
  }

  // Overview actions; returns true when handled (called from onDashboardClick).
  function onShellAction(target) {
    const action = target.dataset.dash;
    if (action === 'ask-suggest') { askFromOverview(target.dataset.q, target.dataset.mode === 'affected' ? 'affected' : serverToken ? 'agent' : 'query'); return true; }
    if (action === 'ask-llm') { askFromOverview($('#dash-ask-input').value, 'llm'); return true; }
    if (action === 'changes') { onNav({ dataset: { nav: 'changes' } }); return true; }
    if (action === 'open-architecture') { openGraph('architecture'); return true; }
    if (action === 'module') { openGraph('architecture'); showClusterDetails(state.modules[state.moduleIndex.get(target.dataset.id)]); return true; }
    if (action === 'node') { const node = state.nodeById.get(target.dataset.id); openGraph('full'); if (node) { select(node); centerNode(node); draw(); } return true; }
    return false;
  }
  document.querySelectorAll('[data-nav]').forEach(b => b.addEventListener('click', () => onNav(b)));
  // Sidebar groups (communities / folders) as a dropdown; remembered per browser.
  function setGroupsOpen(open) {
    state.groupsOpen = open; store('tokenGraph.groupsOpen', open);
    $('#group-dropdown').setAttribute('aria-expanded', String(open));
    $('#group-dropdown').classList.toggle('open', open);
    $('#group-body').classList.toggle('hidden', !open);
  }
  $('#group-dropdown').addEventListener('click', () => setGroupsOpen(!state.groupsOpen));
  setGroupsOpen(state.groupsOpen);
  $('#agent-model').addEventListener('change', updateAiChip);
  $('#llm-model').addEventListener('change', () => { try { localStorage.setItem('tokenGraph.llmModel', $('#llm-model').value); } catch (_) { /* convenience */ } updateAiChip(); if (askMode === 'llm') setAskMode('llm'); });
  document.querySelectorAll('[data-engine]').forEach(b => b.addEventListener('click', () => setAskMode(b.dataset.engine === 'llm' ? 'llm' : 'query')));
  // Ask page → graph with the answer highlighted.
  $('#ask-show-graph').addEventListener('click', () => { showPage('graph'); if (state.highlight?.nodes?.size) fitGraph(new Set(state.highlight.nodes.keys())); });
  // The Filters dropdown closes when clicking elsewhere.
  document.addEventListener('click', event => { const box = $('#graph-filters'); if (box?.open && !box.contains?.(event.target)) box.open = false; });  // a different model, a different colour
  $('#dashboard').addEventListener('submit', event => {
    if (!event.target.matches?.('[data-ask-form]')) return;
    event.preventDefault();
    askFromOverview($('#dash-ask-input').value);
  });
  $('#dashboard').addEventListener('keydown', event => {
    if ((event.key === 'Enter' || event.key === ' ') && event.target.matches?.('.dash-mod')) { event.preventDefault(); onShellAction(event.target); }
  });
  // After start-up (a microtask, so `dash` from js/11-dashboard.js exists): fill the AI chip.
  Promise.resolve().then(() => serverToken ? api('/api/ai/status').then(ai => { dash.ai = ai; dash.aiAt = Date.now(); }).catch(() => {}) : null).then(() => updateAiChip());
