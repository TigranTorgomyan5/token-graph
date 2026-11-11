  // ---- System page: several service folders (a "system") and how they talk to each other.
  // The server scans every service for NATS / Kafka / RabbitMQ / MassTransit / gRPC / HTTP endpoints
  // and links producer to consumer (scripts/system_links.py). This page draws the service map and
  // shows the code lines behind each connection; a service opens as a normal code graph.
  const sysState = { list: [], current: null, scan: null, loading: false, error: '', note: '', editing: null, selected: null, projectQuery: '', hidden: new Set(), pos: new Map(), drag: null, pan: null, noClick: false, focusOnly: false, view: 'map', mapView: { x: 500, y: 300, zoom: 1 }, graphIds: new Map(), building: false, browser: null, noteError: false, saving: false };
  const TECH_COLOR = { nats: '#52c9e8', http: '#8877ff', grpc: '#55d6a2', kafka: '#e6ad63', rabbitmq: '#f09461', masstransit: '#e58bd4', redis: '#f07883', websocket: '#b2d66e', mqtt: '#e0d56b', sqs: '#6f9cff', servicebus: '#53b9a6' };
  const TECH_LABEL = { nats: 'NATS', http: 'HTTP', grpc: 'gRPC', kafka: 'Kafka', rabbitmq: 'RabbitMQ', masstransit: 'MassTransit', redis: 'Redis', websocket: 'WebSocket', mqtt: 'MQTT', sqs: 'SQS/SNS', servicebus: 'Service Bus', pubsub: 'Pub/Sub', zeromq: 'ZeroMQ', jms: 'JMS' };
  const MATCH_NOTE = { exact: 'same subject/topic text on both sides', 'shared name': 'both sides use the same constant name', route: 'client path matches a server route; the destination is inferred', url: 'a URL names this service (caller side only)', 'config key': 'a config key names this service (caller side only)' };
  const techColor = t => TECH_COLOR[t] || '#bb91f4';
  const techLabel = t => TECH_LABEL[t] || t;
  const techChip = (t, text = techLabel(t)) => `<span class="sys-chip" style="--tc:${techColor(t)}">${escapeHtml(text)}</span>`;
  const sysEl = id => $(`#sys-${id}`);
  function remembered() { try { return localStorage.getItem('tokenGraph.system'); } catch (_) { return null; } }

  async function loadSystems() {
    if (!serverToken) { renderSystem(); return; }
    try { sysState.list = (await api('/api/systems')).systems; sysState.error = ''; }
    catch (error) { sysState.error = error.message; }
    if (!sysState.current || !sysState.list.some(s => s.id === sysState.current.id)) {
      const saved = remembered();
      sysState.current = sysState.list.find(s => s.id === saved) || sysState.list[0] || null;
      sysState.scan = null;
    }
    renderSystem();
    if (sysState.current && !sysState.scan && !sysState.editing) scanSystem(false);
  }

  async function scanSystem(refresh) {
    const id = sysState.current?.id;
    if (!id) return;
    sysState.loading = true; sysState.error = ''; renderSystem();
    try {
      const result = await api('/api/systems/scan', { id, refresh });
      if (sysState.current?.id !== id) return;
      sysState.scan = result; sysState.selected = null; sysState.focusOnly = false;
      layoutSystem();
    } catch (error) { sysState.error = error.message; }
    sysState.loading = false;
    renderSystem();
  }

  function selectSystem(id) {
    sysState.current = sysState.list.find(s => s.id === id) || null;
    sysState.scan = null; sysState.selected = null; sysState.projectQuery = ''; sysState.focusOnly = false; sysState.mapView = { x: 500, y: 300, zoom: 1 };
    try { localStorage.setItem('tokenGraph.system', id); } catch (_) { /* per-viewer convenience only */ }
    renderSystem();
    scanSystem(false);
  }

  function layoutSystem() {
    const services = sysState.scan.services, n = services.length, r = n <= 1 ? 0 : Math.min(250, 120 + n * 26);
    services.forEach((s, i) => {
      if (sysState.pos.has(s.path)) return;
      const a = -Math.PI / 2 + 2 * Math.PI * i / Math.max(1, n);
      sysState.pos.set(s.path, { x: 500 + r * 1.6 * Math.cos(a), y: 300 + r * Math.sin(a) });
    });
  }

  // ---- service map (SVG) ----
  function visibleSystemEdges() {
    const selected = sysState.focusOnly && sysState.selected?.type === 'service' ? sysState.selected.index : null;
    return sysState.scan.edges.map((edge, index) => [edge, index]).filter(([edge]) => !sysState.hidden.has(edge.tech) && (selected === null || edge.source === selected || edge.target === selected));
  }
  function mapSvg() {
    const { services, edges } = sysState.scan;
    const query = sysState.projectQuery.trim().toLocaleLowerCase();
    const matchesService = s => !query || `${s.name} ${s.path}`.toLocaleLowerCase().includes(query);
    const P = i => sysState.pos.get(services[i].path);
    const degree = services.map(() => 0);
    edges.forEach(e => { degree[e.source]++; degree[e.target]++; });
    const radius = i => 18 + Math.min(14, degree[i] * 2);
    const pairKey = e => `${Math.min(e.source, e.target)}-${Math.max(e.source, e.target)}`;
    const pairTotal = new Map(), pairSeen = new Map();
    const visible = visibleSystemEdges();
    const focusedNodes = sysState.focusOnly && sysState.selected?.type === 'service' ? new Set([sysState.selected.index, ...visible.flatMap(([e]) => [e.source, e.target])]) : null;
    visible.forEach(([e]) => pairTotal.set(pairKey(e), (pairTotal.get(pairKey(e)) || 0) + 1));
    const sel = sysState.selected;
    let lines = '', labels = '';
    for (const [e, index] of visible) {
      const key = pairKey(e), k = pairSeen.get(key) || 0, total = pairTotal.get(key);
      pairSeen.set(key, k + 1);
      const lo = P(Math.min(e.source, e.target)), hi = P(Math.max(e.source, e.target));
      const len = Math.hypot(hi.x - lo.x, hi.y - lo.y) || 1, nx = -(hi.y - lo.y) / len, ny = (hi.x - lo.x) / len;
      const off = (k - (total - 1) / 2) * 52;
      const a = P(e.source), b = P(e.target);
      const c = { x: (a.x + b.x) / 2 + nx * off, y: (a.y + b.y) / 2 + ny * off };
      const toward = (p, q, r) => { const d = Math.hypot(q.x - p.x, q.y - p.y) || 1; return { x: p.x + (q.x - p.x) * r / d, y: p.y + (q.y - p.y) * r / d }; };
      const s = toward(a, c, radius(e.source) + 3), t = toward(b, c, radius(e.target) + 9);
      const d = `M${s.x.toFixed(1)},${s.y.toFixed(1)} Q${c.x.toFixed(1)},${c.y.toFixed(1)} ${t.x.toFixed(1)},${t.y.toFixed(1)}`;
      const on = sel?.type === 'edge' && sel.index === index;
      const dim = (sel && !on && !(sel.type === 'service' && (e.source === sel.index || e.target === sel.index))) || (query && !matchesService(services[e.source]) && !matchesService(services[e.target]));
      const color = techColor(e.tech);
      lines += `<g class="sys-edge${on ? ' on' : ''}${dim ? ' dim' : ''}" data-sys="edge" data-i="${index}" tabindex="0" role="button" aria-label="${escapeHtml(`${services[e.source].name} to ${services[e.target].name}, ${techLabel(e.tech)} connection`)}"><path d="${d}" class="hit"/><path d="${d}" stroke="${color}" marker-end="url(#sys-arrow-${e.tech.replace(/\W/g, '')})"${e.confidence.every(x => x === 'url' || x === 'config key' || x === 'shared name' || x === 'route') ? ' stroke-dasharray="6 4"' : ''}/><title>${escapeHtml(`${services[e.source].name} → ${services[e.target].name} · ${techLabel(e.tech)}\n${e.channels.map(ch => ch.name).join('\n')}`)}</title></g>`;
      const m = { x: 0.25 * s.x + 0.5 * c.x + 0.25 * t.x, y: 0.25 * s.y + 0.5 * c.y + 0.25 * t.y };
      const text = `${techLabel(e.tech)}${e.count > 1 ? ` · ${e.count}` : ''}`, w = text.length * 6.4 + 12;
      labels += `<g class="sys-label${dim ? ' dim' : ''}" data-sys="edge" data-i="${index}"><rect x="${(m.x - w / 2).toFixed(1)}" y="${(m.y - 9).toFixed(1)}" width="${w.toFixed(1)}" height="18" rx="9" stroke="${color}"/><text x="${m.x.toFixed(1)}" y="${(m.y + 3.5).toFixed(1)}" fill="${color}">${escapeHtml(text)}</text></g>`;
    }
    const nodes = services.map((s, i) => {
      if (focusedNodes && !focusedNodes.has(i)) return '';
      const p = P(i), on = sel?.type === 'service' && sel.index === i;
      const dim = (sel && !on && !(sel.type === 'edge' && [edges[sel.index].source, edges[sel.index].target].includes(i)) && !(sel.type === 'service' && edges.some(e => (e.source === sel.index && e.target === i) || (e.target === sel.index && e.source === i)))) || (query && !matchesService(s));
      return `<g class="sys-node${on ? ' on' : ''}${dim ? ' dim' : ''}${s.missing ? ' missing' : ''}" data-sys="node" data-i="${i}" tabindex="0" role="button" aria-label="${escapeHtml(`Select service ${s.name}`)}" transform="translate(${p.x.toFixed(1)},${p.y.toFixed(1)})"><circle r="${radius(i)}"/><text class="sys-node-icon" y="4">${escapeHtml(s.name.slice(0, 2).toUpperCase())}</text><text class="sys-node-name" y="${radius(i) + 16}">${escapeHtml(s.name)}</text><text class="sys-node-sub" y="${radius(i) + 29}">${escapeHtml(s.missing ? 'folder not found' : `${format(s.files)} files`)}</text><title>${escapeHtml(s.path)}</title></g>`;
    }).join('');
    const techs = [...new Set(edges.map(e => e.tech))];
    const markers = techs.map(t => `<marker id="sys-arrow-${t.replace(/\W/g, '')}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="${techColor(t)}"/></marker>`).join('');
    const view = sysState.mapView, width = 1000 / view.zoom, height = 600 / view.zoom;
    return `<svg class="sys-svg" viewBox="${view.x - width / 2} ${view.y - height / 2} ${width} ${height}" role="group" aria-label="Service map: ${services.length} services, ${visible.length} visible connections"><defs>${markers}</defs>${lines}${nodes}${labels}</svg>`;
  }

  function systemCardsHtml() {
    const { services, edges } = sysState.scan;
    const query = sysState.projectQuery.trim().toLocaleLowerCase();
    const matches = s => !query || `${s.name} ${s.path}`.toLocaleLowerCase().includes(query);
    const shown = services.map((s, i) => ({ kind: 'service', service: s, i })).filter(n => matches(n.service));
    const outsideByService = new Map();
    shown.forEach(({ service, i }) => (service.unmatched || []).filter(ev => !sysState.hidden.has(ev.tech)).forEach(ev => {
      const key = `${i}:${ev.role}`;
      if (!outsideByService.has(key)) outsideByService.set(key, { kind: 'outside', key, service, i, role: ev.role, endpoints: [] });
      outsideByService.get(key).endpoints.push(ev);
    }));
    const visibleOutside = [...outsideByService.values()];
    const items = [...shown, ...visibleOutside];
    if (!shown.length) return '<p class="dash-note">No services match this search.</p>';
    const cols = 3, cardW = 240, cardH = 78, gapX = 95, gapY = 64, rows = Math.ceil(items.length / cols);
    const width = 3 * cardW + 2 * gapX + 60, height = Math.max(150, rows * (cardH + gapY) + 30);
    const pos = new Map(items.map((n, idx) => [n, { x: 30 + (idx % cols) * (cardW + gapX), y: 20 + Math.floor(idx / cols) * (cardH + gapY) }]));
    const links = [];
    edges.forEach((e, k) => {
      if (sysState.hidden.has(e.tech)) return;
      const from = items.find(n => n.kind === 'service' && n.i === e.source), to = items.find(n => n.kind === 'service' && n.i === e.target);
      if (from && to) links.push({ from, to, label: `${techLabel(e.tech)}${e.count > 1 ? ` · ${e.count}` : ''}`, title: `${services[e.source].name} → ${services[e.target].name}: ${e.channels.map(c => c.name).join(', ')}`, edge: k });
    });
    visibleOutside.forEach(n => {
      const serviceNode = items.find(item => item.kind === 'service' && item.i === n.i);
      links.push(n.role === 'out' ? { from: serviceNode, to: n } : { from: n, to: serviceNode });
    });
    const paths = links.map(link => {
      const a = pos.get(link.from), b = pos.get(link.to), ac = { x: a.x + cardW / 2, y: a.y + cardH / 2 }, bc = { x: b.x + cardW / 2, y: b.y + cardH / 2 };
      const dx = bc.x - ac.x, dy = bc.y - ac.y, len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len;
      const scale = Math.min((cardW / 2) / Math.max(.001, Math.abs(dx / len)), (cardH / 2) / Math.max(.001, Math.abs(dy / len)));
      const x1 = ac.x + ux * scale, y1 = ac.y + uy * scale, x2 = bc.x - ux * scale, y2 = bc.y - uy * scale, midx = (x1 + x2) / 2, midy = (y1 + y2) / 2;
      const outsideTitle = link.title || (link.from.kind === 'service'
        ? `${link.from.service.name} has ${link.to.endpoints.length} unmatched outgoing endpoint(s): ${link.to.endpoints.slice(0, 4).map(ev => ev.channel).join(', ')}`
        : `${link.to.service.name} has ${link.from.endpoints.length} unmatched incoming endpoint(s): ${link.from.endpoints.slice(0, 4).map(ev => ev.channel).join(', ')}`);
      return `<g class="sys-card-edge"${link.edge !== undefined ? ` data-sys="edge" data-i="${link.edge}" tabindex="0" role="button"` : ''}><path d="M${x1} ${y1} L${x2} ${y2}" marker-end="url(#sys-card-arrow)"></path>${link.label ? `<text x="${midx}" y="${midy - 5}">${escapeHtml(link.label)}</text>` : ''}<title>${escapeHtml(outsideTitle)}</title></g>`;
    }).join('');
    const boxes = items.map(n => {
      const p = pos.get(n), isService = n.kind === 'service';
      const name = isService ? n.service.name : n.role === 'out' ? 'External dependencies' : 'External callers';
      const channels = isService ? [] : [...new Set(n.endpoints.map(ev => `${techLabel(ev.tech)} · ${ev.channel}`))];
      const note = isService ? (n.service.missing ? 'Folder not found' : `${format(n.service.files)} files · ${format(n.service.routes)} HTTP routes`) : `${n.endpoints.length} endpoints · ${channels.slice(0, 2).join(' · ')}${channels.length > 2 ? ` · +${channels.length - 2}` : ''}`;
      const action = isService ? `data-sys="node" data-i="${n.i}" aria-label="Select service ${escapeHtml(name)}"` : `data-sys="outside" data-i="${n.i}" aria-label="Inspect ${name.toLocaleLowerCase()} for ${escapeHtml(n.service.name)}, ${n.endpoints.length} endpoints"`;
      const title = isService ? n.service.path : n.endpoints.slice(0, 8).map(ev => `${ev.file}:${ev.line}`).join('\n');
      return `<g class="architecture-box sys-service-box${isService ? '' : ' sys-outside-box'}${sysState.selected?.type === 'service' && sysState.selected.index === n.i ? ' on' : ''}" role="button" tabindex="0" ${action} transform="translate(${p.x} ${p.y})"><rect width="${cardW}" height="${cardH}" rx="10"></rect><text x="14" y="30">${escapeHtml(name)}</text><text class="architecture-box-note" x="14" y="55">${escapeHtml(note.length > 40 ? `${note.slice(0, 37)}…` : note)}</text><title>${escapeHtml(title)}</title></g>`;
    }).join('');
    return `<div class="architecture-card-map sys-card-map"><svg viewBox="0 0 ${width} ${height}" role="group" aria-label="Service dependency rectangles with grouped external endpoints"><defs><marker id="sys-card-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10z" fill="#aaa0ff"></path></marker></defs><g class="sys-card-edges">${paths}</g>${boxes}</svg></div>`;
  }

  // ---- one graph for the whole system: all services' code + cross-service links (Graph page, 2D/3D) ----
  async function waitForBuild(label) {
    const started = Date.now();
    for (;;) {
      await new Promise(resolve => setTimeout(resolve, 1500));
      const { job } = await api('/api/status');
      if (!job || job.state !== 'running') {
        if (job?.state === 'error') throw new Error(`${label}: graph build failed. ${job.message || ''}`);
        return;
      }
      sysState.note = `${label}… ${Math.round((Date.now() - started) / 1000)}s`; renderSystem();
    }
  }

  async function openSystemGraph() {
    const scan = sysState.scan, system = sysState.current;
    if (!scan || !system || sysState.building) return;
    sysState.building = true;
    try {
      const missing = scan.services.filter(s => !s.has_graph && !s.missing);
      if (missing.length && confirm(`${missing.length} of ${scan.services.length} services have no code graph yet: ${missing.map(s => s.name).join(', ')}.\n\nBuild them now? This can take a few minutes.\nCancel opens the system graph without their code (they appear as one service node).`)) {
        for (const [k, s] of missing.entries()) {
          const label = `Building code graph for ${s.name} (${k + 1}/${missing.length})`;
          sysState.note = label; renderSystem();
          await api('/api/build', { project: s.path });
          await waitForBuild(label);
          s.has_graph = true;
        }
      }
      sysState.note = 'Loading the system graph…'; renderSystem();
      const raw = await api('/api/systems/graph', { id: system.id });
      let gid = sysState.graphIds.get(system.id);
      if (gid && loadedGraphs.has(gid)) loadedGraphs.get(gid).raw = raw;
      else { gid = addGraph(raw, `⬡ ${system.name}`); sysState.graphIds.set(system.id, gid); }
      sysState.note = '';
      openGraph('full');
      await switchGraph(gid);
      $('#graph-selector').value = gid;
      $('#data-status').textContent = `System ${system.name}: ${scan.services.length} services · ${format(raw.cross_links)} cross-service links${raw.missing_graphs.length ? ` · without code graph: ${raw.missing_graphs.join(', ')}` : ''}`;
    } catch (error) {
      sysState.note = error.message;
    } finally {
      sysState.building = false;
      if (state.page === 'system') renderSystem();
    }
  }

  // ---- details ----
  function evidenceList(list, service, empty) {
    if (!list.length) return `<p class="dash-note">${escapeHtml(empty)}</p>`;
    return `<ul class="sys-evidence">${list.map(ev => `<li><button type="button" data-sys="open" data-svc="${service}" data-file="${escapeHtml(ev.file)}" data-line="${ev.line}" title="Open in VS Code"><span class="sys-loc">${escapeHtml(ev.file)}:${ev.line}</span><code>${escapeHtml(ev.text)}</code></button></li>`).join('')}</ul>`;
  }

  function edgeDetail(index) {
    const { services, edges } = sysState.scan, e = edges[index];
    const from = services[e.source], to = services[e.target];
    const how = e.confidence.map(c => MATCH_NOTE[c] || c).join('; ');
    return `<div class="sys-detail-head"><strong>${escapeHtml(from.name)} → ${escapeHtml(to.name)}</strong>${techChip(e.tech)}</div>
      <p class="dash-note"><strong>Why this connection appears:</strong> ${escapeHtml(how)}. This is static evidence; it does not confirm a live connection.</p>
      ${e.channels.map(ch => `<div class="sys-channel"><div class="sys-channel-name"><code>${escapeHtml(ch.name)}</code></div>
        <div class="sys-side"><span class="eyebrow">${escapeHtml(from.name)} · caller / publisher evidence</span>${evidenceList(ch.from, e.source, 'No source evidence found.')}</div>
        <div class="sys-side"><span class="eyebrow">${escapeHtml(to.name)} · receiver evidence</span>${evidenceList(ch.to, e.target, 'No receiver evidence found; this destination is inferred from the caller or configuration.')}</div></div>`).join('')}`;
  }

  function serviceDetail(i) {
    const { services, edges } = sysState.scan, s = services[i];
    const outgoing = edges.map((e, k) => [e, k]).filter(([e]) => e.source === i), incoming = edges.map((e, k) => [e, k]).filter(([e]) => e.target === i);
    const edgeRow = ([e, k], other) => `<li><button type="button" data-sys="edge" data-i="${k}">${techChip(e.tech)}<span>${escapeHtml(services[other].name)}</span><span class="dash-note">${escapeHtml(e.channels.slice(0, 2).map(c => c.name).join(', '))}${e.count > 2 ? ` +${e.count - 2}` : ''}</span></button></li>`;
    const techs = Object.entries(s.techs).map(([t, v]) => techChip(t, `${techLabel(t)} ${v.out}↑ ${v.in}↓`)).join('') || '<span class="dash-note">No communication found.</span>';
    const outside = s.unmatched.slice(0, 40);
    return `<div class="sys-detail-head"><strong>${escapeHtml(s.name)}</strong></div>
      <p class="dash-note sys-path">${escapeHtml(s.path)}</p>
      <div class="sys-chips">${techs}</div>
      <p class="dash-note">${format(s.files)} files scanned · ${format(s.routes)} HTTP routes · ↑ sends/calls, ↓ receives/serves</p>
      <div class="dash-footer">${s.has_graph ? `<button type="button" class="dash-link" data-sys="graph" data-i="${i}">Open code graph</button>` : `<button type="button" class="dash-link" data-sys="build" data-i="${i}">Build code graph</button>`}</div>
      <h3 class="sys-h3">Sends to / calls (${outgoing.length})</h3>${outgoing.length ? `<ul class="sys-links">${outgoing.map(x => edgeRow(x, x[0].target)).join('')}</ul>` : '<p class="dash-note">No outgoing connections detected in this system.</p>'}
      <h3 class="sys-h3">Receives from / called by (${incoming.length})</h3>${incoming.length ? `<ul class="sys-links">${incoming.map(x => edgeRow(x, x[0].source)).join('')}</ul>` : '<p class="dash-note">No incoming connections detected in this system.</p>'}
      <h3 class="sys-h3">No partner in this system (${s.unmatched.length})</h3>
      ${outside.length ? `<ul class="sys-evidence">${outside.map(ev => `<li><button type="button" data-sys="open" data-svc="${i}" data-file="${escapeHtml(ev.file)}" data-line="${ev.line}">${techChip(ev.tech, `${techLabel(ev.tech)} ${ev.role === 'out' ? '↑' : '↓'}`)}<code>${escapeHtml(ev.channel)}</code><span class="sys-loc">${escapeHtml(ev.file)}:${ev.line}</span></button></li>`).join('')}</ul>` : '<p class="dash-note">No unmatched endpoints detected.</p>'}`;
  }

  function summaryDetail() {
    const { services, edges } = sysState.scan, by = {};
    edges.forEach(e => { by[e.tech] = (by[e.tech] || 0) + 1; });
    return `<p class="dash-note">${services.length} services · ${edges.length} connections · scanned in ${sysState.scan.seconds}s</p>
      <div class="sys-chips">${Object.entries(by).map(([t, n]) => techChip(t, `${techLabel(t)} ${n}`)).join('')}</div>
      <p class="dash-note">Click a service or connection to inspect the available code evidence. Drag services to rearrange. Dashed lines are inferred from routes, names or configuration; verify their destinations. <b>Open as graph</b> shows service code and the links found between them.</p>`;
  }

  // ---- editor (create / edit a system) ----
  function editorHtml() {
    const ed = sysState.editing;
    return `<article class="dash-card sys-editor"><header><h2>${ed.id ? 'Edit system' : 'New system'}</h2></header><div class="dash-body">
      <label class="eyebrow" for="sys-name">NAME</label><input id="sys-name" class="sys-input" value="${escapeHtml(ed.name)}" placeholder="e.g. SparkleGaming" maxlength="80">
      <p class="dash-note">Choose the project folder to find the services inside it, and/or add service folders from anywhere — any language.</p>
      <div class="dash-footer"><button type="button" class="dash-link" data-sys="pick-root">Choose project folder…</button><button type="button" class="dash-link" data-sys="pick-service">Add service folder…</button></div>
      ${folderBrowserHtml()}
      <div class="sys-path-row"><input id="sys-path" class="sys-input" placeholder="or paste a folder path"><button type="button" class="dash-link" data-sys="add-path">Add as service</button><button type="button" class="dash-link" data-sys="discover-path">Find services inside</button></div>
      <ul class="sys-service-list">${ed.services.map((s, i) => `<li><label><input type="checkbox" data-sys="toggle" data-i="${i}"${s.on ? ' checked' : ''}> <strong>${escapeHtml(s.name)}</strong> <span class="dash-note">${escapeHtml(s.path)}</span></label><button type="button" class="sys-x" data-sys="remove" data-i="${i}" aria-label="Remove ${escapeHtml(s.name)}">×</button></li>`).join('') || '<li class="dash-note">No services yet.</li>'}</ul>
      ${sysState.note ? `<p class="sys-note${sysState.noteError ? ' error' : ''}" role="${sysState.noteError ? 'alert' : 'status'}">${escapeHtml(sysState.note)}</p>` : ''}
      <div class="dash-footer"><button type="button" class="dash-link primary" data-sys="save"${sysState.saving ? ' disabled aria-busy="true"' : ''}>${sysState.saving ? 'Saving…' : 'Save &amp; scan'}</button><button type="button" class="dash-link" data-sys="cancel">Cancel</button></div>
    </div></article>`;
  }

  function addServices(items) {
    const ed = sysState.editing, known = new Set(ed.services.map(s => s.path.toLowerCase()));
    let added = 0;
    for (const item of items) if (!known.has(item.path.toLowerCase())) { ed.services.push({ name: item.name, path: item.path, on: true }); known.add(item.path.toLowerCase()); added++; }
    return added;
  }

  // ---- folder browser inside the page: works on every OS and never hides behind the browser
  // window, unlike a native dialog. The server lists folder names only (/api/folders).
  const lastName = path => path.split(/[\\/]/).filter(Boolean).pop() || path;
  async function openFolderBrowser(purpose, path) {
    const start = path ?? (sysState.editing.root || (state.projectPath ? state.projectPath.replace(/[\\/][^\\/]+[\\/]?$/, '') : ''));
    sysState.browser = { purpose, data: sysState.browser?.data || null, loading: true, error: '' };
    renderSystem();
    try { sysState.browser.data = await api(`/api/folders?path=${encodeURIComponent(start)}`); }
    catch (error) {
      if (sysState.browser.data || !start) sysState.browser.error = error.message;  // keep the folder that was open
      else {  // a remembered folder may be gone: start from the home folder instead
        try { sysState.browser.data = await api('/api/folders'); } catch (_) { sysState.browser.error = error.message; }
      }
    }
    if (!sysState.browser) return;
    sysState.browser.loading = false;
    renderSystem();
    $('.sys-browser-list button')?.focus();
  }

  function folderBrowserHtml() {
    const fb = sysState.browser;
    if (!fb) return '';
    const data = fb.data;
    const title = fb.purpose === 'root' ? 'Choose the project folder that contains the services' : 'Choose a service folder';
    const roots = (data?.roots || []).map(r => `<button type="button" class="sys-chip-btn" data-sys="fb-open" data-path="${escapeHtml(r.path)}">${escapeHtml(r.name)}</button>`).join('');
    const list = !data ? `<li class="dash-note">${fb.loading ? 'Loading…' : 'Could not open a folder.'}</li>`
      : data.folders.map(f => `<li><button type="button" data-sys="fb-open" data-path="${escapeHtml(f.path)}"><span aria-hidden="true">▸</span> ${escapeHtml(f.name)}</button></li>`).join('') || '<li class="dash-note">No sub-folders here.</li>';
    return `<section class="sys-browser" aria-label="${escapeHtml(title)}" aria-busy="${fb.loading}">
      <p class="sys-browser-title"><strong>${escapeHtml(title)}</strong></p>
      <div class="sys-browser-bar">${roots}<button type="button" class="sys-chip-btn" data-sys="fb-open" data-path="${escapeHtml(data?.parent || '')}"${data?.parent ? '' : ' disabled'} aria-label="Up one folder">↑ Up</button></div>
      <div class="sys-path-row"><input id="sys-fb-path" class="sys-input" value="${escapeHtml(data?.path || '')}" aria-label="Folder path" spellcheck="false"><button type="button" class="dash-link" data-sys="fb-go">Go</button></div>
      ${fb.error ? `<div class="dash-error" role="alert">${escapeHtml(fb.error)}</div>` : ''}
      <ul class="sys-browser-list">${list}</ul>
      ${data?.truncated ? '<p class="dash-note">Only the first 1,000 folders are shown; type a path above to go further.</p>' : ''}
      <div class="dash-footer"><button type="button" class="dash-link primary" data-sys="fb-choose"${data ? '' : ' disabled'}>${fb.purpose === 'root' ? 'Find services in' : 'Add'} “${escapeHtml(data ? lastName(data.path) : '')}”</button><button type="button" class="dash-link" data-sys="fb-cancel">Cancel</button><button type="button" class="dash-link" data-sys="fb-native" title="Open the operating system's own folder dialog instead">Use system dialog…</button></div>
    </section>`;
  }

  async function useFolder(purpose, path) {
    sysState.browser = null;
    if (purpose === 'root') await discoverInto(path);
    else { addServices([{ name: lastName(path), path }]); sysState.note = ''; }
  }

  async function nativePick(purpose, start) {
    sysState.note = 'Choose a folder in the system dialog…'; renderSystem();
    const picked = await api('/api/pick', { start: start || sysState.editing.root || state.projectPath || '' });
    sysState.note = '';
    if (picked.manual) { sysState.note = 'No system folder dialog is available here; choose the folder in the list instead.'; await openFolderBrowser(purpose, start); return; }
    if (picked.path) await useFolder(purpose, picked.path);
  }

  async function discoverInto(path) {
    const { services } = await api('/api/systems/discover', { path });
    const ed = sysState.editing;
    ed.root = path;
    if (!ed.name) ed.name = path.split(/[\\/]/).filter(Boolean).pop() || '';
    if (!services.length) {  // nothing that looks like a service inside: use the folder itself
      const added = addServices([{ name: lastName(path), path }]);
      sysState.note = `No service folders inside “${lastName(path)}”${added ? ', so it was added as one service' : ''}. Add more with “Add service folder…”.`;
      return;
    }
    const added = addServices(services);
    sysState.note = services.length === 1 && services[0].path === path
      ? `“${services[0].name}” is one service${added ? ' and was added' : ''}. Add the other services with “Add service folder…”.`
      : `Found ${services.length} service folders (${added} new).`;
  }

  async function onSystemAction(target) {
    const action = target.dataset.sys, i = Number(target.dataset.i);
    const ed = sysState.editing;
    if (ed && $('#sys-name')) ed.name = $('#sys-name').value;
    if (action !== 'toggle' && action !== 'save') sysState.noteError = false;
    try {
      if (action === 'new') { sysState.editing = { id: '', name: '', root: '', services: [] }; sysState.note = ''; }
      else if (action === 'edit' && sysState.current) { const c = sysState.current; sysState.editing = { id: c.id, name: c.name, root: c.root || '', services: c.services.map(s => ({ ...s, on: true })) }; sysState.note = ''; }
      else if (action === 'cancel') { sysState.editing = null; sysState.browser = null; }
      else if (action === 'rescan') { scanSystem(true); return; }
      else if (action === 'retry-load') { await loadSystems(); return; }
      else if (action === 'system-graph') { openSystemGraph(); return; }
      else if (action === 'zoom-in') sysState.mapView.zoom = Math.min(3, sysState.mapView.zoom * 1.35);
      else if (action === 'zoom-out') sysState.mapView.zoom = Math.max(.6, sysState.mapView.zoom / 1.35);
      else if (action === 'fit-map') sysState.mapView = { x: 500, y: 300, zoom: 1 };
      else if (action === 'view-map' || action === 'view-cards') { sysState.view = action === 'view-cards' ? 'cards' : 'map'; if (sysState.view === 'cards') sysState.focusOnly = false; }
      else if (action === 'focus' && sysState.selected?.type === 'service') sysState.focusOnly = !sysState.focusOnly;
      else if (action === 'delete' && sysState.current) {
        if (!confirm(`Delete the system "${sysState.current.name}"?\n\nOnly the list of services is removed; no files are touched.`)) return;
        await api('/api/systems/delete', { id: sysState.current.id });
        sysState.current = null; sysState.scan = null; await loadSystems(); return;
      }
      else if (action === 'pick-root' || action === 'pick-service') { await openFolderBrowser(action === 'pick-root' ? 'root' : 'service'); return; }
      else if (action === 'fb-open' && sysState.browser) { await openFolderBrowser(sysState.browser.purpose, target.dataset.path); return; }
      else if (action === 'fb-go' && sysState.browser) { await openFolderBrowser(sysState.browser.purpose, $('#sys-fb-path').value.trim()); return; }
      else if (action === 'fb-choose' && sysState.browser?.data) await useFolder(sysState.browser.purpose, sysState.browser.data.path);
      else if (action === 'fb-cancel') sysState.browser = null;
      else if (action === 'fb-native' && sysState.browser) { const { purpose, data } = sysState.browser; sysState.browser = null; await nativePick(purpose, data?.path); }
      else if (action === 'add-path' || action === 'discover-path') {
        const path = $('#sys-path').value.trim();
        if (!path) { $('#sys-path').focus(); return; }
        if (action === 'discover-path') await discoverInto(path);
        else addServices([{ name: path.split(/[\\/]/).filter(Boolean).pop(), path }]);
      }
      else if (action === 'toggle') { ed.services[i].on = target.checked; return; }
      else if (action === 'remove') ed.services.splice(i, 1);
      else if (action === 'save') {
        if (sysState.saving) return;
        const services = ed.services.filter(s => s.on).map(({ name, path }) => ({ name, path }));
        if (!ed.name.trim()) { sysState.note = 'Give the system a name first.'; sysState.noteError = true; renderSystem(); $('#sys-name')?.focus(); return; }
        else if (!services.length) { sysState.note = ed.services.length ? 'Tick at least one service to save.' : 'Add at least one service folder first: “Choose project folder…” or “Add service folder…”.'; sysState.noteError = true; }
        else {
          sysState.saving = true; sysState.note = ''; renderSystem();
          let saved;
          try { saved = await api('/api/systems/save', { id: ed.id, name: ed.name, root: ed.root, services }); }
          finally { sysState.saving = false; }
          const { system } = saved;
          sysState.editing = null; sysState.browser = null; sysState.note = ''; sysState.current = system; sysState.scan = null;
          try { localStorage.setItem('tokenGraph.system', system.id); } catch (_) { /* per-viewer convenience only */ }
          await loadSystems(); return;
        }
      }
      else if (action === 'node' || action === 'outside') { if (sysState.noClick) { sysState.noClick = false; return; } sysState.selected = sysState.selected?.type === 'service' && sysState.selected.index === i ? null : { type: 'service', index: i }; if (!sysState.selected) sysState.focusOnly = false; }
      else if (action === 'edge') { sysState.selected = { type: 'edge', index: i }; sysState.focusOnly = false; }
      else if (action === 'clear') { sysState.selected = null; sysState.focusOnly = false; }
      else if (action === 'tech') { const t = target.dataset.tech; if (sysState.hidden.has(t)) sysState.hidden.delete(t); else sysState.hidden.add(t); }
      else if (action === 'graph' || action === 'build') {
        const s = sysState.scan.services[i];
        if (action === 'graph') { await openServerProject(s.path, s.name); openGraph('full'); }
        else { openGraph('full'); buildProject(s.path); }
        return;
      }
      else if (action === 'open') {
        const s = sysState.scan.services[Number(target.dataset.svc)];
        await api('/api/open', { editor: 'vscode', project: s.path, file: target.dataset.file, line: target.dataset.line });
        sysState.note = `Opened ${target.dataset.file}:${target.dataset.line} in VS Code.`;
      }
      else return;
    } catch (error) { sysState.note = error.message; sysState.noteError = true; if (!sysState.editing) sysState.error = error.message; }
    renderSystem();
  }

  // ---- page ----
  function renderSystem() {
    const page = $('#system-page');
    if (!page) return;
    const toolbar = sysEl('toolbar'), main = sysEl('main'), message = sysEl('message');
    if (!serverToken) {
      toolbar.innerHTML = ''; main.classList.add('hidden');
      message.innerHTML = serverOnlyHtml('The System view'); message.classList.remove('hidden');
      return;
    }
    const options = sysState.list.map(s => `<option value="${escapeHtml(s.id)}"${s.id === sysState.current?.id ? ' selected' : ''}>${escapeHtml(s.name)} (${s.services.length})</option>`).join('');
    toolbar.innerHTML = `${sysState.list.length ? `<label class="eyebrow" for="sys-select">SYSTEM</label><select id="sys-select" class="project-select">${options}</select>` : ''}
      ${sysState.scan ? `<label class="eyebrow" for="sys-project-search">FIND PROJECT</label><input id="sys-project-search" class="sys-project-search" type="search" placeholder="Search service or project name…" value="${escapeHtml(sysState.projectQuery)}" autocomplete="off" aria-label="Search project or service names in this system">` : ''}
      <button type="button" class="dash-link primary" data-sys="new">New system</button>
      ${sysState.current && sysState.scan ? `<button type="button" class="dash-link primary" data-sys="system-graph"${sysState.building ? ' disabled' : ''} title="All services' code in one 2D/3D graph, with NATS / HTTP / gRPC … links between the code that sends and the code that receives">Open as graph</button>` : ''}
      ${sysState.current ? `<button type="button" class="dash-link" data-sys="edit">Edit services</button><button type="button" class="dash-link" data-sys="rescan"${sysState.loading ? ' disabled' : ''}>${sysState.loading ? 'Scanning…' : 'Rescan'}</button><button type="button" class="dash-link" data-sys="delete">Delete</button>` : ''}
      ${sysState.note && !sysState.editing ? `<span class="dash-note">${escapeHtml(sysState.note)}</span>` : ''}`;
    if (sysState.editing) { main.classList.add('hidden'); message.innerHTML = editorHtml(); message.classList.remove('hidden'); return; }
    if (!sysState.current) {
      main.classList.add('hidden');
      if (sysState.error) { message.innerHTML = errorHtml(sysState.error).replace('data-dash="retry"', 'data-sys="retry-load"'); message.classList.remove('hidden'); return; }
      message.innerHTML = `<div class="dash-card"><div class="dash-empty"><strong>Map how your services talk to each other</strong><p>A system is a set of service folders — e.g. SparkleGaming with Adastra and Bet. Token Graph scans them for NATS, Kafka, RabbitMQ, MassTransit, gRPC and HTTP calls in any language and draws who sends what to whom.</p><button type="button" class="dash-link primary" data-sys="new">Create a system</button></div></div>`;
      message.classList.remove('hidden');
      return;
    }
    if (!sysState.scan) {
      main.classList.add('hidden');
      message.innerHTML = sysState.error ? errorHtml(sysState.error).replace('data-dash="retry"', 'data-sys="rescan"') : loadingHtml(`Scanning ${sysState.current.services.length} services…`);
      message.classList.remove('hidden');
      return;
    }
    if (sysState.error) { message.innerHTML = `<div class="dash-error" role="alert">${escapeHtml(sysState.error)}. Showing the previous scan.</div>`; message.classList.remove('hidden'); }
    else message.classList.add('hidden');
    main.classList.remove('hidden');
    const { edges } = sysState.scan, techs = [...new Set(edges.map(e => e.tech))];
    sysEl('legend').innerHTML = techs.map(t => `<button type="button" class="sys-chip${sysState.hidden.has(t) ? ' off' : ''}" style="--tc:${techColor(t)}" data-sys="tech" data-tech="${escapeHtml(t)}" aria-pressed="${!sysState.hidden.has(t)}">${escapeHtml(techLabel(t))}</button>`).join('');
    sysEl('map').innerHTML = sysState.view === 'cards' ? systemCardsHtml() : mapSvg();
    const mapOnly = sysState.view === 'map';
    for (const action of ['view-map', 'view-cards']) {
      const button = sysEl('map').closest('.dash-card').querySelector(`[data-sys="${action}"]`);
      button?.setAttribute('aria-pressed', String((action === 'view-map') === mapOnly));
    }
    sysEl('map').closest('.dash-card').querySelectorAll('[data-sys="zoom-in"], [data-sys="zoom-out"], [data-sys="fit-map"]').forEach(button => { button.hidden = !mapOnly; });
    $('#sys-map-hint').textContent = mapOnly ? 'Drag the background to pan. Select a service to inspect it.' : 'Select a rectangle to inspect the service; external groups show its unmatched endpoints.';
    const sel = sysState.selected;
    const focus = sysEl('focus');
    focus.disabled = sysState.view === 'cards' || sel?.type !== 'service'; focus.setAttribute('aria-pressed', String(sysState.focusOnly)); focus.textContent = sysState.focusOnly ? 'Show all services' : 'Focus selected service';
    sysEl('detail-title').textContent = sel?.type === 'edge' ? 'Connection' : sel?.type === 'service' ? 'Service' : 'System';
    sysEl('detail').innerHTML = (sel ? `<button type="button" class="sys-x sys-close" data-sys="clear" aria-label="Back to system summary">×</button>` : '') + (sel?.type === 'edge' ? edgeDetail(sel.index) : sel?.type === 'service' ? serviceDetail(sel.index) : summaryDetail());
    const visibleEdges = visibleSystemEdges();
    sysEl('count').textContent = visibleEdges.length === edges.length ? `${edges.length}` : `${visibleEdges.length} of ${edges.length}`;
    const names = sysState.scan.services.map(s => s.name);
    sysEl('edges').innerHTML = visibleEdges.length ? `<table class="sys-table"><thead><tr><th>From</th><th></th><th>To</th><th>Via</th><th>Subjects / topics / routes</th><th>Match evidence</th></tr></thead><tbody>${visibleEdges.map(([e, k]) => `<tr data-sys="edge" data-i="${k}" class="${sel?.type === 'edge' && sel.index === k ? 'on' : ''}"><td><button type="button" class="sys-edge-link" data-sys="edge" data-i="${k}" aria-label="${escapeHtml(`Inspect connection from ${names[e.source]} to ${names[e.target]}`)}">${escapeHtml(names[e.source])}</button></td><td aria-hidden="true">→</td><td>${escapeHtml(names[e.target])}</td><td>${techChip(e.tech)}</td><td class="sys-channels">${escapeHtml(e.channels.map(c => c.name).join(', '))}</td><td>${escapeHtml(e.confidence.map(c => MATCH_NOTE[c] || c).join('; '))}</td></tr>`).join('')}</tbody></table>`
      : `<p class="dash-note">${edges.length ? 'No connections match the current filters or selected service.' : 'No connections were detected. Check each service for unmatched endpoints; some patterns cannot be resolved.'}</p>`;
  }

  // Dragging a service moves it; a click without movement selects it.
  function svgPoint(event) {
    const svg = sysEl('map').querySelector('svg'), pt = svg.createSVGPoint();
    pt.x = event.clientX; pt.y = event.clientY;
    return pt.matrixTransform(svg.getScreenCTM().inverse());
  }
  sysEl('map').addEventListener('pointerdown', event => {
    const node = event.target.closest('[data-sys="node"]');
    if (event.button !== 0) return;
    if (!node) {
      if (event.target.closest('[data-sys]')) return;
      sysState.pan = { x: event.clientX, y: event.clientY, view: { ...sysState.mapView } };
      return;
    }
    sysState.drag = { i: Number(node.dataset.i), moved: false, start: svgPoint(event) };
  });
  sysEl('map').addEventListener('pointermove', event => {
    if (sysState.pan) {
      const rect = sysEl('map').querySelector('svg')?.getBoundingClientRect(), pan = sysState.pan;
      if (rect?.width && rect.height) {
        sysState.mapView.x = pan.view.x - (event.clientX - pan.x) * 1000 / (pan.view.zoom * rect.width);
        sysState.mapView.y = pan.view.y - (event.clientY - pan.y) * 600 / (pan.view.zoom * rect.height);
        sysEl('map').innerHTML = mapSvg();
      }
      return;
    }
    const drag = sysState.drag;
    if (!drag) return;
    const p = svgPoint(event);
    if (!drag.moved && Math.hypot(p.x - drag.start.x, p.y - drag.start.y) < 4) return;
    drag.moved = true;
    sysState.pos.set(sysState.scan.services[drag.i].path, { x: Math.max(30, Math.min(970, p.x)), y: Math.max(30, Math.min(560, p.y)) });
    sysEl('map').innerHTML = mapSvg();
  });
  window.addEventListener('pointerup', () => { if (sysState.drag) { sysState.noClick = sysState.drag.moved; sysState.drag = null; } sysState.pan = null; });
  window.addEventListener('pointercancel', () => { sysState.drag = null; sysState.pan = null; });
  $('#system-page').addEventListener('click', event => {
    const target = event.target.closest('[data-sys]');
    if (target && target.dataset.sys !== 'toggle') onSystemAction(target);
    else if (event.target.closest('[data-dash="server-help"]')) onDashboardClick(event);
  });
  $('#system-page').addEventListener('change', event => {
    if (event.target.id === 'sys-select') selectSystem(event.target.value);
    else if (event.target.dataset.sys === 'toggle') onSystemAction(event.target);
  });
  $('#system-page').addEventListener('input', event => {
    if (event.target.id !== 'sys-project-search') return;
    sysState.projectQuery = event.target.value;
    if (sysState.scan) sysEl('map').innerHTML = sysState.view === 'cards' ? systemCardsHtml() : mapSvg();
  });
  $('#system-page').addEventListener('keydown', event => {
    const mapTarget = event.target.closest('.sys-node, .sys-edge, .sys-card-edge, .sys-service-box');
    if (mapTarget && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      const action = mapTarget.dataset.sys, index = mapTarget.dataset.i;
      onSystemAction(mapTarget);
      sysEl('map').querySelector(`[data-sys="${action}"][data-i="${index}"]`)?.focus();
      return;
    }
    if (event.key === 'Enter' && event.target.id === 'sys-path') onSystemAction({ dataset: { sys: 'add-path' } });
    if (event.key === 'Enter' && event.target.id === 'sys-project-search' && sysState.scan) {
      event.preventDefault();
      const query = sysState.projectQuery.trim().toLocaleLowerCase();
      if (!query) return;
      const services = sysState.scan.services;
      const index = services.findIndex(s => s.name.toLocaleLowerCase() === query || s.path.toLocaleLowerCase() === query);
      const match = index >= 0 ? index : services.findIndex(s => `${s.name} ${s.path}`.toLocaleLowerCase().includes(query));
      if (match >= 0) {
        sysState.selected = { type: 'service', index: match };
        renderSystem();
        const input = sysEl('toolbar').querySelector('#sys-project-search');
        input?.focus(); input?.setSelectionRange(input.value.length, input.value.length);
      }
    }
  });
