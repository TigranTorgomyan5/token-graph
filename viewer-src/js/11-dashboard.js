  // ---- Dashboard page: an overview of the open project. Figures come from the loaded graph
  // and the local server (/api/dashboard, /api/ai/status); anything that cannot be measured
  // is shown as unavailable with the reason, never estimated. Detail stays on the Graph page.
  const dash = { seq: 0, data: null, dataProject: null, error: '', loading: false, ai: null, aiAt: 0, aiError: '', aiLoading: false, job: null, docs: null, pending: null, pendingError: '', savings: null, savingsProject: null, savingsError: '' };
  const STATUS_ICON = { good: '✓', warning: '!', critical: '✕', busy: '⟳', info: 'i', off: '–' };
  const ACTIVITY_ICON = { build: '◉', update: '↻', ai: '✦', question: '?', ai_question: '✦', analysis: '▦', docs: '≡' };
  const pill = (status, text, title = '') => `<span class="dash-pill ${status}"${title ? ` title="${escapeHtml(title)}"` : ''}><span aria-hidden="true">${STATUS_ICON[status]}</span>${escapeHtml(text)}</span>`;
  const row = (label, valueHtml, noteHtml = '') => `<div class="dash-row"><span class="dash-label">${escapeHtml(label)}</span><span class="dash-value">${valueHtml}</span>${noteHtml ? `<span class="dash-note">${noteHtml}</span>` : ''}</div>`;
  const tile = (label, valueHtml, note = '') => `<div class="dash-tile"><span class="dash-label">${escapeHtml(label)}</span><strong>${valueHtml}</strong>${note ? `<span class="dash-note">${escapeHtml(note)}</span>` : ''}</div>`;
  const na = (text = 'Not available') => `<span class="dash-na">${escapeHtml(text)}</span>`;
  const share = (part, whole) => whole ? `${Math.round(100 * part / whole)}%` : '—';
  const count = (n, word, plural = `${word}s`) => `${format(n)} ${n === 1 ? word : plural}`;
  const ago = seconds => {
    if (!seconds) return 'never';
    const s = Math.max(0, Date.now() / 1000 - seconds);
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} days ago`;
  };
  const timeTag = seconds => `<time datetime="${new Date(seconds * 1000).toISOString()}" title="${escapeHtml(when(seconds))}">${ago(seconds)}</time>`;
  function bars(items) {
    const max = Math.max(1, ...items.map(item => item.value));
    return `<ol class="dash-bars">${items.map(item => `<li title="${escapeHtml(item.title || `${item.name}: ${item.text}`)}"><span class="dash-bar-name">${escapeHtml(item.name)}</span><span class="dash-bar-track" aria-hidden="true"><span class="dash-bar-fill" style="width:${Math.max(2, Math.round(100 * item.value / max))}%"></span></span><span class="dash-bar-value">${escapeHtml(item.text)}</span></li>`).join('')}</ol>`;
  }
  const loadingHtml = text => `<div class="dash-loading" role="status">${escapeHtml(text)}</div>`;
  const errorHtml = message => `<div class="dash-error" role="alert">${escapeHtml(message)}</div><div class="dash-footer"><button type="button" class="dash-link" data-dash="retry">Retry</button></div>`;
  const serverOnlyHtml = what => `<div class="dash-empty"><p>${escapeHtml(what)} needs the local Token Graph server, which reads the project on this computer.</p><button type="button" class="dash-link" data-dash="server-help">How to start it</button></div>`;
  function setCard(id, body, head = '', busy = false) {
    const card = $(`#dash-${id}`);
    card.querySelector('.dash-body').innerHTML = body;
    card.querySelector('.dash-head').innerHTML = head;
    card.setAttribute('aria-busy', String(busy));
  }
  function showCard(id, on) { $(`#dash-${id}`).classList.toggle('hidden', !on); }

  const documentation = { items: [], selected: '', project: null, request: 0, detail: 0 };
  function renderDocumentationList() {
    const query = $('#docs-search').value.trim().toLowerCase();
    const matches = documentation.items.map((item, index) => ({item, index})).filter(({item}) => item.path.toLowerCase().includes(query));
    $('#docs-list').innerHTML = matches.length ? matches.map(({item, index}) => `<button type="button" class="dash-link" data-doc-index="${index}" aria-current="${item.path === documentation.selected}">${escapeHtml(item.path)}<small>${escapeHtml(item.kind)}</small></button>`).join('') : '<p class="dash-note">No documents match.</p>';
  }
  async function loadDocumentation() {
    const seq = ++documentation.request, project = state.projectPath;
    documentation.project = project; documentation.items = []; documentation.selected = ''; documentation.detail++;
    $('#docs-title').textContent = 'Choose a document'; $('#docs-content').textContent = 'Select a file to read it here.'; $('#docs-list').innerHTML = '';
    if (!serverToken || !project) {
      $('#docs-status').textContent = !serverToken ? 'Open the local Token Graph server to browse project files.' : 'Choose a project to browse its documents.';
      return;
    }
    $('#docs-status').textContent = 'Finding documents…';
    try {
      const result = await api(`/api/documentation?project=${encodeURIComponent(project)}`);
      if (seq !== documentation.request || state.projectPath !== project) return;
      documentation.items = Array.isArray(result.documents) ? result.documents : [];
      $('#docs-status').textContent = documentation.items.length ? `${format(documentation.items.length)} documents found. Content is shown as plain text.` : 'No project documents or generated wiki articles found.';
      renderDocumentationList();
    } catch (error) {
      if (seq === documentation.request) $('#docs-status').textContent = `Could not load documents: ${error.message}`;
    }
  }
  async function openDocumentation(index) {
    const item = documentation.items[index], project = documentation.project, seq = ++documentation.detail;
    if (!item || !project) return;
    documentation.selected = item.path; renderDocumentationList(); $('#docs-title').textContent = item.path; $('#docs-content').textContent = 'Loading…';
    try {
      const result = await api(`/api/documentation/file?project=${encodeURIComponent(project)}&file=${encodeURIComponent(item.path)}`);
      if (seq === documentation.detail && state.projectPath === project) $('#docs-content').textContent = result.content;
    } catch (error) {
      if (seq === documentation.detail) $('#docs-content').textContent = `Could not open document: ${error.message}`;
    }
  }

  function showPage(page) {
    // One task per page. "plain" pages hide the graph workspace and the inspector.
    const pages = {
      dashboard: { title: 'Overview', crumb: 'OVERVIEW', hint: 'What this project is, how it is built and where to start reading.', section: '#dashboard', plain: true },
      graph: { hint: 'Click a node to inspect it, double-click to focus. Views and filters are in the toolbar.' },
      ask: { title: 'Ask AI', crumb: 'ASK', hint: 'Questions about the code, answered from this graph with file and line references.', section: '#ask-page' },
      health: { title: 'Health', crumb: 'HEALTH', hint: 'Issues, graph freshness, changes and recent activity.', section: '#health-page', plain: true },
      system: { title: 'System', crumb: 'SYSTEM', hint: 'Several services and how they talk to each other: NATS, Kafka, RabbitMQ, MassTransit, gRPC, HTTP.', section: '#system-page', plain: true },
      agents: { title: 'Agents', crumb: 'AGENTS', hint: 'Explore code and investigate logs with local tools.', section: '#agents-page', plain: true },
      documentation: { title: 'Documentation', crumb: 'DOCUMENTATION', hint: 'Browse project documents and generated wiki articles.', section: '#documentation-page', plain: true },
      settings: { title: 'Settings', crumb: 'SETTINGS', hint: 'Projects, local AI and the index.', section: '#settings', plain: true },
    };
    state.page = pages[page] ? page : 'dashboard';
    const info = pages[state.page], shell = $('.app-shell');
    for (const name of Object.keys(pages)) shell.classList.toggle(`page-${name}`, name === state.page);
    shell.classList.toggle('page-dashboard', Boolean(info.plain));
    for (const [name, p] of Object.entries(pages)) if (p.section) $(p.section).classList.toggle('hidden', name !== state.page);
    $('#page-title').textContent = info.title || (state.view === 'docs' ? 'Documentation map' : 'Codebase map');
    $('#view-crumb').textContent = info.crumb || (state.view === 'docs' ? 'DOCUMENTATION' : 'GRAPH');
    $('#page-hint').textContent = info.hint;
    $('#ask-page-note').classList.toggle('hidden', Boolean(serverToken));
    updateNav();
    try { localStorage.setItem('tokenGraph.page', state.page); } catch (_) { /* per-viewer convenience only */ }
    if (state.page === 'system') loadSystems();
    else if (state.page === 'agents') { loadClaudeAgents(); loadBenchModels(); if (benchmarkProject !== state.projectPath) { benchmarkRows.clear(); benchmarkProject = state.projectPath; renderBenchResults(); } }
    else if (state.page === 'documentation') loadDocumentation();
    else if (info.plain) refreshDashboard();
    else if (state.page === 'graph') { resize(); fitGraph(); }
  }

  function noteJob(job) {
    dash.job = job;
    noteAiJob(job);
    if (state.page === 'dashboard' && job && dash.data) renderDashboard(['overview', 'health']);
  }

  async function refreshDashboard() {
    if (!['dashboard', 'settings', 'health'].includes(state.page)) return;
    const seq = ++dash.seq;
    const project = state.projectPath;
    if (!serverToken || !project) { renderDashboard(); return; }
    if (dash.savingsProject !== project) dash.savingsError = '';
    dash.loading = true;
    const aiFresh = dash.ai && Date.now() - dash.aiAt < 15000;
    dash.aiLoading = !aiFresh;
    renderDashboard();
    const [summary, ai, savings] = await Promise.allSettled([
      api(`/api/dashboard?project=${encodeURIComponent(project)}`),
      aiFresh ? Promise.resolve(dash.ai) : api('/api/ai/status'),
      api(`/api/savings?project=${encodeURIComponent(project)}`),
    ]);
    if (seq !== dash.seq || project !== state.projectPath) return;  // a newer refresh or another project won
    dash.loading = false; dash.aiLoading = false;
    if (summary.status === 'fulfilled') { dash.data = summary.value; dash.dataProject = project; dash.error = ''; }
    else { dash.error = summary.reason?.message || 'The dashboard could not be loaded.'; }
    if (ai.status === 'fulfilled') { dash.ai = ai.value; dash.aiAt = Date.now(); dash.aiError = ''; }
    else { dash.aiError = ai.reason?.message || 'Could not check the local AI provider.'; }
    if (savings.status === 'fulfilled') { dash.savings = savings.value.sessions || []; dash.savingsProject = project; dash.savingsError = ''; }
    else { dash.savings = null; dash.savingsProject = project; dash.savingsError = savings.reason?.message || 'Could not read the Claude Code sessions.'; }
    renderDashboard();
  }

  // Figures available without the server: straight from the graph loaded in this tab.
  function loadedGraphFigures() {
    const files = new Set(state.nodes.map(node => String(node.source_file || '').replace(/\\/g, '/')).filter(Boolean));
    const extensions = new Map();
    for (const file of files) {
      const match = /\.([^./]+)$/.exec(file);
      const ext = match ? `.${match[1].toLowerCase()}` : '(no extension)';
      extensions.set(ext, (extensions.get(ext) || 0) + 1);
    }
    return { files: files.size, nodes: state.nodes.length, edges: state.links.length,
      communities: new Set(state.nodes.map(node => node.community).filter(c => c != null)).size,
      extensions: [...extensions].sort((a, b) => b[1] - a[1]) };
  }

  function freshness(data) {
    const job = dash.job?.state === 'running' && dash.job.project === state.projectPath ? dash.job : data?.job?.state === 'running' ? data.job : null;
    if (job) return { status: 'busy', text: 'Building…', detail: `${job.step || 'Building'} · started ${ago(job.started)}` };
    if (data?.last_build && data.last_build.ok === false) return { status: 'critical', text: 'Last build failed', detail: data.last_build.message || 'See graphify-out/token-graph.log' };
    if (!data) return null;
    const changed = (data.stale?.changed || 0) + (data.stale?.deleted || 0);
    if (changed) return { status: 'warning', text: `${format(changed)} file${changed === 1 ? '' : 's'} changed`, detail: (data.stale.examples || []).join(', '), stale: true };
    return { status: 'good', text: 'Up to date', detail: 'No source file changed since the graph was built.' };
  }

  function renderDashboard(only = null) {
    const want = id => !only || only.includes(id);
    const hasGraph = state.nodes.length > 0;
    const data = dash.dataProject === state.projectPath ? dash.data : null;
    for (const id of ['ask', 'health', 'ai', 'index', 'intel', 'architecture', 'components', 'activity', 'savings']) showCard(id, hasGraph);
    updateAiChip(); updateNav();
    $('#dash-docs').disabled = Boolean(serverToken) && !state.projectPath;  // without the server it explains how to start it
    if (!hasGraph && dash.pending) { setCard('overview', loadingHtml(`Loading the ${dash.pending} graph…`), '', true); return; }
    if (!hasGraph && dash.pendingError) { setCard('overview', `<div class="dash-error" role="alert">${escapeHtml(dash.pendingError)}</div>`); return; }
    if (!hasGraph) {
      setCard('overview', `<div class="dash-empty"><strong>No project open</strong><p>Choose a project folder. Token Graph builds its graph on this computer, then this dashboard summarizes it.</p><button type="button" class="dash-action primary" data-dash="build-first">Choose project &amp; build graph</button></div>`);
      return;
    }
    if (want('ask')) renderAskCard();
    if (want('overview')) renderOverview(data);
    if (want('components')) renderComponents();
    if (want('health')) renderHealth(data);
    if (want('ai')) renderAI();
    if (want('index')) renderIndex(data);
    if (want('intel')) renderIntel(data);
    if (want('architecture')) renderArchitecture();
    if (want('activity')) renderActivity(data);
    if (want('savings')) renderSavings();
  }

  // Tokens Claude Code saved by asking this project's graph instead of reading whole files,
  // per recent session (session_savings.py over the local transcripts; an estimate).
  const tokensText = n => Math.abs(n) >= 1000 ? `${(n / 1000).toFixed(1)}K` : format(n);
  const percentText = p => p === null || p === undefined ? '—' : `${p}%`;
  function renderSavings() {
    if (!serverToken) { setCard('savings', serverOnlyHtml('Counting saved tokens'), pill('off', 'Server off')); return; }
    if (!state.projectPath) {
      const choices = dash.savingsChoices;
      const picker = choices?.length ? `<label class="eyebrow" for="savings-project">LOCAL PROJECT</label><select class="project-select" id="savings-project">${choices.map(p => `<option value="${escapeHtml(p.path)}">${escapeHtml(p.name)} — ${escapeHtml(p.path)}</option>`).join('')}</select><button type="button" class="dash-link primary" data-dash="savings-open-project">Open project</button>` : choices ? '<p>No saved local project graphs were found. Choose a project folder in Settings first.</p><button type="button" class="dash-link" data-dash="savings-settings">Open Settings</button>' : '<button type="button" class="dash-link" data-dash="savings-list-projects">Choose local project</button>';
      setCard('savings', `<div class="dash-empty"><p>This graph is not linked to a single local project folder. Open a project from this computer to view its Claude Code savings.</p>${picker}</div>`, pill('off', 'Choose project'));
      return;
    }
    const sessions = dash.savingsProject === state.projectPath ? dash.savings : null;
    if (!sessions) { setCard('savings', dash.savingsError ? errorHtml(dash.savingsError) : loadingHtml('Reading Claude Code sessions…'), '', true); return; }
    const refresh = '<div class="dash-footer"><button type="button" class="dash-link" data-dash="savings-refresh">Refresh</button></div>';
    if (!sessions.length) {
      setCard('savings', `<div class="dash-empty"><p>No recognized Claude Code graph queries were found for this project.</p><p>To start tracking, ask Claude Code to explore this project using Token Graph, then refresh this card.</p><p class="dash-note">Checks this project's recent history and the 300 most recent Claude transcripts on this computer. Some query formats may not be recognized.</p></div>${refresh}`, pill('off', 'No sessions'));
      return;
    }
    const latest = sessions[0];
    const without = sessions.reduce((sum, s) => sum + s.without_graph_tokens, 0);
    const saved = sessions.reduce((sum, s) => sum + s.saved_tokens, 0);
    const overall = without ? Math.round(1000 * saved / without) / 10 : null;
    const status = latest.saved_percent === null ? 'off' : latest.saved_percent >= 50 ? 'good' : latest.saved_percent > 0 ? 'info' : 'warning';
    const tiles = [
      tile('Saved, latest session', tokensText(latest.saved_tokens), `${percentText(latest.saved_percent)} · ${count(latest.graph_answers, 'graph answer')}`),
      tile('Without graph', tokensText(latest.without_graph_tokens), `Estimated full-file baseline · ${count(latest.files, 'file')}`),
      tile('With graph', tokensText(latest.with_graph_tokens), `answers ${tokensText(latest.graph_tokens)} + reads ${tokensText(latest.read_tokens)}`),
      tile(`Saved, ${count(sessions.length, 'session')}`, tokensText(saved), `${percentText(overall)} of ${tokensText(without)}`),
    ].join('');
    const started = s => s.started ? new Date(s.started).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'unknown time';
    const sessionName = s => s.title || `${s.scope === 'subagent' ? 'Subagent' : 'Session'} ${s.session.slice(0, 8)}`;
    const where = s => `<p class="dash-note"><strong>${escapeHtml(sessionName(s))}</strong> · started ${escapeHtml(started(s))} · last active ${ago(s.updated)}<br>Session started in: <code>${escapeHtml(s.folder || 'unknown')}</code> · session <code>${escapeHtml(s.session.slice(0, 8))}</code>${s.last_prompt ? `<br>Last request: “${escapeHtml(s.last_prompt.slice(0, 140))}”` : ''}</p>`;
    const rows = bars(sessions.map(s => ({
      name: `${sessionName(s)} · ${ago(s.updated)}`,
      value: Math.max(0, s.saved_tokens),
      text: `${tokensText(s.saved_tokens)} · ${percentText(s.saved_percent)}`,
      title: `${sessionName(s)} — folder ${s.folder || 'unknown'}, started ${started(s)}, session ${s.session}: without graph ${format(s.without_graph_tokens)}, with graph ${format(s.with_graph_tokens)}, saved ${format(s.saved_tokens)} tokens (${percentText(s.saved_percent)}) in ${count(s.graph_answers, 'graph answer')}`,
    })));
    setCard('savings', `<p class="dash-sub">Latest session</p>${where(latest)}<p class="dash-note">Sessions can start in another folder and still query this project's graph.</p><div class="dash-tiles">${tiles}</div><p class="dash-sub">Saved per session (hover for folder and time)</p>${rows}<p class="dash-note">Estimate from the Claude Code transcripts on this computer (4 characters ≈ 1 token, no model used). Without graph: the files a graph answer pointed to and Claude then opened, read in full. With graph: the answers plus what Claude read from those files.</p>${refresh}`,
      pill(status, `${percentText(latest.saved_percent)} saved`, 'Latest session'));
  }

  function renderOverview(data) {
    const local = loadedGraphFigures();
    const graph = data?.summary?.graph;
    const name = $('#graph-selector').selectedOptions?.[0]?.textContent || data?.summary?.project || 'Project';
    const fresh = freshness(data);
    const head = fresh ? pill(fresh.status, fresh.text, fresh.detail) : serverToken ? (dash.loading ? pill('off', 'Checking…') : '') : pill('off', 'Freshness unknown', 'Start the local server to compare the graph with the source files.');
    const languages = data?.summary?.languages || null;
    const code = languages ? languages.filter(l => l.code) : null;
    const other = languages ? languages.filter(l => !l.code) : null;
    const tiles = [
      tile('Source files', format(graph?.files ?? local.files), graph ? `${format(graph.code_files)} code files` : 'in this graph'),
      tile('Graph nodes', format(graph?.nodes ?? local.nodes), 'symbols, files, docs'),
      tile('Connections', format(graph?.edges ?? local.edges), 'relationships'),
      tile('Languages', code ? format(code.length) : dash.loading ? '…' : format(local.extensions.length), code ? 'with a parser' : 'file types'),
    ].join('');
    let languageHtml;
    if (code) {
      const total = code.reduce((sum, l) => sum + l.files, 0);
      languageHtml = code.length
        ? `<p class="dash-sub">Languages detected</p>${bars(code.slice(0, 8).map(l => ({ name: l.name, value: l.files, text: `${count(l.files, 'file')} · ${share(l.files, total)}`, title: `${l.name} (${l.extensions.join(', ')}): ${format(l.files)} files, ${format(l.nodes)} nodes` })))}`
        : `<p class="dash-note">No program code was parsed in this graph.</p>`;
      if (code.length > 8) languageHtml += `<p class="dash-note">+ ${code.length - 8} more languages</p>`;
      if (other.length) languageHtml += `<p class="dash-note">Also indexed: ${other.map(l => `${escapeHtml(l.name)} ${format(l.files)}`).join(' · ')}</p>`;
    } else if (dash.loading) {
      languageHtml = loadingHtml('Detecting languages…');
    } else {
      languageHtml = `<p class="dash-sub">File types in this graph</p>${bars(local.extensions.slice(0, 8).map(([ext, n]) => ({ name: ext, value: n, text: count(n, 'file') })))}`
        + (serverToken ? '' : '<p class="dash-note">Language names come from the local server.</p>');
    }
    const path = state.projectPath ? `<code>${escapeHtml(state.projectPath)}</code>` : '';
    setCard('overview', `<div class="dash-project"><strong>${escapeHtml(name)}</strong>${path}</div><div class="dash-tiles">${tiles}</div>${dash.error ? errorHtml(dash.error) : languageHtml}`, head, dash.loading);
  }

  function renderHealth(data) {
    const local = loadedGraphFigures();
    if (!serverToken) {
      setCard('health', `<div class="dash-rows">${row('Communities', format(local.communities))}${row('Connections', format(local.edges))}</div>${serverOnlyHtml('Freshness and dependency figures')}`, pill('off', 'Server off'));
      return;
    }
    if (!data) { setCard('health', dash.error ? errorHtml(dash.error) : loadingHtml('Reading graph health…'), '', true); return; }
    const graph = data.summary.graph, deps = data.summary.dependencies, build = data.last_build;
    const fresh = freshness(data);
    const relations = Object.entries(graph.relations || {}).slice(0, 4).map(([name, count]) => `${escapeHtml(name)} ${format(count)}`).join(' · ');
    const lastBuild = build?.finished ? `${{ ai: 'AI generation', update: 'Update', build: 'Full build' }[build.mode] || 'Build'} ${timeTag(build.finished)}` : timeTag(graph.built);
    const top = deps.top_external.length ? `Most used: ${deps.top_external.map(e => `${escapeHtml(e.label)} (${format(e.uses)})`).join(', ')}` : '';
    const rows = [
      row('Indexing status', pill(fresh.status, fresh.text), escapeHtml(fresh.detail || '')),
      row('Last graph update', lastBuild, build?.finished ? '' : 'Built outside this Explorer'),
      row('Communities', format(graph.communities)),
      row('Dependencies', count(deps.edges, 'link'), `${count(deps.external_symbols, 'external symbol')} referenced${top ? `. ${top}` : ''}`),
      row('Relationships', relations || na('None')),
    ].join('');
    const footer = fresh.stale ? `<div class="dash-footer"><button type="button" class="dash-link" data-dash="update">Update graph</button></div>` : '';
    setCard('health', `<div class="dash-rows">${rows}</div>${footer}`, pill(fresh.status, fresh.status === 'good' ? 'Healthy' : fresh.text));
  }

  function renderAI() {
    if (!serverToken) { setCard('ai', serverOnlyHtml('The local AI status'), pill('off', 'Server off')); return; }
    if (!dash.ai) { setCard('ai', dash.aiError ? errorHtml(dash.aiError) : loadingHtml('Checking the local model provider…'), '', true); return; }
    const ai = dash.ai;
    const problems = (ai.problems || []).map(p => `<li>${pill('critical', 'Setup')}<div>${escapeHtml(p)}</div></li>`).join('');
    if (!ai.reachable) {
      setCard('ai', `<div class="dash-rows">${row('Provider', `${escapeHtml(ai.provider)} <code>${escapeHtml(ai.endpoint)}</code>`)}${row('Connection', pill('critical', 'Not responding'), escapeHtml(ai.error))}</div><p class="dash-note">Start the Ollama app on this computer, then retry. Graph features keep working without it.</p><div class="dash-footer"><button type="button" class="dash-link" data-dash="retry">Retry</button></div>${problems ? `<ul class="dash-issues">${problems}</ul>` : ''}`, pill('critical', 'Offline'));
      return;
    }
    const preferred = ai.chat_models.includes('qwen3:8b') ? 'qwen3:8b' : ai.chat_models[0];
    const loaded = ai.loaded.length ? ai.loaded.map(m => `${escapeHtml(m.name)} <span class="dash-na">· ${escapeHtml(m.processor)}</span>`).join(', ') : na('None loaded');
    const rows = [
      row('Provider', `${escapeHtml(ai.provider)}${ai.version ? ` ${escapeHtml(ai.version)}` : ''}`, `<code>${escapeHtml(ai.endpoint)}</code> · local only`),
      row('Connection', pill('good', 'Connected')),
      row('Active model', loaded, ai.loaded.length ? 'Loaded in memory now' : 'Ollama loads a model on the first question'),
      row('Chat models', ai.chat_models.length ? escapeHtml(ai.chat_models.join(', ')) : na('None installed'), preferred ? `Agent default: ${escapeHtml(preferred)}` : 'Install one with ollama pull, e.g. qwen3:8b'),
      row('Embedding model', ai.embedding_models.length ? escapeHtml(ai.embedding_models.join(', ')) : na('None installed'), 'Not used yet: there is no vector index'),
    ].join('');
    const status = ai.problems?.length ? pill('warning', 'Setup needed') : ai.chat_models.length ? pill('good', 'Ready') : pill('warning', 'No model');
    setCard('ai', `<div class="dash-rows">${rows}</div>${problems ? `<ul class="dash-issues">${problems}</ul>` : ''}`, status);
  }

  function renderIndex(data) {
    if (!serverToken) { setCard('index', serverOnlyHtml('The knowledge index status'), pill('off', 'Server off')); return; }
    if (!data) { setCard('index', dash.error ? errorHtml(dash.error) : loadingHtml('Reading the index…'), '', true); return; }
    const index = data.summary.index, graph = data.summary.graph, build = data.last_build;
    const vectorReason = dash.ai?.vector_index?.reason || 'Token Graph has no vector index yet; questions use graph traversal and keyword search.';
    const lastOperation = build?.finished
      ? `${escapeHtml({ ai: 'AI generation', update: 'Incremental update', build: 'Full build' }[build.mode] || 'Build')} ${timeTag(build.finished)}`
      : index.last_indexed ? `Engine scan ${timeTag(index.last_indexed)}` : na('None recorded');
    const rows = [
      row('Indexed files', format(index.manifest_files), `${format(index.ast_cache_entries)} parsed results cached for incremental updates`),
      row('Indexed symbols', format(graph.nodes), graph.ai_nodes ? `${format(graph.ai_nodes)} added by local AI` : 'All from the code parser'),
      row('Code chunks', na('Not indexed'), 'No chunk index exists; the graph indexes symbols and relationships instead'),
      row('Vector database', na('Not configured'), escapeHtml(vectorReason)),
      row('Last indexing', lastOperation),
    ].join('');
    setCard('index', `<div class="dash-rows">${rows}</div>`, pill(index.manifest_files ? 'good' : 'warning', index.manifest_files ? 'Graph index' : 'Empty'));
  }

  function renderIntel(data) {
    if (!serverToken) { setCard('intel', serverOnlyHtml('Code analysis'), pill('off', 'Server off')); return; }
    if (!data) { setCard('intel', dash.error ? errorHtml(dash.error) : loadingHtml('Analysing the graph…'), '', true); return; }
    const q = data.summary.quality, graph = data.summary.graph, build = data.last_build;
    const issues = [];
    for (const problem of data.problems || []) issues.push(['critical', 'Runtime', escapeHtml(problem)]);
    if (build && build.ok === false) issues.push(['critical', 'Build', `The last graph build failed: ${escapeHtml((build.message || '').split('\n').pop())}`]);
    const fresh = freshness(data);
    if (fresh.stale) issues.push(['warning', 'Stale', `${escapeHtml(fresh.text)} since the graph was built. Answers may miss recent code.`]);
    if (q.cycles.count) issues.push(['warning', 'Cycles', `${format(q.cycles.count)} circular import ${q.cycles.count === 1 ? 'dependency' : 'dependencies'}: ${q.cycles.items.map(c => `<code>${c.files.map(escapeHtml).join(' → ')}</code>`).join(', ')}`]);
    const warnings = build?.warnings || [];
    if (warnings.length) issues.push(['warning', 'Parser', `${format(warnings.length)} warning${warnings.length === 1 ? '' : 's'} in the last build<details class="dash-more"><summary>Show</summary><ul>${warnings.map(w => `<li>${escapeHtml(w)}</li>`).join('')}</ul></details>`]);
    const checked = ['runtime', 'last build', 'freshness', q.cycles.supported ? 'import cycles' : null].filter(Boolean).join(', ');
    const issueHtml = issues.length
      ? `<ul class="dash-issues">${issues.map(([status, label, text]) => `<li>${pill(status, label)}<div>${text}</div></li>`).join('')}</ul>`
      : `<ul class="dash-issues"><li>${pill('good', 'None')}<div>No issues detected. Checked: ${escapeHtml(checked)}.</div></li></ul>`;
    const top = q.hotspots[0];
    const indicators = [
      tile('Import cycles', q.cycles.supported ? format(q.cycles.count) : na('n/a'), q.cycles.supported ? `${format(q.cycles.import_edges)} file imports checked` : 'No file-level imports in this graph'),
      tile('Top coupling', top ? format(top.degree) : na('—'), top ? `${top.label}` : 'No hub found'),
      tile('Unconnected symbols', format(q.unconnected_symbols), 'no relationship at all'),
      tile('Test files', share(q.test_files, q.code_files), `${format(q.test_files)} of ${format(q.code_files)} code files`),
      tile('Generated files', format(q.generated_files), share(q.generated_files, q.code_files) + ' of code files'),
      tile('Inferred links', share(graph.inferred, graph.edges), `${format(graph.inferred)} resolved by inference`),
    ].join('');
    const hotspots = q.hotspots.length
      ? `<p class="dash-sub">Coupling hotspots</p>${bars(q.hotspots.slice(0, 5).map(h => ({ name: h.label, value: h.degree, text: count(h.degree, 'link'), title: `${h.label} — ${h.file || 'no file'}: ${h.degree} relationships` })))}`
      : '';
    const head = issues.some(i => i[0] === 'critical') ? pill('critical', `${issues.length} issue${issues.length === 1 ? '' : 's'}`) : issues.length ? pill('warning', `${issues.length} to review`) : pill('good', 'No issues');
    setCard('intel', `<p class="dash-sub">Detected issues</p>${issueHtml}<p class="dash-sub">Quality indicators</p><div class="dash-tiles small">${indicators}</div>${hotspots}`, head);
  }

  function renderArchitecture() { renderModuleMap(); }  // client-side, from the same modules as the Architecture view (js/06c-lenses.js)

  function renderActivity(data) {
    if (!serverToken) { setCard('activity', serverOnlyHtml('Activity history'), pill('off', 'Server off')); return; }
    if (!data) { setCard('activity', dash.error ? errorHtml(dash.error) : loadingHtml('Loading activity…'), '', true); return; }
    const items = data.activity || [];
    const list = items.length
      ? `<ol class="dash-activity">${items.slice(0, 12).map(item => `<li class="${item.ok === false ? 'failed' : ''}"><span class="icon" aria-hidden="true">${item.ok === false ? '✕' : ACTIVITY_ICON[item.kind] || '•'}</span><span class="title">${escapeHtml(item.title)}${item.detail && item.kind !== 'analysis' ? `<span class="detail">${escapeHtml(item.detail)}</span>` : ''}</span>${timeTag(item.time)}</li>`).join('')}</ol>`
      : '<div class="dash-empty"><p>No activity yet. Graph builds, questions, AI answers, code analyses and documentation exports made in this Explorer appear here.</p></div>';
    const footer = `<div class="dash-footer">${items.length ? '<button type="button" class="dash-link" data-dash="clear-activity">Clear history</button>' : ''}<span class="dash-note">Kept in this project: graphify-out/token-graph-activity.jsonl</span></div>`;
    setCard('activity', list + footer, items.length ? pill('info', `${format(items.length)} recent`) : '');
  }

  function dashStatus(html, error = false) {
    $('#dash-action-status').innerHTML = html;
    $('#dash-action-status').classList.toggle('error', error);
  }

  async function generateDocs() {
    if (!serverToken) { showServerInstructions(); return; }
    if (!state.projectPath) { dashStatus('Open a project first.', true); return; }
    const button = $('#dash-docs'), project = state.projectPath;
    button.disabled = true;
    dashStatus('Writing documentation from the graph…');
    try {
      dash.docs = { ...(await api('/api/docs', { project })), project };
      const open = state.editors.vscode ? '<button type="button" class="dash-link" data-dash="open-docs">Open in VS Code</button>' : '';
      dashStatus(`${format(dash.docs.articles)} articles written to <code>${escapeHtml(dash.docs.file)}</code>${open}`);
      refreshDashboard();
    } catch (error) {
      dashStatus(escapeHtml(error.message), true);
    } finally {
      button.disabled = false;
    }
  }

  async function onDashboardClick(event) {
    const target = event.target.closest?.('[data-dash]');
    if (!target) return;
    if (onShellAction(target)) return;
    const action = target.dataset.dash;
    if (action === 'ask') { showPage('graph'); setAskMode(serverToken ? 'agent' : 'query'); return; }
    if (action === 'explore') { showPage('graph'); return; }
    if (action === 'architecture') { showPage('graph'); setGroupBy('folder'); return; }
    if (action === 'search') { showPage('graph'); $('#search').focus(); return; }
    if (action === 'docs') { generateDocs(); return; }
    if (action === 'retry') { dash.aiAt = 0; refreshDashboard(); return; }
    if (action === 'savings-refresh') { refreshDashboard(); return; }
    if (action === 'savings-settings') { showPage('settings'); return; }
    if (action === 'savings-list-projects') {
      setCard('savings', loadingHtml('Finding local projects…'), '', true);
      try { const result = await api('/api/projects'); dash.savingsChoices = (result.projects || []).filter(p => p.has_graph); }
      catch (error) { setCard('savings', errorHtml(error.message), pill('warning', 'Unavailable')); return; }
      if (!state.projectPath) renderSavings();
      return;
    }
    if (action === 'savings-open-project') {
      const path = $('#savings-project')?.value;
      if (!path || !dash.savingsChoices?.some(p => p.path === path)) return;
      try { await openServerProject(path); }
      catch (error) { setCard('savings', errorHtml(error.message), pill('warning', 'Unavailable')); }
      return;
    }
    if (action === 'server-help') { showServerInstructions(); return; }
    if (action === 'build-first') { if (serverToken) buildProject(); else showServerInstructions(); return; }
    if (action === 'update') { if (state.projectPath) buildProject(state.projectPath); return; }
    if (action === 'open-docs' && dash.docs) {
      try { await api('/api/open', { editor: 'vscode', project: dash.docs.project, file: dash.docs.file, line: 1 }); }
      catch (error) { dashStatus(escapeHtml(error.message), true); }
      return;
    }
    if (action === 'clear-activity' && state.projectPath) {
      if (!confirm('Clear the recent activity history for this project?')) return;
      try { await api('/api/activity/clear', { project: state.projectPath }); refreshDashboard(); }
      catch (error) { dashStatus(escapeHtml(error.message), true); }
    }
  }

  function initDashboard() {
    $('#docs-search').addEventListener('input', renderDocumentationList);
    $('#docs-list').addEventListener('click', event => { const item = event.target.closest('[data-doc-index]'); if (item) openDocumentation(Number(item.dataset.docIndex)); });
    $('#docs-graph').addEventListener('click', () => { showPage('graph'); setView('docs'); updateNav(); });
    $('#agent-bench-form').addEventListener('submit', runAgentBenchmark);
    $('#bench-agent').addEventListener('change', updateBenchFields);
    $('#bench-bridge-source').addEventListener('change', updateBenchFields);
    $('#bench-no-llm').addEventListener('change', updateBenchFields);
    updateBenchFields();
    $('.agent-open-explorer').addEventListener('click', () => { showPage('ask'); setAskMode('llm'); $('#ask-a').focus(); });
    $('#claude-agents-refresh').addEventListener('click', loadClaudeAgents);
    $('#dashboard').addEventListener('click', onDashboardClick);
    $('#settings').addEventListener('click', onDashboardClick);
    $('#health-page').addEventListener('click', onDashboardClick);
    let saved = null;
    try { saved = localStorage.getItem('tokenGraph.page'); } catch (_) { /* per-viewer convenience only */ }
    showPage(saved || 'dashboard');
  }

  loadBenchmark(null);
  initDashboard();
  if (window.GRAPHIFY_DATA) switchGraph(addGraph(window.GRAPHIFY_DATA, window.GRAPHIFY_DATA.project_name || 'Project graph'));
  else if (serverToken) initServer(); else loadLocalGraph();
  if (!serverToken && 'serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1')) {
    window.addEventListener('load', () => navigator.serviceWorker.register('./sw.js').catch(error => console.warn('Offline app shell is unavailable:', error)));
  }
})();

    
