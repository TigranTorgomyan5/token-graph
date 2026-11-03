  // ---- graph freshness (7) and changes since the last build (9) ----
  const when = seconds => new Date(seconds * 1000).toLocaleString();
  async function checkStale() {
    const chip = $('#stale-chip');
    if (!serverToken) { chip.textContent = 'Freshness: use local server'; chip.title = 'Open server instructions to check graph freshness.'; chip.dataset.serverHelp = '1'; chip.dataset.stale = ''; chip.classList.remove('hidden'); return; }
    if (!state.projectPath) { chip.classList.add('hidden'); return; }
    chip.dataset.serverHelp = '';
    try {
      const s = await api(`/api/stale?project=${encodeURIComponent(state.projectPath)}`);
      const n = s.changed + s.deleted;
      chip.dataset.stale = n ? '1' : '';
      chip.classList.toggle('fresh', !n);
      chip.textContent = n ? `${format(n)} file${n === 1 ? '' : 's'} changed since this graph · Update` : 'Graph is up to date';
      chip.title = n ? `Graph built ${when(s.built)}. Changed or deleted since:\n${s.examples.join('\n')}${n > s.examples.length ? '\n…' : ''}\n\nClick to update the graph.` : `Graph built ${when(s.built)}; no source file changed since.`;
      chip.classList.remove('hidden');
    } catch (_) { chip.classList.add('hidden'); }
  }
  $('#stale-chip').addEventListener('click', () => { if ($('#stale-chip').dataset.serverHelp) showServerInstructions(); else if ($('#stale-chip').dataset.stale && state.projectPath) buildProject(state.projectPath); });

  async function showDiff() {
    if (!serverToken) { showServerInstructions(); return; }
    if (!state.projectPath) { showAskResult({text:'Load a project through the local server to compare graph versions.',nodes:{}}); return; }
    let d;
    try { d = await api(`/api/diff?project=${encodeURIComponent(state.projectPath)}`); }
    catch (error) { $('#ask-hint').textContent = error.message; return; }
    if (!d.available) {
      showAskResult({ text: 'No earlier version of this graph is stored yet.\nFrom the next “Update current graph” (or rebuild) on, the previous version is kept and this shows what changed.', nodes: {} });
      return;
    }
    const list = (items, sign) => items.map(n => `  ${sign} ${n.label}${n.file ? ` — ${n.file}` : ''}`).join('\n');
    const text = [
      `Changes between graph builds`,
      `Previous: ${when(d.previous_built)}   Current: ${when(d.built)}`,
      '',
      `New nodes: ${format(d.added_count)}   Removed nodes: ${format(d.removed_count)}   Connections: +${format(d.links_added)} / -${format(d.links_removed)}`,
      d.added_count ? `\nNew (${format(d.added_count)}${d.added_count > d.added.length ? `, first ${d.added.length}` : ''}):\n${list(d.added, '+')}` : '',
      d.removed_count ? `\nRemoved (${format(d.removed_count)}${d.removed_count > d.removed.length ? `, first ${d.removed.length}` : ''}):\n${list(d.removed, '-')}` : '',
      d.links_added_sample.length ? `\nNew connections (sample):\n${d.links_added_sample.map(s => '  + ' + s).join('\n')}` : '',
      d.links_removed_sample.length ? `\nRemoved connections (sample):\n${d.links_removed_sample.map(s => '  - ' + s).join('\n')}` : '',
      !d.added_count && !d.removed_count && !d.links_added && !d.links_removed ? '\nNothing changed in the graph.' : '',
    ].filter(Boolean).join('\n');
    // New nodes are ringed; existing nodes whose connections changed are lit without a ring.
    const nodes = Object.fromEntries([...d.touched.map(id => [id, 1]), ...d.added.map(n => [n.id, 0])]);
    showAskResult({ text, nodes, seeds: d.added.map(n => n.id) });
  }
  $('#diff-button').addEventListener('click', showDiff);

