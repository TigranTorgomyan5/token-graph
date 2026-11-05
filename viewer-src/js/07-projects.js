  // raw may be null for a server project; switchGraph then fetches it on first view.
  function addGraph(raw, name, path = null, folderFiles = null, sourceRoot = '') {
    if (raw !== null && (!raw || !Array.isArray(raw.nodes) || !Array.isArray(raw.links ?? raw.edges))) {
      throw new Error(`${name}: expected a graph JSON with nodes and links or edges.`);
    }
    if (path) {
      for (const [existingId, entry] of loadedGraphs) {
        if (entry.path === path) { entry.raw = raw; return existingId; }
      }
    }
    const id = String(++graphSequence);
    loadedGraphs.set(id, { raw, name, path, folderFiles, sourceRoot });
    const option = document.createElement('option');
    option.value = id;
    option.textContent = name;
    $('#graph-selector').append(option);
    $('#graph-selector').disabled = false;
    return id;
  }

  async function switchGraph(id) {
    const entry = loadedGraphs.get(id);
    if (!entry) return;
    if (!entry.raw && entry.path) {
      $('#data-status').textContent = `Loading ${entry.name}…`;
      dash.pending = entry.name; dash.pendingError = '';
      if (state.page === 'dashboard') renderDashboard();
      try {
        entry.raw = await api(`/api/graph?project=${encodeURIComponent(entry.path)}`);
      } catch (error) {
        $('#data-status').textContent = `${entry.name}: ${error.message}`;
        $('#data-status').classList.add('error-state');
        dash.pendingError = `${entry.name}: ${error.message}`;
        if (state.page === 'dashboard') renderDashboard();
        return;
      } finally {
        dash.pending = null;
      }
    }
    state.projectPath = entry.path || null;
    if (state.page === 'agents') loadClaudeAgents();
    if (state.page === 'documentation') loadDocumentation();
    state.editorRootKey = `tokenGraph.vscodeRoot.${hashString(`${entry.name}|${entry.sourceRoot || ''}`)}`;
    state.localSource = entry.folderFiles ? {files: entry.folderFiles, root: entry.sourceRoot} : null;
    state.activeCommunity = null; state.selected = null; state.hovered = null;
    state.query = ''; $('#search').value = '';
    renderDetails(null);
    if (state.view === 'docs' && !entry.raw.nodes.some(isDocument)) setView('all');
    normalizeGraph(entry.raw);
    loadBenchmark(entry.raw.benchmark ?? entry.raw.graph?.benchmark ?? null);
    $('#graph-selector').value = id;
    $('#graph-source-note').textContent = `Viewing ${entry.name}. ${loadedGraphs.size} graph(s) loaded in this tab. Data stays in this browser.`;
    const project = document.querySelector('#project-name');
    if (project) project.textContent = entry.name;
    $('#diff-button').classList.remove('hidden');
    checkStale();
    refreshDashboard();
  }

  async function importProjectFolder(files) {
    const ignored = new Set(['node_modules', '.git', '.venv', 'vendor', '.next', 'bin', 'obj']);
    const byPath = new Map(Array.from(files).map(file => [file.webkitRelativePath, file]));
    const graphs = [...byPath].filter(([path]) => {
      const parts = path.split('/');
      return parts.slice(-2).join('/') === 'graphify-out/graph.json' && !parts.some(part => ignored.has(part));
    }).sort(([a], [b]) => a.localeCompare(b));
    if (!graphs.length) {
      $('#data-status').textContent = 'No graphify-out/graph.json files found in this folder.';
      return;
    }
    let firstId;
    const errors = [];
    const entries = [];
    for (const [path, file] of graphs) {
      const project = path.split('/').slice(1, -2).join('/') || path.split('/')[0];
      try {
        const raw = JSON.parse(await file.text());
        if (!Array.isArray(raw.nodes) || !Array.isArray(raw.links ?? raw.edges)) throw new Error('Invalid graph format');
        const benchmarkFile = byPath.get(path.replace(/graph\.json$/, 'benchmark.json'));
        if (!raw.benchmark && !raw.graph?.benchmark && benchmarkFile) {
          try { raw.benchmark = JSON.parse(await benchmarkFile.text()); } catch (_) { /* Graph remains usable without benchmark. */ }
        }
        entries.push({ raw, project, sourceRoot: path.slice(0, -'graphify-out/graph.json'.length) });
      } catch (error) { errors.push(`${project}: ${error.message}`); }
    }
    if (entries.length) {
      loadedGraphs.clear();
      $('#graph-selector').replaceChildren();
      for (const entry of entries) {
        const id = addGraph(entry.raw, entry.project, null, byPath, entry.sourceRoot);
        firstId ||= id;
      }
      switchGraph(firstId);
    }
    if (errors.length) {
      $('#data-status').textContent = `${entries.length} projects loaded. ${errors.join(' ')}`;
      $('#data-status').classList.add('error-state');
    }
  }
  $('#projects-folder').addEventListener('change', async event => {
    $('#data-status').textContent = 'Finding project graphs…';
    await importProjectFolder(event.target.files || []);
    event.target.value = '';
  });

  $('#graph-selector').addEventListener('change', event => switchGraph(event.target.value));
  $('#graph-file').addEventListener('change', async event => {
    let lastId;
    const errors = [];
    for (const file of Array.from(event.target.files || [])) {
      try {
        const raw = JSON.parse(await file.text());
        const title = raw.project_name || raw.graph?.project_name || file.name;
        lastId = addGraph(raw, `${title} (${graphSequence + 1})`);
      } catch (error) { errors.push(`${file.name}: ${error.message}`); }
    }
    if (lastId) switchGraph(lastId);
    if (errors.length) {
      $('#data-status').textContent = errors.join(' ');
      $('#data-status').classList.add('error-state');
    }
    event.target.value = '';
  });

  $('#reset-view').addEventListener('click', () => { state.yaw=.45; state.pitch=-.25; state.focus = null; $('#focus-bar').classList.add('hidden'); state.activeCommunity = null; state.query = ''; $('#search').value = ''; renderCommunityList(); fitGraph(); });
  canvas.addEventListener('dblclick', event => { const node = findNode(event); if (node) enterFocus(node, state.focus?.depth || (state.lens === 'flow' ? 3 : 1)); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && state.focus && document.activeElement !== $('#search')) exitFocus(); });
  $('#focus-1').addEventListener('click', () => state.focus && enterFocus(state.nodeById.get(state.focus.id), 1));
  $('#focus-2').addEventListener('click', () => state.focus && enterFocus(state.nodeById.get(state.focus.id), 2));
  $('#focus-exit').addEventListener('click', exitFocus);
  $('#hide-tests').setAttribute('aria-pressed', String(state.hideTests));
  $('#hide-generated').setAttribute('aria-pressed', String(state.hideGenerated));
  $('#hide-tests').addEventListener('click', () => setFilter('hideTests', '#hide-tests', !state.hideTests));
  $('#hide-generated').addEventListener('click', () => setFilter('hideGenerated', '#hide-generated', !state.hideGenerated));
  $('#graph-help-toggle').addEventListener('click', () => { const help = $('#graph-help'); const open = help.classList.contains('hidden'); help.classList.toggle('hidden', !open); $('#graph-help-toggle').setAttribute('aria-expanded', String(open)); });
  $('#graph-help-close').addEventListener('click', () => { $('#graph-help').classList.add('hidden'); $('#graph-help-toggle').setAttribute('aria-expanded', 'false'); });
  $('#all-view').addEventListener('click', () => setView('all'));
  $('#docs-view').addEventListener('click', () => setView('docs'));
  window.addEventListener('resize', () => { resize(); fitGraph(); });
