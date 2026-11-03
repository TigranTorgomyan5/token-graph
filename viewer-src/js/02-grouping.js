  // Grouping (sidebar list, colours, layout): Token Graph communities, or source folders.
  function folderOf(node) {
    const file = String(node.source_file || '').replace(/\\/g, '/');
    if (!file) return node.file_type === 'concept' || node.external ? '(external)' : '(no file)';
    const parts = file.split('/');
    parts.pop();
    return parts.slice(0, 3).join('/') || '(project root)';  // three levels: internal/place/debit, src/X.Domain/Entities
  }
  function buildGroups() {
    const byFolder = state.groupBy === 'folder';
    const groups = new Map();
    state.clusterVersion++; state.expanded = new Set(); state.selectedCluster = null;  // group ids change
    for (const node of state.nodes) {
      const id = byFolder ? folderOf(node) : String(node.community ?? 'Other');
      node._group = id;
      if (!groups.has(id)) groups.set(id, { id, name: byFolder ? id : node.community_name, nodes: [] });
      groups.get(id).nodes.push(node);
    }
    state.communities = [...groups.values()].sort((a, b) => b.nodes.length - a.nodes.length);
    state.groupIndex = new Map(state.communities.map((group, i) => [group.id, i]));
    for (const node of state.nodes) {
      const i = state.groupIndex.get(node._group);
      const group = state.communities[i];
      const angle = i / Math.max(1, state.communities.length) * Math.PI * 2;
      const spread = 58 + Math.sqrt(group.nodes.length) * 9;
      const hash = hashString(node.id);
      const localAngle = hash % 6283 / 1000;
      const radius = Math.sqrt((hash >>> 4) % 10000) / 100 * spread;
      node.x = Math.cos(angle) * (150 + Math.sqrt(state.nodes.length) * 8) + Math.cos(localAngle) * radius;
      node.y = Math.sin(angle) * (150 + Math.sqrt(state.nodes.length) * 8) + Math.sin(localAngle) * radius;
      const sphereY = 1 - 2 * (i + .5) / Math.max(1, state.communities.length);
      const ring = Math.sqrt(Math.max(0, 1 - sphereY * sphereY));
      const groupAngle = i * Math.PI * (3 - Math.sqrt(5));
      const scene = state.communities.length === 1 ? 0 : 150 + Math.sqrt(state.nodes.length) * 8;
      const localZ = ((hash >>> 12) % 2000) / 1000 - 1;
      const localRing = Math.sqrt(Math.max(0, 1 - localZ * localZ));
      node._x3 = Math.cos(groupAngle) * ring * scene + Math.cos(localAngle) * localRing * radius;
      node._y3 = sphereY * scene + localZ * radius;
      node._z3 = Math.sin(groupAngle) * ring * scene + Math.sin(localAngle) * localRing * radius;
    }
    const label = byFolder ? 'FOLDERS' : 'COMMUNITIES';
    buildModules(); computeLensNodes();
    $('#group-heading').textContent = label;
    $('.metric-card:nth-child(3) .metric-label').textContent = label;
    $('.metric-card:nth-child(3) .metric-note').textContent = byFolder ? 'source folders' : 'detected clusters';
  }
  function setGroupBy(mode) {
    if (state.groupBy === mode) return;
    state.groupBy = mode;
    try { localStorage.setItem('tokenGraph.groupBy', mode); } catch (_) { /* per-viewer convenience only */ }
    document.querySelectorAll('[data-group-by]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.groupBy === mode)));
    if (!state.nodes.length) return;
    state.activeCommunity = null;
    buildGroups();
    updateMetrics({});
    renderCommunityList();
    fitGraph();
  }

