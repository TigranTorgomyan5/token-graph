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
    const dirs = state.nodes.map(node => String(node.source_file || '').replace(/\\/g, '/').replace(/^\.\//, '').split('/').slice(0, -1));
    let depth = 1;
    for (let d = 1; d <= 6; d++) {
      const count = new Set(dirs.filter(parts => parts.length).map(parts => parts.slice(0, d).join('/'))).size;
      if (count > 40 && d > 1) break;
      depth = d;
      if (count >= 3) break;
    }
    const modules = new Map();
    state.nodes.forEach((node, i) => {
      const id = !node.source_file ? '(external / unresolved)' : dirs[i].slice(0, depth).join('/') || '(project root)';
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
  function setLens(lens) {
    if (state.view === 'docs' && lens !== 'full') setView('all');
    state.lens = lens;
    state.focus = null; $('#focus-bar').classList.add('hidden');
    state.expanded = new Set(); state.selectedCluster = null;
    state.focusDir = lens === 'flow' ? 'out' : 'both';
    computeLensNodes();
    updateLensButtons();
    if (state.selected && !visible(state.selected)) select(null); else renderDetails(state.selected);
    renderCommunityList();
    fitGraph();
    updateNav();
  }
  document.querySelectorAll('[data-lens]').forEach(b => b.addEventListener('click', () => setLens(b.dataset.lens)));
