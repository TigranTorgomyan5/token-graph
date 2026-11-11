  // Graph lenses (Explorer 2.0): which relationships the graph shows.
  //   full          everything
  //   architecture  modules (top source folders) as bubbles, with directed dependency/call links between them
  //   dependency    imports, references, inheritance … ; nodes without such links are hidden
  //   flow          calls and dispatches; double-click a method to trace what it calls (3 steps, outgoing)
  // Relation names come from the extractors. Unknown names are classified by pattern, so new languages work too.
  function relationKind(relation) {
    const r = String(relation || '').toLowerCase();
    if (/^(contains|defines|method|declares|member|has_|nested|part_of)/.test(r)) return 'structure';
    if (/call|dispatch|invoke|trigger|emit|publish|send|handle/.test(r)) return 'flow';
    return 'dependency';
  }
  function lensAllows(relation) {
    if (state.lens === 'full') return true;
    const kind = relationKind(relation);
    return state.lens === 'flow' ? kind === 'flow' : state.lens === 'dependency' ? kind === 'dependency' : kind !== 'structure';
  }
  function lensNodeVisible(node) { return state.lens === 'full' || state.lens === 'architecture' || state.lensNodes.has(node.id); }
  function computeLensNodes() {
    state.lensNodes = new Set();
    if (state.lens === 'full' || state.lens === 'architecture') return;
    for (const link of state.links) if (lensAllows(link.relation)) { state.lensNodes.add(link.source); state.lensNodes.add(link.target); }
  }
  // Modules: the shallowest folder depth that splits the project into at least 3 parts (at most 40).
  // Path-based only, so it does not depend on a language's namespace or package rules.
  function buildModules() {
    const paths = state.nodes.map(node => String(node.source_file || '').replace(/\\/g, '/').replace(/^\.\//, ''));
    const dirs = paths.map(path => path.split('/').slice(0, -1));
    const codeFiles = new Set(paths.filter(path => /\.(go|cs|fs|java|kt|py|js|jsx|ts|tsx|rs|rb|php|cpp|c|h|swift)$/i.test(path)));
    const goFiles = [...codeFiles].filter(path => /\.go$/i.test(path));
    const goPackages = goFiles.length > 0 && goFiles.length * 2 >= codeFiles.size;
    let depth = 1;
    for (let d = 1; d <= 6; d++) {
      const count = new Set(dirs.filter(parts => parts.length).map(parts => parts.slice(0, d).join('/'))).size;
      if (count > 40 && d > 1) break;
      depth = d;
      if (count >= 3) break;
    }
    const modules = new Map();
    state.nodes.forEach((node, i) => {
      const parts = dirs[i];
      const goDepth = goPackages && ['internal', 'cmd', 'pkg'].includes(parts[0]) && parts.length > 1 ? 2 : 1;
      const id = !node.source_file ? '(external / unresolved)' : parts.slice(0, goPackages ? goDepth : depth).join('/') || '(project root)';
      node._module = id;
      if (!modules.has(id)) modules.set(id, { id, name: id, nodes: [] });
      modules.get(id).nodes.push(node);
    });
    state.modules = [...modules.values()].sort((a, b) => b.nodes.length - a.nodes.length);
    state.moduleIndex = new Map(state.modules.map((m, i) => [m.id, i]));
    const n = state.modules.length, ring = 140 + n * 28;
    state.modules.forEach((m, i) => {
      const angle = i / Math.max(1, n) * Math.PI * 2;
      m.x = Math.cos(angle) * ring; m.y = Math.sin(angle) * ring;
      const sy = n === 1 ? 0 : 1 - 2 * (i + .5) / n, r = Math.sqrt(Math.max(0, 1 - sy * sy)), a = i * Math.PI * (3 - Math.sqrt(5));
      m._x3 = Math.cos(a) * r * ring; m._y3 = sy * ring; m._z3 = Math.sin(a) * r * ring;
    });
  }
  // How nodes are grouped into bubbles: modules in the architecture lens, otherwise the sidebar grouping.
  function grouping() {
    return state.lens === 'architecture'
      ? { key: node => node._module, groups: state.modules, index: state.moduleIndex, word: 'modules', title: 'MODULE' }
      : { key: node => node._group, groups: state.communities, index: state.groupIndex, word: 'clusters', title: 'CLUSTER' };
  }
  function arrowHead(from, to, gap, size) {
    const dx = to.x - from.x, dy = to.y - from.y, len = Math.hypot(dx, dy);
    if (len < gap + size) return;
    const ux = dx / len, uy = dy / len, tx = to.x - ux * gap, ty = to.y - uy * gap;
    ctx.beginPath(); ctx.moveTo(tx, ty);
    ctx.lineTo(tx - ux * size - uy * size * .5, ty - uy * size + ux * size * .5);
    ctx.lineTo(tx - ux * size + uy * size * .5, ty - uy * size - ux * size * .5);
    ctx.closePath(); ctx.fill();
  }
  function updateLensButtons() {
    document.querySelectorAll('[data-lens]').forEach(b => { const on = b.dataset.lens === state.lens; b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); });
  }
  let architectureCardsSelected = false;
  function updateArchitectureDisplay() {
    const isArchitecture = state.lens === 'architecture';
    const cards = isArchitecture && architectureCardsSelected;
    $('#architecture-switch').classList.toggle('hidden', !isArchitecture);
    $('#architecture-card-view').classList.toggle('hidden', !cards);
    $('#canvas-wrap').classList.toggle('hidden', cards);
    $('#architecture-network').setAttribute('aria-pressed', String(!architectureCardsSelected));
    $('#architecture-cards').setAttribute('aria-pressed', String(architectureCardsSelected));
    $('#architecture-network').classList.toggle('active', !architectureCardsSelected);
    $('#architecture-cards').classList.toggle('active', architectureCardsSelected);
    if (!cards) return;
    const groups = state.modules.filter(m => m.id !== '(external / unresolved)' && m.nodes.some(passesFilters));
    const shown = groups.slice(0, 40), ids = new Set(shown.map(m => m.id));
    const links = moduleLinks().filter(l => ids.has(l.from) && ids.has(l.to));
    const width = 1000, cols = 3, cardW = 240, cardH = 80, gapX = 115, gapY = 62, rows = Math.ceil(shown.length / cols);
    const pos = new Map(shown.map((m, i) => [m.id, {x: 30 + (i % cols) * (cardW + gapX), y: 25 + Math.floor(i / cols) * (cardH + gapY)}]));
    const edges = links.map(l => { const a = pos.get(l.from), b = pos.get(l.to), dx = b.x - a.x, dy = b.y - a.y, scale = Math.min(dx ? (cardW / 2 + 4) / Math.abs(dx) : Infinity, dy ? (cardH / 2 + 4) / Math.abs(dy) : Infinity); return `<path d="M ${a.x + cardW / 2 + dx * scale} ${a.y + cardH / 2 + dy * scale} L ${b.x + cardW / 2 - dx * scale} ${b.y + cardH / 2 - dy * scale}" marker-end="url(#arch-arrow)"><title>${escapeHtml(`${l.from} → ${l.to}: ${l.count} detected links`)}</title></path>`; }).join('');
    const boxes = shown.map(m => { const p = pos.get(m.id), files = new Set(m.nodes.filter(passesFilters).map(n => n.source_file).filter(Boolean)).size; return `<g class="architecture-box" role="button" tabindex="0" data-architecture-module="${escapeHtml(m.id)}" aria-label="${escapeHtml(`${m.name}, ${files} files. Open details`)}" transform="translate(${p.x} ${p.y})"><rect width="${cardW}" height="${cardH}" rx="10"></rect><text x="14" y="30">${escapeHtml(m.name.length > 29 ? m.name.slice(0, 26) + '…' : m.name)}</text><text class="architecture-box-note" x="14" y="55">${files} files · ${m.nodes.filter(passesFilters).length} symbols</text><title>${escapeHtml(m.name)}</title></g>`; }).join('');
    $('#architecture-card-map').innerHTML = shown.length ? `${groups.length > shown.length ? `<p class="dash-note">Showing ${shown.length} of ${groups.length} groups.</p>` : ''}<svg viewBox="0 0 ${width} ${Math.max(130, rows * (cardH + gapY))}" role="group" aria-label="Architecture folder groups and relationships"><defs><marker id="arch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10z" fill="#aaa0ff"></path></marker></defs><g class="architecture-edges">${edges}</g>${boxes}</svg>` : '<p class="dash-note">No folder groups match the current filters.</p>';
  }
  $('#architecture-network').addEventListener('click', () => { architectureCardsSelected = false; updateArchitectureDisplay(); resize(); });
  $('#architecture-cards').addEventListener('click', () => { architectureCardsSelected = true; updateArchitectureDisplay(); });
  function openArchitectureCard(event) {
    const target = event.target.closest?.('[data-architecture-module]');
    if (!target) return;
    const group = state.modules[state.moduleIndex.get(target.dataset.architectureModule)];
    showClusterDetails(group);
  }
  $('#architecture-card-map').addEventListener('click', openArchitectureCard);
  $('#architecture-card-map').addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openArchitectureCard(event); } });
  function setLens(lens) {
    if (state.view === 'docs' && lens !== 'full') setView('all');
    state.lens = lens;
    state.focus = null; $('#focus-bar').classList.add('hidden');
    state.expanded = new Set(); state.selectedCluster = null;
    state.focusDir = lens === 'flow' ? 'out' : 'both';
    computeLensNodes();
    updateArchitectureDisplay();
    updateLensButtons();
    if (state.selected && !visible(state.selected)) select(null); else renderDetails(state.selected);
    renderCommunityList();
    fitGraph();
    updateNav();
  }
  document.querySelectorAll('[data-lens]').forEach(b => b.addEventListener('click', () => setLens(b.dataset.lens)));
