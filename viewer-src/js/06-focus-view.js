  // Focus mode: only a node and its neighbours up to `depth` steps (filters still apply).
  function enterFocus(node, depth = 1, dir = state.focusDir) {
    if (!node) return;
    if (node._cluster) { expandCluster(node._cluster.id); return; }  // double-click on a cluster bubble
    const edges = dir === 'out' ? state.outgoing : dir === 'in' ? state.incoming : state.adjacency;
    const ids = new Set([node.id]);
    let frontier = [node.id];
    for (let step = 0; step < depth; step++) {
      const next = [];
      for (const id of frontier) for (const edge of edges.get(id) || []) if (!ids.has(edge.nodeId) && lensAllows(edge.relation)) { ids.add(edge.nodeId); next.push(edge.nodeId); }
      frontier = next;
    }
    state.focus = { id: node.id, depth, dir, ids }; state.focusDir = dir;
    state.activeCommunity = null;
    state.query = ''; $('#search').value = '';
    $('#focus-bar').classList.remove('hidden');
    $('#focus-name').textContent = node.label;
    $('#focus-1').setAttribute('aria-pressed', String(depth === 1));
    $('#focus-2').setAttribute('aria-pressed', String(depth === 2));
    $('#focus-3').setAttribute('aria-pressed', String(depth === 3));
    for (const d of ['both', 'in', 'out']) $(`#focus-dir-${d}`).setAttribute('aria-pressed', String(dir === d));
    renderCommunityList();
    select(node);
    fitGraph();
  }
  function exitFocus() {
    if (!state.focus) return;
    state.focus = null;
    $('#focus-bar').classList.add('hidden');
    renderCommunityList();
    if (state.selected) renderDetails(state.selected);
    fitGraph();
  }
  function setFilter(key, buttonId, on) {
    state[key] = on;
    store(`tokenGraph.${key}`, on);
    $(buttonId).setAttribute('aria-pressed', String(on));
    $('#ask-hint').textContent = answerHint();
    if (state.selected && !visible(state.selected)) select(null);
    updateMetrics({});
    renderCommunityList();
    fitGraph();
    renderSearchResults();
    updateArchitectureDisplay();
  }

  function setView(view) {
    state.view = view;
    if (view === 'docs' && state.lens !== 'full') { state.lens = 'full'; computeLensNodes(); updateLensButtons(); }
    state.focus = null;
    $('#focus-bar').classList.add('hidden');
    state.activeCommunity = null;
    state.selected = null;
    renderDetails(null);
    $('#all-view').classList.toggle('active', view === 'all');
    $('#all-view').setAttribute('aria-pressed', String(view === 'all'));
    $('#docs-view').classList.toggle('active', view === 'docs');
    $('#docs-view').setAttribute('aria-pressed', String(view === 'docs'));
    if (state.page === 'graph') {
      $('#page-title').textContent = view === 'docs' ? 'Documentation map' : 'Codebase map';
      $('#view-crumb').textContent = view === 'docs' ? 'DOCUMENTATION' : 'GRAPH';
    }
    $('#graph-title').textContent = view === 'docs' ? 'Documentation relationships' : 'Relationship graph';
    $('.legend-code').classList.toggle('hidden', view === 'docs');
    updateArchitectureDisplay();
    updateMetrics({});
    renderCommunityList();
    fitGraph();
  }

  canvas.addEventListener('contextmenu', event => event.preventDefault());
  canvas.addEventListener('pointerdown', event => {
    state.dragging=true; state.moved=false; state.startX=state.lastX=event.clientX; state.startY=state.lastY=event.clientY;
    state.dragMode=state.is3d && !event.shiftKey && event.button!==2 ? 'orbit' : 'pan';
    canvas.classList.add('dragging'); canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', event => {
    if (state.dragging) {
      const dx=event.clientX-state.lastX,dy=event.clientY-state.lastY;
      if(Math.hypot(event.clientX-state.startX,event.clientY-state.startY)>3) state.moved=true;
      if(state.dragMode==='orbit') {state.yaw+=dx*.007;state.pitch=Math.max(-1.5,Math.min(1.5,state.pitch+dy*.007));}
      else {state.x+=dx;state.y+=dy;}
      state.lastX=event.clientX;state.lastY=event.clientY; draw();
    } else {state.hovered=findNode(event);canvas.style.cursor=state.hovered?'pointer':'grab';draw();}
  });
  canvas.addEventListener('pointerup', event => {state.dragging=false;canvas.classList.remove('dragging');if(!state.moved && event.button===0)select(findNode(event));});
  canvas.addEventListener('pointercancel', () => {state.dragging=false;canvas.classList.remove('dragging');});
  $('#graph-empty-reset').addEventListener('click', recoverGraph);
  $('#mode-3d').addEventListener('click', () => setDimension(true));
  $('#mode-2d').addEventListener('click', () => setDimension(false));
  setDimension(state.is3d);
  canvas.addEventListener('wheel', event => { event.preventDefault(); const rect = canvas.getBoundingClientRect(); const beforeX = event.clientX - rect.left - rect.width / 2 - state.x, beforeY = event.clientY - rect.top - rect.height / 2 - state.y; const next = Math.max(.12, Math.min(5, state.scale * Math.exp(-event.deltaY * .001))); state.x -= beforeX / state.scale * (next - state.scale); state.y -= beforeY / state.scale * (next - state.scale); state.scale = next; draw(); }, { passive: false });
  $('#search').addEventListener('input', event => { state.query = event.target.value.trim().toLowerCase(); draw(); renderSearchResults(); });
  $('#kind-filter').addEventListener('change', event => {
    state.kind = event.target.value;
    if (state.selected && !visible(state.selected)) select(null);
    updateMetrics({}); renderCommunityList(); fitGraph(); renderSearchResults(); updateArchitectureDisplay();
  });
  document.querySelectorAll('[data-group-by]').forEach(b => {
    b.setAttribute('aria-pressed', String(b.dataset.groupBy === state.groupBy));
    b.addEventListener('click', () => setGroupBy(b.dataset.groupBy));
  });
  $('#search').addEventListener('keydown', event => { if (event.key === 'Enter') { const node = state.nodes.find(n => visible(n)); if (node) { select(node); centerNode(node); draw(); } } if (event.key === 'Escape') { event.target.value = ''; state.query = ''; draw(); } });
  document.addEventListener('keydown', event => { if (event.key === '/' && !['INPUT','TEXTAREA'].includes(document.activeElement.tagName)) { event.preventDefault(); if (state.page === 'dashboard') showPage('graph'); $('#search').focus(); } });
  $('#original-source').addEventListener('click', () => serverToken ? loadLocalGraph() : showServerInstructions());
  const loadedGraphs = new Map();
  let graphSequence = 0;

