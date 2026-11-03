  function draw() {
    if (!canvas.width) return;
    const width = canvas.clientWidth, height = canvas.clientHeight;
    ctx.clearRect(0, 0, width, height);
    updateClusterControls();
    if (clustersActive()) { drawClusters(width, height); return; }
    const visibleNodes = state.nodes.filter(visible).map(projectNode).sort((a,b) => a.depth - b.depth);
    updateEmptyState(visibleNodes.length);
    const projectedById = new Map(visibleNodes.map(node => [node.id, node]));
    const visibleIds = new Set(visibleNodes.map(n => n.id));
    if (state.focus) $('#focus-count').textContent = `${format(visibleNodes.length)} nodes shown`;
    $('#visible-count').textContent = state.focus ? `Focus · ${format(visibleNodes.length)} nodes` : state.query ? `${format(visibleNodes.length)} matches` : state.activeCommunity ? `${format(visibleNodes.length)} nodes` : state.view === 'docs' ? `${format(visibleNodes.length)} docs` : 'All nodes';
    if (state.lens === 'flow' && !state.focus) $('#visible-count').textContent += ' · double-click a method to trace its calls';
    ctx.save(); ctx.translate(width / 2 + state.x, height / 2 + state.y); ctx.scale(state.scale, state.scale);
    const selectedNeighbors = state.selected ? new Set((state.adjacency.get(state.selected.id) || []).map(e => e.nodeId)) : null;
    const hl = state.highlight?.nodes || null;
    const routeEdges = state.highlight?.routeEdges || null;
    for (const edge of state.links) {
      if (!visibleIds.has(edge.source) || !visibleIds.has(edge.target) || !lensAllows(edge.relation)) continue;
      const a = projectedById.get(edge.source), b = projectedById.get(edge.target);
      const focus = state.selected && (a.id === state.selected.id || b.id === state.selected.id);
      const onRoute = routeEdges && (routeEdges.has(`${a.id}|${b.id}`) || routeEdges.has(`${b.id}|${a.id}`));
      if (state.selected && !focus && !onRoute) continue;
      if (hl && !focus && !onRoute && !(hl.has(a.id) && hl.has(b.id))) continue;
      ctx.strokeStyle = onRoute ? '#f4c95d' : edge.tech ? techColor(edge.tech) : focus ? '#aa9dffdd' : hl ? '#f4c95d66' : '#91a0bb55';
      ctx.lineWidth = onRoute ? 3 / Math.max(state.scale, .35) : edge.tech ? (focus ? 3 : 2) / Math.max(state.scale, .35) : state.view === 'docs' ? (focus ? 1.8 : 1) / state.scale : (focus ? 1.5 : .7);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      if (state.lens !== 'full' || edge.tech) { const from = projectedById.get(edge._src ?? edge.source), to = projectedById.get(edge._tgt ?? edge.target); if (from && to) { ctx.fillStyle = ctx.strokeStyle; arrowHead(from, to, 4, 5 / Math.max(state.scale, .5)); } }
    }
    for (const node of visibleNodes) {
      const match = state.query && `${node.label} ${node.source_file || ''}`.toLowerCase().includes(state.query);
      const selected = state.selected?.id === node.id;
      const neighbor = selectedNeighbors?.has(node.id);
      const level = hl ? hl.get(node.id) : undefined;
      const lit = level !== undefined;
      const base = state.view === 'docs' ? (selected ? 6 : neighbor ? 4.5 : 2.7) / state.scale : selected ? 5.7 : neighbor ? 4.1 : state.scale < .5 ? 1.7 : 2.5;
      const depthSize = state.is3d ? Math.max(.55, Math.min(1.8, node.perspective)) : 1;
      let radius = depthSize * (lit ? Math.max(base, (level === 0 ? 7 : level === 1 ? 4.8 : 3.6) / Math.max(state.scale, .5)) : base);
      if (node.file_type === 'service') radius = Math.max(radius, 7 * depthSize);  // system graph: one node per service
      ctx.globalAlpha = hl && !lit && !selected && !neighbor ? .08 : state.selected && !selected && !neighbor && !lit ? .18 : state.query && !match ? .22 : 1;
      if (state.is3d && !selected && !lit) ctx.globalAlpha *= Math.max(.3, Math.min(1, node.perspective * .8));
      ctx.fillStyle = node.file_type === 'document' ? '#45c4df' : colorFor(state.groupIndex.get(node._group));
      ctx.beginPath(); ctx.arc(node.x, node.y, radius, 0, Math.PI * 2); ctx.fill();
      if (lit && (level === 0 || state.highlight.seeds.has(node.id))) { ctx.globalAlpha = 1; ctx.strokeStyle = '#f4c95d'; ctx.lineWidth = 2 / Math.max(state.scale, .5); ctx.beginPath(); ctx.arc(node.x, node.y, radius + 3 / Math.max(state.scale, .5), 0, Math.PI * 2); ctx.stroke(); }
      if (selected || match) { ctx.globalAlpha = 1; ctx.strokeStyle = selected ? '#eeeaff' : '#ffffff'; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(node.x, node.y, radius + 3, 0, Math.PI * 2); ctx.stroke(); }
    }
    ctx.globalAlpha = 1;
    const labelNodes = new Set([state.selected?.id, state.hovered?.id, ...(state.highlight?.labelIds || []), ...visibleNodes.filter(n => n.file_type === 'service').map(n => n.id)].filter(Boolean));
    if (labelNodes.size) {
      ctx.font = '600 12px ui-sans-serif, system-ui, sans-serif';
      for (const node of visibleNodes) if (labelNodes.has(node.id)) {
        const label = (node.file_type === 'service' ? '⬡ ' : '') + String(node.label).slice(0, 60);
        const textWidth = ctx.measureText(label).width;
        const labelX = node.x + 12, labelY = node.y - 12;
        ctx.fillStyle = '#0b1020eF'; ctx.fillRect(labelX - 5, labelY - 14, textWidth + 10, 21);
        ctx.strokeStyle = '#596783'; ctx.lineWidth = 1 / state.scale; ctx.strokeRect(labelX - 5, labelY - 14, textWidth + 10, 21);
        ctx.fillStyle = '#f4f6ff'; ctx.fillText(label, labelX, labelY);
      }
    }
    ctx.restore();
  }

  function screenPoint(event) { const rect = canvas.getBoundingClientRect(); return { x: event.clientX - rect.left - rect.width / 2 - state.x, y: event.clientY - rect.top - rect.height / 2 - state.y }; }
  function findNode(event) {
    if (clustersActive()) return findClusterItem(event);
    const point = screenPoint(event);
    let best = null, bestDistance = 10 / state.scale, bestDepth = -Infinity;
    for (const node of state.nodes) {
      if (!visible(node)) continue;
      const p = projectNode(node);
      const distance = Math.hypot(p.x-point.x/state.scale,p.y-point.y/state.scale);
      if (distance < bestDistance || (Math.abs(distance-bestDistance)<.01 && p.depth>bestDepth)) {
        best=node; bestDistance=distance; bestDepth=p.depth;
      }
    }
    return best;
  }
