
(() => {
  const canvas = document.querySelector('#graph-canvas');
  const ctx = canvas.getContext('2d');
  const state = { nodes: [], links: [], nodeById: new Map(), adjacency: new Map(), communities: [], activeCommunity: null, selected: null, hovered: null, query: '', view: 'all', scale: 1, x: 0, y: 0, dragging: false, dpr: 1 };
  const palette = ['#8e7cff','#51c6e3','#e58bd4','#e6ad63','#61d3aa','#6f9cff','#f07883','#b2d66e','#bb91f4','#e0d56b','#53b9a6','#f09461'];
  const $ = (selector) => document.querySelector(selector);
  const format = (value) => new Intl.NumberFormat().format(value);
  // Set when the page is served by scripts/explorer_server.py (desktop shortcut); null when opened as a file.
  const serverToken = location.protocol.startsWith('http') ? new URLSearchParams(location.search).get('token') : null;
  const api = async (path, body) => {
    const response = await fetch(path, body === undefined
      ? { cache: 'no-store', headers: { 'X-Token-Graph': serverToken } }
      : { method: 'POST', cache: 'no-store', headers: { 'X-Token-Graph': serverToken, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Server answered ${response.status}`);
    return data;
  };
  if (serverToken) $('.brand').href = `/?token=${encodeURIComponent(serverToken)}`;
  const colorFor = (id) => palette[Math.abs(Number(id) || 0) % palette.length];

  // Path rules for the "Hide tests" / "Hide generated" toggles (source_file, forward slashes).
  const TEST_PATH = /(^|\/)(tests?|__tests__|specs?|testdata|test-data|fixtures)(\/|$)|(^|\/)[^\/]+\.(unit|integration|functional|e2e|acceptance)?tests?(\/|$)|_test\.go$|(^|\/)test_[^\/]*\.py$|_test\.py$|\.(test|spec)\.[cm]?[jt]sx?$/i;
  const CSHARP_TEST_FILE = /[a-z0-9]Tests?\.cs$/;  // case-sensitive: FooTests.cs, not Latest.cs
  const GENERATED_PATH = /(^|\/)(gen|generated|__generated__)(\/|$)|\.pb(\.gw)?\.go$|_gen\.go$|_pb2(_grpc)?\.pyi?$|\.g(\.i)?\.cs$|\.designer\.cs$|\.generated\.\w+$|(^|\/)migrations\/[^\/]+\.cs$|(^|\/)obj\//i;
  const stored = (key, fallback = false) => { try { const v = localStorage.getItem(key); return v === null ? fallback : v === '1'; } catch (_) { return fallback; } };
  const store = (key, on) => { try { localStorage.setItem(key, on ? '1' : '0'); } catch (_) { /* per-viewer convenience only */ } };
  state.is3d = stored('tokenGraph.is3d', true);
  state.yaw = .45; state.pitch = -.25; state.cameraTarget = {x:0,y:0,z:0};
  state.hideTests = stored('tokenGraph.hideTests');
  state.hideGenerated = stored('tokenGraph.hideGenerated', true);  // same default as /token-graph answers
  state.focus = null;      // { id, depth, dir, ids } while focus mode is on
  state.focusDir = 'both'; // focus follows: 'both' directions, 'in' (callers/users) or 'out' (callees/dependencies)
  state.clusterMode = stored('tokenGraph.clusters', true);  // see js/06b-clusters.js
  state.clusterThreshold = 500; state.expanded = new Set(); state.selectedCluster = null; state.clusterCache = null; state.clusterVersion = 0;
  state.groupsOpen = stored('tokenGraph.groupsOpen', false);  // sidebar groups dropdown
  state.lens = 'full'; state.lensNodes = new Set(); state.modules = []; state.moduleIndex = new Map();  // see js/06c-lenses.js
  state.highlight = null;  // { nodes: Map(id -> level), seeds: Set, routeEdges: Set('a|b') } from the ask bar
  state.highlightOnly = false;
  state.kind = 'all';      // node-kind filter from the sidebar select
  state.groupBy = (() => { try { return localStorage.getItem('tokenGraph.groupBy') === 'folder' ? 'folder' : 'community'; } catch (_) { return 'community'; } })();
  state.groupIndex = new Map();
  state.editors = { vscode: false, rider: false };

  function tagNode(node) {
    const file = String(node.source_file || '').replace(/\\/g, '/');
    node._test = Boolean(file) && (TEST_PATH.test(file) || CSHARP_TEST_FILE.test(file));
    node._generated = Boolean(file) && GENERATED_PATH.test(file);
    return node;
  }
  function passesFilters(node) { return !(state.hideTests && node._test) && !(state.hideGenerated && node._generated) && (state.kind === 'all' || node._kind === state.kind); }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    state.dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(rect.width * state.dpr));
    canvas.height = Math.max(1, Math.round(rect.height * state.dpr));
    ctx.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
    draw();
  }

  function projectNode(node) {
    if (!state.is3d) return { ...node, depth: 0, perspective: 1 };
    const target = state.cameraTarget;
    const x = (node._x3 || 0) - target.x;
    const y = (node._y3 || 0) - target.y;
    const z = (node._z3 || 0) - target.z;
    const cy = Math.cos(state.yaw), sy = Math.sin(state.yaw);
    const cp = Math.cos(state.pitch), sp = Math.sin(state.pitch);
    const rx = x * cy + z * sy, rz = -x * sy + z * cy;
    const ry = y * cp - rz * sp, depth = y * sp + rz * cp;
    const distance = state.cameraDistance || 1500;
    const perspective = distance / Math.max(distance * .25, distance - depth);
    return { ...node, x: rx * perspective, y: ry * perspective, depth, perspective };
  }

  function centerNode(node) {
    const point = projectNode(node);
    state.x = -point.x * state.scale; state.y = -point.y * state.scale;
  }

  function setDimension(is3d) {
    state.is3d = is3d;
    store('tokenGraph.is3d', is3d);
    $('#mode-3d').classList.toggle('active', is3d);
    $('#mode-2d').classList.toggle('active', !is3d);
    $('#mode-3d').setAttribute('aria-pressed', String(is3d));
    $('#mode-2d').setAttribute('aria-pressed', String(!is3d));
    $('#navigation-hint').innerHTML = is3d
      ? '<span>DRAG</span> rotate <b>·</b> <span>SHIFT + DRAG</span> pan <b>·</b> <span>SCROLL</span> zoom <b>·</b> double-click to focus'
      : '<span>DRAG</span> pan <b>·</b> <span>SCROLL</span> zoom <b>·</b> double-click to focus';
    fitGraph();
  }

  function fitGraph(only = null) {
    const model = !only && clustersActive() ? clusterModel() : null;  // cluster views fit their bubbles
    const fitNodes = model ? [...model.bubbles, ...model.shown] : state.nodes.filter(node => visible(node) && (!only || only.has(node.id)));
    if (!fitNodes.length) { draw(); return; }
    const bounds = (values) => values.reduce((b, value) => [Math.min(b[0], value), Math.max(b[1], value)], [Infinity, -Infinity]);
    if (state.is3d) {
      const bx = bounds(fitNodes.map(n => n._x3 || 0)), by = bounds(fitNodes.map(n => n._y3 || 0)), bz = bounds(fitNodes.map(n => n._z3 || 0));
      state.cameraTarget = {x:(bx[0]+bx[1])/2,y:(by[0]+by[1])/2,z:(bz[0]+bz[1])/2};
      const t = state.cameraTarget;
      state.cameraDistance = fitNodes.reduce((distance,n) => Math.max(distance, Math.hypot(n._x3-t.x,n._y3-t.y,n._z3-t.z)*3),200);
    }
    const projected = fitNodes.map(projectNode);
    const bx = bounds(projected.map(n => n.x)), by = bounds(projected.map(n => n.y));
    const padding = 56;
    state.scale = Math.max(.03, Math.min(1, Math.max(1,canvas.clientWidth-padding*2)/Math.max(1,bx[1]-bx[0]), Math.max(1,canvas.clientHeight-padding*2)/Math.max(1,by[1]-by[0])));
    state.x = -(bx[0]+bx[1])/2*state.scale;
    state.y = -(by[0]+by[1])/2*state.scale;
    draw();
  }

  function nameCommunity(group) {
    const explicit = group.nodes.find(node => node.community_name && !/^Community\s+\d+$/i.test(node.community_name));
    if (explicit) return explicit.community_name;
    const files = new Map();
    for (const node of group.nodes) {
      if (!node.source_file) continue;
      const entry = files.get(node.source_file) || { count: 0, connections: 0 };
      entry.count++;
      entry.connections += state.adjacency.get(node.id)?.length || 0;
      files.set(node.source_file, entry);
    }
    const ranked = [...files].sort((a, b) => b[1].count - a[1].count || b[1].connections - a[1].connections || a[0].localeCompare(b[0]));
    if (ranked.length) {
      const parts = ranked[0][0].replace(/\\/g, '/').split('/');
      const filename = parts.pop();
      return /^(index|init|__init__|main)\./i.test(filename) && parts.length ? `${parts.pop()}/${filename}` : filename;
    }
    const hub = [...group.nodes].filter(node => node.label).sort((a, b) => (state.adjacency.get(b.id)?.length || 0) - (state.adjacency.get(a.id)?.length || 0))[0];
    return hub?.label || 'Unassigned';
  }

  function normalizeGraph(raw) {
    const edges = raw?.links ?? raw?.edges;
    if (!raw || !Array.isArray(raw.nodes) || !Array.isArray(edges)) throw new Error('This file is not a Token Graph graph export. It must include nodes and links or edges.');
    if (raw.nodes.some(node => !node || node.id == null)) throw new Error('Each graph node needs an id.');
    state.nodes = raw.nodes.map(node => tagNode({ ...node, id: String(node.id), x: 0, y: 0 }));
    state.focus = null;
    $('#focus-bar').classList.add('hidden');
    state.highlight = null; state.highlightOnly = false;
    $('#ask-result').classList.add('hidden');
    const edgeId = value => typeof value === 'object' && value !== null ? value.id : value;
    state.links = edges.filter(link => link && edgeId(link.source) != null && edgeId(link.target) != null).map(link => ({...link, source:String(edgeId(link.source)), target:String(edgeId(link.target)), ...(link._src != null ? {_src:String(edgeId(link._src))} : {}), ...(link._tgt != null ? {_tgt:String(edgeId(link._tgt))} : {})}));
    state.nodeById = new Map(state.nodes.map((node) => [node.id, node]));
    state.outgoing = new Map(state.nodes.map(node => [node.id, []]));
    state.incoming = new Map(state.nodes.map(node => [node.id, []]));
    state.adjacency = new Map(state.nodes.map((node) => [node.id, []]));
    for (const link of state.links) {
      const source = link._src ?? link.source, target = link._tgt ?? link.target;
      if (state.outgoing.has(source) && state.incoming.has(target)) {
        state.outgoing.get(source).push({nodeId: target, relation: link.relation});
        state.incoming.get(target).push({nodeId: source, relation: link.relation});
      }
      if (state.adjacency.has(link.source)) state.adjacency.get(link.source).push({ nodeId: link.target, relation: link.relation });
      if (state.adjacency.has(link.target)) state.adjacency.get(link.target).push({ nodeId: link.source, relation: link.relation });
    }
    // Community names are always computed: the details panel and search show them in either grouping.
    const communities = new Map();
    for (const node of state.nodes) {
      const id = String(node.community ?? 'Other');
      if (!communities.has(id)) communities.set(id, { id, nodes: [] });
      communities.get(id).nodes.push(node);
    }
    for (const group of communities.values()) {
      const name = nameCommunity(group);
      for (const node of group.nodes) node.community_name = name;
    }
    const methodTargets = new Set(state.links.filter(link => link.relation === 'method').map(link => link.target));
    for (const node of state.nodes) node._kind = kindOf(node, methodTargets);
    fillKindOptions();
    buildGroups();
    renderCommunityList();
    renderSearchResults();
    updateMetrics(raw);
    $('#data-status').textContent = `${format(state.nodes.length)} nodes loaded`;
    $('#snapshot').textContent = raw.built_at_commit ? `Commit ${String(raw.built_at_commit).slice(0, 7)}` : 'Graph snapshot';
    $('#graph-updated').textContent = raw.built_at_commit ? `Built from ${String(raw.built_at_commit).slice(0, 10)}` : 'Token Graph export';
    $('#loading').classList.add('hidden');
    $('#data-status').classList.remove('error-state');
    state.x = 0; state.y = 0; state.scale = 1;
    resize();
    fitGraph();
  }

