  // Cluster view (Explorer 2.0): on graphs above state.clusterThreshold nodes, every group that is
  // not expanded is drawn as one bubble, and links between groups are merged into one weighted line.
  // It only applies to the plain overview: focus, search, ask highlights, a picked community and the
  // docs view always show real nodes. Selecting a node (search result, inspector link) expands its group.
  function clustersActive() {
    return (state.lens === 'architecture' || (state.clusterMode && state.nodes.length > state.clusterThreshold)) && state.view !== 'docs' && !state.focus && !state.query && !state.highlight && state.activeCommunity === null;
  }
  function clusterRadius(bubble) { return 6 + Math.sqrt(bubble.size) * 2.6; }
  function clusterModel() {
    const key = [state.clusterVersion, state.lens, state.hideTests, state.hideGenerated, state.kind, state.groupBy, [...state.expanded].join('\u0001')].join('|');
    if (state.clusterCache?.key === key) return state.clusterCache;
    const g = grouping(), fixed = state.lens === 'architecture';  // modules have their own ring layout
    const bubbles = new Map(), shown = [], owner = new Map();
    for (const node of state.nodes) {
      if (!passesFilters(node) || !lensNodeVisible(node)) continue;
      const gid = g.key(node), group = g.groups[g.index.get(gid)];
      if (state.expanded.has(gid)) {
        if (fixed) {  // place the module's nodes around its bubble
          const h = hashString(node.id), spread = 30 + Math.sqrt(group.nodes.length) * 9, la = h % 6283 / 1000, rad = Math.sqrt((h >>> 4) % 10000) / 100 * spread, lz = ((h >>> 12) % 2000) / 1000 - 1, lr = Math.sqrt(Math.max(0, 1 - lz * lz));
          shown.push({ ...node, x: group.x + Math.cos(la) * rad, y: group.y + Math.sin(la) * rad, _x3: group._x3 + Math.cos(la) * lr * rad, _y3: group._y3 + lz * rad, _z3: group._z3 + Math.sin(la) * lr * rad });
        } else shown.push(node);
        owner.set(node.id, node.id); continue;
      }
      const id = `cluster:${gid}`;
      let bubble = bubbles.get(id);
      if (!bubble) {
        bubble = { id, _cluster: group, label: group?.name || gid, size: 0, x: 0, y: 0, _x3: 0, _y3: 0, _z3: 0 };
        bubbles.set(id, bubble);
      }
      bubble.size++; bubble.x += node.x; bubble.y += node.y; bubble._x3 += node._x3 || 0; bubble._y3 += node._y3 || 0; bubble._z3 += node._z3 || 0;
      owner.set(node.id, id);
    }
    for (const b of bubbles.values()) {
      if (fixed) Object.assign(b, { x: b._cluster.x, y: b._cluster.y, _x3: b._cluster._x3, _y3: b._cluster._y3, _z3: b._cluster._z3 });
      else { b.x /= b.size; b.y /= b.size; b._x3 /= b.size; b._y3 /= b.size; b._z3 /= b.size; }
    }
    const directed = state.lens !== 'full';
    const edges = new Map();
    for (const link of state.links) {
      if (!lensAllows(link.relation)) continue;
      const a = owner.get(link._src ?? link.source), b = owner.get(link._tgt ?? link.target);
      if (!a || !b || a === b || (!bubbles.has(a) && !bubbles.has(b))) continue;  // node–node links are drawn one by one
      const k = directed || a < b ? `${a}\u0000${b}` : `${b}\u0000${a}`;
      const edge = edges.get(k);
      if (edge) { edge.count++; if (link.tech) edge.tech = link.tech; } else edges.set(k, { a, b, count: 1, tech: link.tech || null });
    }
    const list = [...bubbles.values()];
    state.clusterCache = { key, bubbles: list, shown, shownIds: new Set(shown.map(n => n.id)), edges: [...edges.values()],
 };
    return state.clusterCache;
  }
  function drawClusters(width, height) {
    const model = clusterModel();
    const items = [...model.bubbles, ...model.shown].map(projectNode).sort((a, b) => a.depth - b.depth);
    const byId = new Map(items.map(item => [item.id, item]));
    updateEmptyState(items.length);
    const g = grouping(), arrows = state.lens !== 'full';
    $('#visible-count').textContent = `${format(model.bubbles.length)} ${g.word}${model.shown.length ? ` · ${format(model.shown.length)} nodes expanded` : ''} · double-click a bubble to expand`;
    ctx.save(); ctx.translate(width / 2 + state.x, height / 2 + state.y); ctx.scale(state.scale, state.scale);
    const px = 1 / Math.max(state.scale, .05);  // one screen pixel in graph units
    const pick = state.selected?.id || (state.selectedCluster ? `cluster:${state.selectedCluster.id}` : null);
    const line = (a, b) => { ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); };
    const maxCount = model.edges.reduce((m, e) => Math.max(m, e.count), 1);
    for (const edge of model.edges) {
      const a = byId.get(edge.a), b = byId.get(edge.b);
      if (!a || !b) continue;
      const lit = pick && (edge.a === pick || edge.b === pick);
      ctx.globalAlpha = pick && !lit ? .12 : 1;
      ctx.strokeStyle = edge.tech ? techColor(edge.tech) : lit ? '#aa9dffdd' : '#91a0bb55';
      ctx.lineWidth = (edge.tech ? 2.5 : .6 + 3.4 * Math.log1p(edge.count) / Math.log1p(maxCount)) * px;
      line(a, b);
      if (arrows || edge.tech) { ctx.fillStyle = ctx.strokeStyle; arrowHead(a, b, (b._cluster ? clusterRadius(b) * (state.is3d ? Math.max(.55, Math.min(1.8, b.perspective)) : 1) : 3) + 2 * px, (6 + 2 * Math.log1p(edge.count)) * px); }
    }
    for (const link of state.links) {
      if (!model.shownIds.has(link.source) || !model.shownIds.has(link.target) || !lensAllows(link.relation)) continue;
      const a = byId.get(link._src ?? link.source) || byId.get(link.source), b = byId.get(link._tgt ?? link.target) || byId.get(link.target), lit = pick && (a.id === pick || b.id === pick);
      ctx.globalAlpha = pick && !lit ? .12 : 1;
      ctx.strokeStyle = link.tech ? techColor(link.tech) : lit ? '#aa9dffdd' : '#91a0bb55'; ctx.lineWidth = link.tech ? 2 * px : lit ? 1.5 : .7;
      line(a, b);
      if (arrows || link.tech) { ctx.fillStyle = ctx.strokeStyle; arrowHead(a, b, 4, 5 * px); }
    }
    const labels = [];
    for (const item of items) {
      const depthSize = state.is3d ? Math.max(.55, Math.min(1.8, item.perspective)) : 1;
      const isPick = item.id === pick, isHover = item.id === state.hovered?.id;
      const fade = state.is3d && !isPick ? Math.max(.35, Math.min(1, item.perspective * .8)) : 1;
      const radius = depthSize * (item._cluster ? clusterRadius(item) : isPick ? 5.7 : 2.5);
      ctx.globalAlpha = fade * (item._cluster ? .8 : 1);
      ctx.fillStyle = item.file_type === 'document' ? '#45c4df' : colorFor(g.index.get(item._cluster ? item._cluster.id : g.key(item)));
      ctx.beginPath(); ctx.arc(item.x, item.y, radius, 0, Math.PI * 2); ctx.fill();
      if (item._cluster || isPick) { ctx.globalAlpha = 1; ctx.strokeStyle = isPick ? '#eeeaff' : '#0b1020'; ctx.lineWidth = (isPick ? 2 : 1) * px; ctx.beginPath(); ctx.arc(item.x, item.y, radius + (isPick ? 3 * px : 0), 0, Math.PI * 2); ctx.stroke(); }
      if (isPick || isHover || item._cluster) labels.push({ item, radius, first: isPick || isHover });
    }
    ctx.globalAlpha = 1;
    ctx.font = `600 ${12 * px}px ui-sans-serif, system-ui, sans-serif`;
    // Selected/hovered first, then biggest clusters; a label that would overlap one already drawn is skipped.
    labels.sort((a, b) => (b.first - a.first) || ((b.item.size || 0) - (a.item.size || 0)));
    const placed = [];
    for (const { item, radius, first } of labels) {
      const text = item._cluster ? `${String(item.label).slice(0, 40)} · ${format(item.size)}` : String(item.label).slice(0, 60);
      const w = ctx.measureText(text).width;
      const lx = item._cluster ? item.x - w / 2 : item.x + 12 * px, ly = item._cluster ? item.y + radius + 16 * px : item.y - 12 * px;
      const box = [lx - 5 * px, ly - 14 * px, lx + w + 5 * px, ly + 6 * px];
      if (!first && (placed.length >= 30 || placed.some(o => box[0] < o[2] && box[2] > o[0] && box[1] < o[3] && box[3] > o[1]))) continue;
      placed.push(box);
      ctx.fillStyle = '#0b1020dd'; ctx.fillRect(lx - 5 * px, ly - 14 * px, w + 10 * px, 20 * px);
      ctx.fillStyle = '#f4f6ff'; ctx.fillText(text, lx, ly);
    }
    ctx.restore();
  }
  function findClusterItem(event) {
    const point = screenPoint(event), model = clusterModel();
    let best = null, bestScore = Infinity;
    for (const item of [...model.shown, ...model.bubbles]) {
      const p = projectNode(item);
      const depthSize = state.is3d ? Math.max(.55, Math.min(1.8, p.perspective)) : 1;
      const reach = item._cluster ? clusterRadius(item) * depthSize : 10 / state.scale;
      const distance = Math.hypot(p.x - point.x / state.scale, p.y - point.y / state.scale);
      if (distance <= reach && distance / reach < bestScore) { best = item; bestScore = distance / reach; }
    }
    return best;
  }
  function updateClusterControls() {
    $('#cluster-controls').classList.toggle('hidden', state.nodes.length <= state.clusterThreshold);
    $('#cluster-toggle').setAttribute('aria-pressed', String(state.clusterMode));
    $('#collapse-all').classList.toggle('hidden', !(clustersActive() && state.expanded.size));
  }
  function expandCluster(id) { if (!state.expanded.has(id)) toggleCluster(id); }
  function toggleCluster(id) {
    if (state.expanded.has(id)) state.expanded.delete(id); else state.expanded.add(id);
    if (state.selectedCluster?.id === id) showClusterDetails(state.selectedCluster);
    else if (state.selected) renderDetails(state.selected);
    draw();
  }
  function showClusterDetails(group) {
    if (!group) return;
    state.selected = null; state.selectedCluster = group;
    document.querySelector('.app-shell').classList.toggle('has-selection', true);
    const g = grouping(), nodes = group.nodes.filter(passesFilters), ids = new Set(nodes.map(n => n.id));
    const kinds = new Map(), folders = new Map(), uses = new Map(), usedBy = new Map();
    const bump = (map, key) => map.set(key, (map.get(key) || 0) + 1);
    for (const node of nodes) {
      bump(kinds, node._kind); bump(folders, folderOf(node));
      for (const [map, list] of [[uses, state.outgoing.get(node.id)], [usedBy, state.incoming.get(node.id)]]) for (const edge of list || []) {
        const other = state.nodeById.get(edge.nodeId);
        if (other && !ids.has(other.id) && passesFilters(other) && lensAllows(edge.relation) && relationKind(edge.relation) !== 'structure') bump(map, g.key(other));
      }
    }
    const top = (map, n) => [...map].sort((a, b) => b[1] - a[1]).slice(0, n);
    const degree = node => state.adjacency.get(node.id)?.length || 0;
    const hubs = [...nodes].sort((a, b) => degree(b) - degree(a)).slice(0, 12);
    const groupName = id => g.groups[g.index.get(id)]?.name || id;
    const groupList = (map, empty) => top(map, 12).map(([id, c]) => `<button class="detail-connection" data-group="${escapeHtml(id)}"><span class="relation">${format(c)} links</span><span class="neighbor">${escapeHtml(groupName(id))}</span></button>`).join('') || `<div class="empty-result">${empty}</div>`;
    const panel = $('#details-panel');
    panel.innerHTML = `<div class="detail-header"><span class="node-kind">${g.title}</span><strong class="detail-title">${escapeHtml(group.name)}</strong><button class="detail-close" aria-label="Close cluster details">×</button></div>
      <div class="detail-actions"><button data-expand title="Draw this cluster's nodes instead of one bubble">${state.expanded.has(group.id) ? 'Collapse' : 'Expand'}</button>${state.lens === 'architecture' ? '' : '<button data-only title="Show only this cluster\'s nodes">Show only this</button>'}</div>
      <div class="detail-label">SIZE</div><div class="detail-value">${format(nodes.length)} nodes</div>
      <div class="detail-label">KINDS</div><div class="detail-value">${top(kinds, 6).map(([k, c]) => `${escapeHtml(KIND_NAMES[k] || k)} ${format(c)}`).join(' · ') || '—'}</div>
      <div class="detail-label">FOLDERS</div><div class="detail-value detail-code">${top(folders, 5).map(([f, c]) => `${escapeHtml(f)} (${format(c)})`).join('<br>') || '—'}</div>
      <div class="detail-label" title="Links from this ${g.title.toLowerCase()} to others: calls, imports, references">DEPENDS ON <span class="muted">(${uses.size})</span></div>
      <div class="connection-list">${groupList(uses, 'Depends on nothing outside itself.')}</div>
      <div class="detail-label" title="Links from others into this ${g.title.toLowerCase()}">USED BY <span class="muted">(${usedBy.size})</span></div>
      <div class="connection-list">${groupList(usedBy, 'Nothing outside uses it.')}</div>
      <div class="detail-label">MOST CONNECTED NODES</div>
      <div class="connection-list">${hubs.map(n => `<button class="detail-connection" data-node="${escapeHtml(n.id)}"><span class="relation">${format(degree(n))}</span><span class="neighbor">${escapeHtml(n.label)}</span></button>`).join('') || '<div class="empty-result">No nodes.</div>'}</div>`;
    panel.querySelector('.detail-close').addEventListener('click', () => select(null));
    panel.querySelector('[data-expand]').addEventListener('click', () => toggleCluster(group.id));
    panel.querySelector('[data-only]')?.addEventListener('click', () => { state.activeCommunity = group.id; renderCommunityList(); fitGraph(); });
    panel.querySelectorAll('[data-group]').forEach(b => b.addEventListener('click', () => showClusterDetails(g.groups[g.index.get(b.dataset.group)])));
    panel.querySelectorAll('[data-node]').forEach(b => b.addEventListener('click', () => select(state.nodeById.get(b.dataset.node))));
    draw();
  }

  // Node Inspector helpers: what a node is, and its relationships split by direction and relation.
  function kindLabel(node) {
    return ({ type: 'Type (class / struct / interface)', method: 'Method', function: 'Function', file: 'File', document: 'Document', external: 'External' })[node._kind] || node._kind || 'Unknown';
  }
  function inspectorRelations(node) {
    const section = (title, hint, list) => {
      const groups = new Map();
      for (const edge of list || []) {
        const other = state.nodeById.get(edge.nodeId);
        if (!other) continue;
        const relation = edge.relation || 'links';
        if (!groups.has(relation)) groups.set(relation, []);
        groups.get(relation).push(other);
      }
      const ranked = [...groups].sort((a, b) => b[1].length - a[1].length);
      const total = ranked.reduce((n, [, nodes]) => n + nodes.length, 0);
      return `<div class="detail-label" title="${hint}">${title} <span class="muted">(${format(total)})</span></div>` + (ranked.length ? ranked.map(([relation, nodes]) => `<details class="rel-group"${ranked.length <= 3 ? ' open' : ''}><summary><span class="relation">${escapeHtml(relation)}</span> <span class="muted">${format(nodes.length)}</span></summary><div class="connection-list">${nodes.slice(0, 25).map(n => `<button class="detail-connection" data-node="${escapeHtml(n.id)}"><span class="neighbor">${escapeHtml(n.label)}</span></button>`).join('')}${nodes.length > 25 ? `<div class="empty-result">+${format(nodes.length - 25)} more · use Focus to see all</div>` : ''}</div></details>`).join('') : '<div class="empty-result">None.</div>');
    };
    const related = new Map(), docs = [];
    for (const edge of state.adjacency.get(node.id) || []) {
      const other = state.nodeById.get(edge.nodeId);
      if (!other) continue;
      if (other._group !== node._group) related.set(other._group, (related.get(other._group) || 0) + 1);
      if (other.file_type === 'document' && !docs.includes(other)) docs.push(other);
    }
    const groupName = id => state.communities[state.groupIndex.get(id)]?.name || id;
    return section('INCOMING', 'Nodes that point to this one: callers, importers, users', state.incoming.get(node.id))
      + section('OUTGOING', 'Nodes this one points to: calls, imports, dependencies', state.outgoing.get(node.id))
      + `<div class="detail-label">RELATED COMPONENTS <span class="muted">(${related.size})</span></div><div class="connection-list">${[...related].sort((a, b) => b[1] - a[1]).slice(0, 10).map(([id, c]) => `<button class="detail-connection" data-group="${escapeHtml(id)}"><span class="relation">${format(c)} links</span><span class="neighbor">${escapeHtml(groupName(id))}</span></button>`).join('') || '<div class="empty-result">Only connected inside its own group.</div>'}</div>`
      + (docs.length ? `<div class="detail-label">DOCUMENTATION</div><div class="connection-list">${docs.slice(0, 10).map(n => `<button class="detail-connection" data-node="${escapeHtml(n.id)}"><span class="relation">doc</span><span class="neighbor">${escapeHtml(n.label)}</span></button>`).join('')}</div>` : '');
  }

  $('#cluster-toggle').addEventListener('click', () => { state.clusterMode = !state.clusterMode; store('tokenGraph.clusters', state.clusterMode); if (state.selectedCluster) select(null); else draw(); });
  $('#collapse-all').addEventListener('click', () => { state.expanded.clear(); if (state.selectedCluster) showClusterDetails(state.selectedCluster); else draw(); });
  $('#focus-3').addEventListener('click', () => state.focus && enterFocus(state.nodeById.get(state.focus.id), 3));
  for (const dir of ['both', 'in', 'out']) $(`#focus-dir-${dir}`).addEventListener('click', () => { state.focusDir = dir; if (state.focus) enterFocus(state.nodeById.get(state.focus.id), state.focus.depth, dir); });
