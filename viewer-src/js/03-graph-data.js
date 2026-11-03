  // What a node is, inferred from the graph (the engine stores no explicit class/interface kind).
  const KIND_NAMES = { type: 'Types (class / struct / interface)', method: 'Methods', function: 'Functions', file: 'Files', document: 'Docs', external: 'External' };
  function kindOf(node, methodTargets) {
    const label = String(node.label || '');
    const file = String(node.source_file || '').replace(/\\/g, '/');
    if (node.file_type === 'document') return 'document';
    if (node.file_type === 'concept' || node.external || node.type === 'external') return 'external';
    if (file && file.split('/').pop() === label) return 'file';
    if (methodTargets.has(node.id) || /^\..*\)$/.test(label)) return 'method';
    if (label.endsWith(')')) return 'function';
    if (node.type === 'namespace' || node.type === 'package') return 'external';
    return 'type';
  }

  function fillKindOptions() {
    const counts = new Map();
    for (const node of state.nodes) counts.set(node._kind, (counts.get(node._kind) || 0) + 1);
    if (!counts.has(state.kind)) state.kind = 'all';
    const select = $('#kind-filter');
    select.replaceChildren(new Option(`All kinds (${format(state.nodes.length)})`, 'all'));
    for (const kind of Object.keys(KIND_NAMES)) if (counts.has(kind)) select.append(new Option(`${KIND_NAMES[kind]} (${format(counts.get(kind))})`, kind));
    select.value = state.kind;
  }

  // Search results list: what matches the search box, the kind filter and the hide filters.
  function renderSearchResults() {
    const box = $('#search-results');
    if (!state.query) { box.classList.add('hidden'); box.replaceChildren(); return; }
    const q = state.query;
    const rank = node => { const label = String(node.label).toLowerCase().replace(/^\.|\(\)$/g, ''); return label === q ? 0 : label.startsWith(q) ? 1 : String(node.label).toLowerCase().includes(q) ? 2 : 3; };
    const matches = state.nodes.filter(visible).sort((a, b) => rank(a) - rank(b) || (state.adjacency.get(b.id)?.length || 0) - (state.adjacency.get(a.id)?.length || 0));
    box.replaceChildren();
    for (const node of matches.slice(0, 40)) {
      const item = document.createElement('button');
      item.className = `search-result ${state.selected?.id === node.id ? 'active' : ''}`;
      item.innerHTML = `<span class="kind">${escapeHtml(node._kind)}</span>${escapeHtml(node.label)}<small>${escapeHtml(node.source_file || '')}${node.source_location ? ':' + escapeHtml(node.source_location) : ''}</small>`;
      item.addEventListener('click', () => { select(node); centerNode(node); draw(); renderSearchResults(); });
      box.append(item);
    }
    if (!matches.length) box.innerHTML = '<div class="search-more">No matches with the current filters.</div>';
    else if (matches.length > 40) box.insertAdjacentHTML('beforeend', `<div class="search-more">${format(matches.length - 40)} more — type more of the name</div>`);
    box.classList.remove('hidden');
  }

  function hashString(value) { let hash = 2166136261; for (let i = 0; i < value.length; i++) hash = Math.imul(hash ^ value.charCodeAt(i), 16777619); return hash >>> 0; }
  function loadBenchmark(benchmark = window.GRAPHIFY_BENCHMARK) {
    if (!benchmark) {
      $('#reduction-total').textContent = 'Not included';
      $('#benchmark-detail').textContent = 'This graph file has no corpus scan data.';
      $('#benchmark-source').textContent = 'Run Token Graph benchmark locally';
      $('#benchmark-strip').title = 'Benchmark estimates are not exact tokenizer counts.';
      return;
    }
    $('#reduction-total').textContent = `${benchmark.reduction_ratio_estimate}×`;
    $('#benchmark-detail').textContent = `${format(benchmark.corpus_files)} files · ${format(benchmark.corpus_words)} words · ~${format(benchmark.corpus_tokens_estimate)} baseline`;
    const saved = benchmark.saved_tokens_per_query_estimate;
    $('#benchmark-source').textContent = `~${format(Math.abs(saved))} estimated ${saved < 0 ? 'extra tokens' : 'tokens saved'}/query`;
    $('#benchmark-strip').title = `${benchmark.method}. Values are estimates, not exact tokens for a particular model.`;
  }

  function setGraphSource(source, filename = '') {
    const original = source === 'original';
    $('#project-name').textContent = original ? 'LOCAL PROJECT' : filename.replace(/\.json$/i, '');
    $('#original-source').classList.toggle('active', original);
    $('#token-source').classList.toggle('active', !original);
    $('#graph-source-note').textContent = original
      ? 'Choose your Projects folder to find all project graphs. Files stay in this browser.'
      : `Using graph file: ${filename}. Data stays in this browser.`;
  }

  function updateMetrics(raw) {
    const nodes = (state.view === 'docs' ? state.nodes.filter(isDocument) : state.nodes).filter(passesFilters);
    const ids = new Set(nodes.map(node => node.id));
    const links = state.links.filter(link => ids.has(link.source) && ids.has(link.target));
    const groups = new Set(nodes.map(node => node._group));
    $('#node-total').textContent = format(nodes.length);
    $('#edge-total').textContent = format(links.length);
    $('#group-total').textContent = format(groups.size);
    $('#file-total').textContent = format(new Set(nodes.map(n => n.source_file).filter(Boolean)).size);
    $('.metric-card:nth-child(1) .metric-label').textContent = state.view === 'docs' ? 'DOCUMENT NODES' : 'NODES';
    $('.metric-card:nth-child(1) .metric-note').textContent = state.view === 'docs' ? 'indexed sections' : 'code + docs';
    $('.metric-card:nth-child(2) .metric-note').textContent = state.view === 'docs' ? 'doc relationships' : 'relationships';
    $('.metric-card:nth-child(3) .metric-note').textContent = state.groupBy === 'folder' ? 'source folders' : state.view === 'docs' ? 'doc clusters' : 'detected clusters';
    $('.metric-card:nth-child(4) .metric-label').textContent = state.view === 'docs' ? 'DOC FILES' : 'SOURCE FILES';
    $('#community-count').textContent = groups.size;
  }

  function renderCommunityList() {
    const list = $('#community-list');
    list.innerHTML = '';
    const all = document.createElement('button');
    all.className = `community-item ${state.activeCommunity === null ? 'active' : ''}`;
    const inScope = node => (state.view !== 'docs' || isDocument(node)) && passesFilters(node);
    const scopedNodes = state.nodes.filter(inScope);
    const scopedGroups = state.communities.map(group => ({ ...group, nodes: group.nodes.filter(inScope) })).filter(group => group.nodes.length);
    all.innerHTML = `<span class="swatch" style="background:#a2acc1"></span><span class="community-name">All ${state.view === 'docs' ? 'doc ' : ''}${state.groupBy === 'folder' ? 'folders' : 'communities'}</span><span class="community-size">${format(scopedNodes.length)}</span>`;
    all.onclick = () => { state.activeCommunity = null; renderCommunityList(); draw(); };
    list.append(all);
    for (const group of scopedGroups) {
      const button = document.createElement('button');
      button.className = `community-item ${state.activeCommunity === group.id ? 'active' : ''}`;
      button.innerHTML = `<span class="swatch" style="background:${colorFor(state.groupIndex.get(group.id))}"></span><span class="community-name" title="${escapeHtml(group.name)}">${escapeHtml(group.name)}</span><span class="community-size">${format(group.nodes.length)}</span>`;
      button.onclick = () => { state.activeCommunity = state.activeCommunity === group.id ? null : group.id; renderCommunityList(); draw(); };
      list.append(button);
    }
  }

  function escapeHtml(value) { return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function isDocument(node) { return node.file_type === 'document'; }
  function visible(node) { return (state.view !== 'docs' || isDocument(node)) && passesFilters(node) && lensNodeVisible(node) && (!state.focus || state.focus.ids.has(node.id)) && (!state.highlightOnly || !state.highlight || state.highlight.nodes.has(node.id)) && (state.activeCommunity === null || node._group === state.activeCommunity) && (!state.query || `${node.label} ${node.source_file || ''} ${node.community_name || ''}`.toLowerCase().includes(state.query)); }
  function updateEmptyState(visibleCount) {
    const empty = visibleCount === 0 && state.nodes.length > 0;
    $('#graph-empty').classList.toggle('hidden', !empty);
    if (!empty) return;
    const noDocuments = state.view === 'docs' && !state.nodes.some(isDocument);
    $('#graph-empty-title').textContent = noDocuments ? 'No documentation indexed' : 'No nodes match these filters';
    $('#graph-empty-detail').textContent = noDocuments
      ? `This graph contains ${format(state.nodes.length)} code and symbol nodes, but no document nodes. Choose All graph to explore the code.`
      : 'Try clearing the search, choosing another node kind, or showing all graph nodes.';
    $('#graph-empty-reset').textContent = noDocuments ? 'Show all graph' : 'Clear filters and show graph';
  }

  function recoverGraph() {
    const noDocuments = state.view === 'docs' && !state.nodes.some(isDocument);
    state.query = ''; $('#search').value = ''; state.kind = 'all'; $('#kind-filter').value = 'all';
    state.highlightOnly = false;
    if (!noDocuments) {
      setFilter('hideTests', 'hide-tests', false);
      setFilter('hideGenerated', 'hide-generated', false);
    }
    setView('all');
    renderSearchResults();
  }

