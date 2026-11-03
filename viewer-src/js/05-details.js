  // System graph: links from this node to code in other services (NATS, HTTP, gRPC …), with the subject/route.
  function crossServiceHtml(node) {
    const rows = state.links.filter(l => l.cross_service && (l.source === node.id || l.target === node.id)).slice(0, 30).map(l => {
      const out = l.source === node.id, other = state.nodeById.get(out ? l.target : l.source);
      if (!other) return '';
      return `<button class="detail-connection" data-node="${escapeHtml(other.id)}"><span class="relation" style="color:${techColor(l.tech)}">${out ? '→' : '←'} ${escapeHtml(techLabel(l.tech))}</span> <span class="neighbor">${escapeHtml(other.service || '')} · ${escapeHtml(other.label)}</span> <span class="muted">${escapeHtml(l.channel || '')}</span></button>`;
    }).join('');
    return rows ? `<div class="detail-label">TALKS TO OTHER SERVICES</div><div class="connection-list">${rows}</div>` : '';
  }

  function renderDetails(node) {
    const panel = $('#details-panel');
    document.querySelector('.app-shell').classList.toggle('has-selection', Boolean(node));
    if (!node) { panel.innerHTML = '<div class="details-empty"><div class="detail-orbit">◎</div><h2>Explore the graph</h2><p>Pick any node to inspect its source, community, and nearby connections.</p></div>'; return; }
    const project = node.project_path || state.projectPath;  // nodes of a system graph carry their own service folder
    const kind = node.file_type === 'document' ? 'DOC' : node.file_type === 'code' ? 'CODE' : String(node.file_type || 'NODE').toUpperCase();
    const editorButton = (editor, name) => {
      const localVscode = !serverToken && editor === 'vscode' && Boolean(node.source_file);
      const serverReady = Boolean(serverToken && project && state.editors[editor] && node.source_file);
      const ready = localVscode || serverReady;
      const why = localVscode ? 'Open this source file in VS Code; the browser will ask for the project folder path once. Shift-click to change it later.' : !serverToken ? 'Open this editor through the local Token Graph server' : !project ? 'Open a project graph first' : !state.editors[editor] ? `${name} is not installed on this computer` : `Open ${node.source_file} at this line in ${name}`;
      return `<button data-open="${editor}" ${ready ? '' : 'disabled'} title="${escapeHtml(why)}">Open in ${name}${serverToken && project && !state.editors[editor] ? ' (not installed)' : ''}</button>`;
    };
    panel.innerHTML = `<div class="detail-header"><span class="node-kind ${kind === 'DOC' ? 'doc' : ''}">${kind}</span><strong class="detail-title">${escapeHtml(node.label)}</strong><button class="detail-close" aria-label="Close node details">×</button></div>
      <div class="detail-actions"><button data-focus title="Show only this node and its neighbours (double-click a node does the same)">${state.focus?.id === node.id ? 'Focused' : 'Focus here'}</button>${clustersActive() && state.expanded.has(grouping().key(node)) ? '<button data-collapse title="Fold this node\'s cluster back into one bubble">Collapse cluster</button>' : ''}<button data-copy title="Copy this node, its connections and the visible source lines as short text to paste into an assistant">Copy context</button>${node.source_file ? editorButton('vscode', 'VS Code') + editorButton('rider', 'Rider') : ''}</div>
      ${node.service ? `<div class="detail-label">SERVICE</div><div class="detail-value">${escapeHtml(node.service)}</div>${crossServiceHtml(node)}` : ''}
      <div class="detail-label">SOURCE FILE</div><div class="detail-value detail-code">${escapeHtml(node.source_file || 'External / unavailable')}</div>
      ${node.source_location ? `<div class="detail-label">LOCATION</div><div class="detail-value">${escapeHtml(node.source_location)}</div>` : ''}
      <div class="detail-label">TYPE</div><div class="detail-value">${escapeHtml(kindLabel(node))}${node.type && node.type !== node._kind ? ` · ${escapeHtml(node.type)}` : ''}</div>
      <div class="detail-label">MODULE / FOLDER</div><div class="detail-value detail-code">${escapeHtml(folderOf(node))}</div>
      <div class="detail-label">COMMUNITY</div><div class="detail-value">${escapeHtml(node.community_name || 'Unassigned')}</div>
      ${node.description || node.rationale ? `<div class="detail-label">${node.description ? 'DESCRIPTION' : 'RATIONALE'}</div><div class="detail-value node-description">${escapeHtml(node.description || node.rationale)}</div>` : ''}
      <div class="detail-label">SOURCE CONTENT</div><pre class="source-excerpt" id="source-excerpt">${node.source_file ? 'Choose the Projects folder or use the local server to preview source.' : 'No source file is attached to this node.'}</pre>
      ${inspectorRelations(node)}`;
    panel.querySelector('.detail-close').addEventListener('click', () => select(null));
    panel.querySelector('[data-focus]').addEventListener('click', () => enterFocus(node, state.focus?.depth || 1));
    panel.querySelector('[data-copy]').addEventListener('click', async event => {
      const button = event.currentTarget;
      button.textContent = await copyText(nodeContext(node)) ? 'Copied ✓' : 'Copy failed';
      setTimeout(() => { button.textContent = 'Copy context'; }, 1500);
    });
    panel.querySelectorAll('[data-open]').forEach(button => button.addEventListener('click', async event => {
      const label = button.textContent;
      button.disabled = true;
      try {
        if (!serverToken && button.dataset.open === 'vscode') {
          openInVSCode(node, event.shiftKey);
          $('#data-status').textContent = 'If your browser asks, allow it to open Visual Studio Code. Shift-click to change the saved project path.';
        } else {
          await api('/api/open', { editor: button.dataset.open, project, file: node.source_file, line: node.source_location || '' });
        }
        button.textContent = 'Opened ✓';
      } catch (error) {
        button.textContent = label;
        $('#data-status').textContent = `Could not open the file: ${error.message}`;
        $('#data-status').classList.add('error-state');
      }
      setTimeout(() => { button.textContent = label; button.disabled = false; }, 1500);
    }));
    panel.querySelectorAll('[data-node]').forEach(button => button.addEventListener('click', () => select(state.nodeById.get(button.dataset.node))));
    panel.querySelectorAll('[data-group]').forEach(button => button.addEventListener('click', () => showClusterDetails(state.communities[state.groupIndex.get(button.dataset.group)] || state.modules[state.moduleIndex.get(button.dataset.group)])));
    panel.querySelector('[data-collapse]')?.addEventListener('click', () => { state.selected = null; const g = grouping(), id = g.key(node); toggleCluster(id); showClusterDetails(g.groups[g.index.get(id)]); });
    if (node.source_file && serverToken && project) {
      const sourceUrl = `/api/source?file=${encodeURIComponent(node.source_file)}&line=${encodeURIComponent(node.source_location || '')}&project=${encodeURIComponent(project || '')}`;
      fetch(sourceUrl, { headers: { 'X-Token-Graph': serverToken || '' } }).then(response => { if (!response.ok) throw new Error('Source excerpt unavailable'); return response.json(); }).then(source => {
        if (state.selected?.id !== node.id) return;
        const excerpt = $('#source-excerpt');
        if (excerpt) excerpt.textContent = source.lines.map((line, index) => `${String(source.start + index).padStart(4, ' ')}  ${line}`).join('\n') || '(empty file)';
      }).catch(() => {
        if (state.selected?.id === node.id && $('#source-excerpt')) $('#source-excerpt').textContent = 'Source content could not be loaded.';
      });
    } else if (node.source_file && state.localSource) {
      const fileName = String(node.source_file).replace(/\\/g, '/').replace(/^\.\//, '');
      const parts = fileName.split('/');
      const unsafe = !fileName || fileName.startsWith('/') || /^[a-z]:/i.test(fileName) || parts.includes('..') || parts.some(part => /^(\.git|\.env(?:\..*)?|id_rsa|id_ed25519|credentials(?:\..*)?)$/i.test(part)) || /\.(pem|key|p12|pfx)$/i.test(fileName);
      const file = unsafe ? null : state.localSource.files.get(state.localSource.root + fileName);
      if (!file || file.size > 2000000) {
        $('#source-excerpt').textContent = 'Source preview unavailable for this file.';
      } else {
        file.text().then(text => {
          if (state.selected !== node) return;
          const location = String(node.source_location || '').split(':').pop();
          const line = Math.max(1, Number(location.match(/\d+/)?.[0]) || 1);
          const start = Math.max(1, line - 3);
          const lines = text.split(/\r?\n/).slice(start-1, start+28);
          $('#source-excerpt').textContent = lines.map((value,index) => `${String(start+index).padStart(4,' ')}  ${value}`).join('\n') || '(empty file)';
        }).catch(() => {
          if (state.selected === node) $('#source-excerpt').textContent = 'Source content could not be loaded. Reselect the Projects folder.';
        });
      }
    }
  }
  function openInVSCode(node, changePath = false) {
    const source = String(node.source_file || '').replace(/\\/g, '/').replace(/^\.\//, '');
    const parts = source.split('/');
    const unsafe = !source || source.startsWith('/') || /^[a-z]:/i.test(source) || parts.includes('..') || parts.some(part => /^(\.git|\.env(?:\..*)?|id_rsa|id_ed25519|credentials(?:\..*)?)$/i.test(part)) || /\.(pem|key|p12|pfx)$/i.test(source);
    if (unsafe) throw new Error('This graph entry does not point to a safe project source file.');
    let root = '';
    try { root = changePath ? '' : localStorage.getItem(state.editorRootKey) || ''; } catch (_) { /* Ask again if local storage is unavailable. */ }
    if (!root) {
      root = window.prompt('Enter the absolute path to this project folder. The browser hides this path when you select a folder; Token Graph will remember it in this browser.');
      if (!root) throw new Error('Project folder path was not provided.');
      root = root.trim().replace(/[\\/]+$/, '');
      if (!root || !(/^(?:[a-z]:[\\/]|\\\\|\/)/i.test(root))) throw new Error('Enter an absolute project folder path, such as /Users/name/Projects/my-project.');
      try { localStorage.setItem(state.editorRootKey, root); } catch (_) { /* The path can still be used for this click. */ }
    }
    let fullPath = root.replace(/\\/g, '/');
    if (!fullPath.endsWith('/')) fullPath += '/';
    fullPath += source;
    const location = String(node.source_location || '').split(':').pop();
    const line = Math.max(1, Number(location.match(/\d+/)?.[0]) || 1);
    const target = `vscode://file${encodeURI(fullPath).replace(/#/g, '%23').replace(/\?/g, '%3F')}:${line}`;
    const link = document.createElement('a');
    link.href = target;
    link.rel = 'noopener';
    document.body.append(link);
    link.click();
    link.remove();
  }

  function select(node) {
    if (node?._cluster) { showClusterDetails(node._cluster); return; }
    state.selectedCluster = null;
    if (node) node = state.nodeById.get(node.id) || node;  // cluster views draw positioned copies
    if (node && clustersActive() && !state.expanded.has(grouping().key(node))) state.expanded.add(grouping().key(node));  // show the node, not its bubble
    state.selected = node || null; renderDetails(state.selected); draw(); }

  // "Copy context": the node, its connections and the loaded source lines, as compact text.
  function nodeContext(node) {
    const where = node.source_file ? `${node.source_file}${node.source_location ? ':' + node.source_location : ''}` : 'no source file';
    const lines = [
      `Project: ${$('#project-name').textContent}`,
      `Node: ${node.label} (${KIND_NAMES[node._kind] || node._kind})`,
      `Location: ${where}`,
      `Community: ${node.community_name || '-'} · Folder: ${folderOf(node)}`,
    ];
    if (node.description || node.rationale) lines.push(`Description: ${node.description || node.rationale}`);
    const edges = state.links.filter(l => l.source === node.id || l.target === node.id);
    lines.push('', `Connections (${edges.length}${edges.length > 60 ? ', first 60' : ''}):`);
    for (const l of edges.slice(0, 60)) {
      const other = state.nodeById.get(l.source === node.id ? l.target : l.source);
      const text = other ? `${other.label}${other.source_file ? ` (${other.source_file}${other.source_location ? ':' + other.source_location : ''})` : ''}` : (l.source === node.id ? l.target : l.source);
      lines.push(l.source === node.id ? `  --${l.relation || 'links'}--> ${text}` : `  <--${l.relation || 'links'}-- ${text}`);
    }
    const excerpt = $('#source-excerpt')?.textContent || '';
    if (excerpt && !/not embedded|could not be loaded|No source file/i.test(excerpt)) lines.push('', 'Source excerpt:', excerpt);
    return lines.join('\n');
  }
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (_) {
      const area = Object.assign(document.createElement('textarea'), { value: text });
      document.body.append(area); area.select();
      const ok = document.execCommand('copy'); area.remove();
      return ok;
    }
  }

