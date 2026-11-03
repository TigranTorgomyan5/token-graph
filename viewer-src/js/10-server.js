  // ---- server mode: pick a project folder, build/update its graph, open it ----
  const buildButton = $('#build-project');
  let polling = null;

  function setBuilding(text) {
    buildButton.disabled = Boolean(text);
    $('#agent-build').disabled = Boolean(text);
    buildButton.textContent = text || 'Choose project & build graph';
  }

  async function openServerProject(path, name) {
    const raw = await api(`/api/graph?project=${encodeURIComponent(path)}`);
    await switchGraph(addGraph(raw, name || raw.project_name || path, path));
  }

  function watchJob() {
    clearInterval(polling);
    polling = setInterval(async () => {
      let job;
      try { ({ job } = await api('/api/status')); } catch (error) { return; }
      if (!job) return;
      noteJob(job);
      const name = job.project.split(/[\\/]/).pop();
      const seconds = Math.round(((job.finished || Date.now() / 1000) - job.started));
      if (job.state === 'running') {
        setBuilding(`Building ${name}… ${seconds}s`);
        $('#data-status').classList.remove('error-state');
        $('#data-status').textContent = `${name}: ${job.step} (${seconds}s)`;
        if (!state.nodes.length) { $('#loading').classList.remove('hidden'); $('#loading').textContent = `Building the graph for ${name}… ${job.step}, ${seconds}s`; }
        return;
      }
      clearInterval(polling);
      setBuilding('');
      if (job.state === 'error') {
        $('#data-status').textContent = `${name}: graph build failed. ${job.message}`;
        $('#data-status').classList.add('error-state');
        if (!state.nodes.length) $('#loading').textContent = `Graph build failed for ${name}. See graphify-out/token-graph.log in the project.`;
        refreshDashboard();
        return;
      }
      try {
        await openServerProject(job.project, name);
        $('#data-status').textContent = `${name}: graph ${job.mode === 'update' ? 'updated' : 'built'} in ${seconds}s · ${format(state.nodes.length)} nodes`;
      } catch (error) {
        $('#data-status').textContent = `${name}: ${error.message}`;
        $('#data-status').classList.add('error-state');
      }
    }, 1000);
  }

  async function buildProject(path) {
    try {
      if (!path) {
        setBuilding('Choose a folder in the dialog…');
        const picked = await api('/api/pick', { start: state.projectPath || '' });
        if (picked.manual) {
          setBuilding('');
          $('#project-path-form').classList.remove('hidden');
          $('#project-path').value = state.projectPath || '';
          $('#data-status').classList.remove('error-state');
          $('#data-status').textContent = 'Enter the full path to your project folder.';
          $('#project-path').focus();
          return;
        }
        path = picked.path;
        if (!path) { setBuilding(''); $('#data-status').textContent = 'No folder chosen.'; return; }
      }
      await api('/api/build', { project: path });
      watchJob();
    } catch (error) {
      setBuilding('');
      $('#data-status').textContent = error.message;
      $('#data-status').classList.add('error-state');
    }
  }

  async function stopServer() {
    if (!confirm('Stop the Token Graph server?\n\nThe graph on screen stays visible; building and switching projects stop working until you reopen the desktop shortcut.')) return;
    try {
      await api('/api/shutdown', {});
    } catch (error) {
      $('#data-status').textContent = error.message;
      $('#data-status').classList.add('error-state');
      return;
    }
    clearInterval(polling);
    buildButton.disabled = true;
    $('#stop-server').disabled = true;
    $('#original-source').disabled = true;
    $('#stop-server').textContent = 'Server stopped';
    $('#data-status').classList.remove('error-state');
    $('#data-status').textContent = 'Server stopped. Open the desktop shortcut to start it again.';
  }

  buildButton.classList.remove('hidden');
  $('#ask-panel').classList.remove('hidden');
  if (!serverToken) {
    buildButton.title = 'Show instructions for the local server needed to build or update graphs';
    $('#original-source').textContent = 'Update current graph';
    $('#ask-hint').textContent = 'Browser results use node-name matching and graph traversal; verify source code.';
  }
  buildButton.addEventListener('click', () => serverToken ? buildProject() : showServerInstructions());
  $('#agent-build').addEventListener('click', async () => {
    if (!serverToken) { showServerInstructions(); return; }
    if (!state.projectPath) { $('#ask-hint').textContent = 'Open a project first.'; return; }
    buildModelPickerOpen = true; $('#agent-model').classList.remove('hidden');
    if (!$('#agent-model').value) { await checkAgent(); if (!$('#agent-model').value) return; }
    setBuilding('Starting AI graph…');
    try {
      await api('/api/agent/build', {project: state.projectPath, model: $('#agent-model').value});
      $('#ask-hint').textContent = 'Generating the graph locally. Check the build status in the sidebar.';
      watchJob();
    } catch (error) { setBuilding(''); $('#ask-hint').textContent = error.message; }
  });
  $('#project-path-form').addEventListener('submit', event => {
    event.preventDefault();
    const path = $('#project-path').value.trim();
    if (!path) return;
    $('#project-path-form').classList.add('hidden');
    buildProject(path);
  });
  $('#project-path-cancel').addEventListener('click', () => {
    $('#project-path-form').classList.add('hidden');
    $('#data-status').textContent = 'No folder chosen.';
  });
  $('#server-setup-close').addEventListener('click', () => $('#server-setup').classList.add('hidden'));

  async function initServer() {
    $('#stop-server').classList.remove('hidden');
    $('#stop-server').addEventListener('click', stopServer);
    setInterval(checkStale, 60000);
    $('#original-source').textContent = 'Update current graph';
    $('#original-source').title = 'Re-scan the current project; only changed files are re-parsed';
    $('#graph-source-note').textContent = 'Choose a project folder: its graph is built or updated, then opened here. Everything stays on this computer.';
    dash.pending = 'project'; renderDashboard();
    try {
      const [{ projects }, { job }, editors] = await Promise.all([api('/api/projects'), api('/api/status'), api('/api/editors').catch(() => state.editors)]);
      state.editors = editors;
      let firstId = null;
      for (const project of projects.filter(p => p.has_graph)) {
        const id = addGraph(null, project.name, project.path);
        firstId ||= id;
      }
      if (job && job.state === 'running') watchJob();
      if (firstId) { await switchGraph(firstId); return; }
      dash.pending = null; renderDashboard();
      $('#loading').textContent = 'Click “Choose project & build graph” in the sidebar to build your first project graph.';
      $('#data-status').textContent = 'No project graphs yet';
    } catch (error) {
      dash.pending = null; dash.pendingError = `The Token Graph server is not answering (${error.message}). Reopen the desktop shortcut.`; renderDashboard();
      $('#loading').textContent = `Token Graph server is not answering (${error.message}). Reopen the desktop shortcut.`;
      $('#data-status').textContent = 'Server unavailable';
      $('#data-status').classList.add('error-state');
    }
  }

  async function loadLocalGraph() {
    if (serverToken) {
      if (state.projectPath) return buildProject(state.projectPath);
      return;
    }
    try {
      if (location.protocol === 'file:') {
        $('#loading').textContent = 'Choose your Projects folder in the sidebar to explore all project graphs.';
        $('#data-status').textContent = 'Choose a Projects folder or graph JSON files';
        return;
      }
      const response = await fetch('./graphify-out/graph.json', { cache: 'no-store' });
      if (!response.ok) throw new Error('Local graph unavailable');
      const graph = await response.json();
      switchGraph(addGraph(graph, 'Local project'));
      $('#original-source').classList.add('active');
    } catch (error) {
      $('#loading').textContent = 'Could not load graphify-out/graph.json. Choose a graph JSON file.';
      $('#data-status').textContent = 'Graph not found';
    }
  }
