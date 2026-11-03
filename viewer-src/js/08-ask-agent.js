  // ---- ask bar: question / path A→B / impact, answered by scripts/ask_engine.py ----
  const ASK_MODES = {
    query: { a: 'e.g. how is partial cashout executed', pickA: false },
    path: { a: 'From: class, method or file', pickA: true },
    affected: { a: 'Class, method or file that would change', pickA: true },
    agent: { a: 'Find files, symbols, references, callers or source evidence', pickA: false },
    llm: { a: 'Ask the local model, e.g. how is a bet placed and where is the balance updated?', pickA: false },
  };
  let askMode = 'query';
  let agentHistory = [];
  let llmHistory = [];  // separate conversation for the local LLM
  let agentProject = null;
  let buildModelPickerOpen = false;

  function clearAgentChat() {
    agentHistory = []; llmHistory = []; agentProject = state.projectPath;
    $('#agent-chat').replaceChildren();
  }
  function appendAgentMessage(role, content, who = '') {
    const item = document.createElement('div'); item.className = `agent-message ${role}${who.startsWith('LLM') ? ' llm' : ''} engine-${askMode === 'llm' ? 'llm' : 'ai'}`;  // each engine keeps its own visible history
    const label = document.createElement('strong'); label.textContent = role === 'user' ? 'You' : who || 'Evidence';
    const body = document.createElement('div'); body.textContent = content;
    item.append(label, body); $('#agent-chat').append(item); item.scrollIntoView?.({block:'nearest'});
  }

  function setAskMode(mode) {
    askMode = mode;
    document.querySelectorAll('.ask-modes [data-mode]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
    document.querySelectorAll('[data-engine]').forEach(b => b.setAttribute('aria-pressed', String((b.dataset.engine === 'llm') === (mode === 'llm'))));
    $('.ask-modes').classList.toggle('hidden', mode === 'llm');
    $('#agent-chat').dataset.engine = mode === 'llm' ? 'llm' : 'ai';
    $('#llm-model').classList.toggle('hidden', mode !== 'llm');
    $('#ask-a').placeholder = ASK_MODES[mode].a;
    $('#ask-a-pick').classList.toggle('hidden', !ASK_MODES[mode].pickA);
    $('#ask-b-wrap').classList.toggle('hidden', mode !== 'path');
    $('#ask-depth').classList.toggle('hidden', mode !== 'affected');
    $('#ask-budget').classList.toggle('hidden', mode !== 'query');
    $('#agent-model').classList.toggle('hidden', mode !== 'agent' || !buildModelPickerOpen);
    $('#agent-build').classList.toggle('hidden', mode !== 'agent');
    $('#agent-chat').classList.toggle('hidden', mode !== 'agent' && mode !== 'llm');
    $('#agent-clear').classList.toggle('hidden', mode !== 'agent' && mode !== 'llm');
    if (mode === 'llm') $('#ask-hint').textContent = `Answered by a local model on this computer (${$('#llm-model').value || 'no model yet'}). It searches the graph, reads source and cites file:line; it can take a minute. Verify references before relying on them.`;
    else if (mode === 'agent') $('#ask-hint').textContent = 'Deterministic graph evidence with source citations. No Ollama model is used for search.';
    else $('#ask-hint').textContent = answerHint();
    $('#ask-a').focus();
  }
  function askScopeNote() {
    const skipped = [state.hideTests && 'tests', state.hideGenerated && 'generated code'].filter(Boolean);
    return skipped.length ? `Answers skip ${skipped.join(' and ')} (Hide buttons above the graph).` : 'Answers include tests and generated code.';
  }

  function answerHint() {
    return `${serverToken && state.projectPath ? 'Answers come from the local graph engine.' : 'Browser results use node-name matching and graph traversal; verify source code.'} ${askScopeNote()}`;
  }

  // Suggest node ids while typing a name; a picked suggestion sends the exact id, so it is never ambiguous.
  function suggestNodes(text) {
    const list = $('#ask-nodes');
    list.replaceChildren();
    const q = text.trim().toLowerCase();
    if (askMode === 'query' || q.length < 2 || state.nodeById.has(text.trim())) return;
    const hits = state.nodes.filter(n => passesFilters(n) && String(n.label).toLowerCase().includes(q))
      .sort((x, y) => String(x.label).length - String(y.label).length).slice(0, 25);
    for (const n of hits) {
      const option = document.createElement('option');
      option.value = n.id;
      option.label = `${n.label} — ${n.source_file || 'no file'}`;
      list.append(option);
    }
  }

  function clearHighlight() {
    state.highlight = null;
    state.highlightOnly = false;
    $('#ask-only').setAttribute('aria-pressed', 'false');
    $('#ask-result').classList.add('hidden');
    draw();
  }

  function showAskResult(result) {
    const nodes = new Map(Object.entries(result.nodes || {}).filter(([id]) => state.nodeById.has(id)).map(([id, level]) => [id, Number(level)]));
    const route = (result.route || []).filter(id => state.nodeById.has(id));
    const routeEdges = new Set(route.slice(1).map((id, i) => `${route[i]}|${id}`));
    const seeds = new Set((result.seeds || []).filter(id => state.nodeById.has(id)));
    state.highlight = nodes.size ? { nodes, seeds, routeEdges, labelIds: [...(route.length ? route : seeds)].slice(0, 14) } : null;
    $('#ask-result').classList.remove('hidden');
    $('#ask-text').textContent = result.text || '(no answer text)';
    const hidden = [...nodes.keys()].filter(id => !passesFilters(state.nodeById.get(id))).length;
    $('#ask-summary').innerHTML = nodes.size
      ? `<b>${format(nodes.size)}</b> node${nodes.size === 1 ? '' : 's'} highlighted${route.length ? ` · path of ${route.length - 1} step${route.length === 2 ? '' : 's'}` : ''}${hidden ? ` · ${hidden} hidden by filters` : ''}`
      : 'Nothing to highlight';
    // Ambiguous name: offer the candidates the engine listed as one-click choices.
    const choices = $('#ask-choices');
    choices.replaceChildren();
    if (result.ambiguous) {
      for (const [, id] of String(result.text).matchAll(/id: (\S+)/g)) {
        const node = state.nodeById.get(id);
        const button = document.createElement('button');
        button.textContent = node ? `${node.label} — ${node.source_file || id}` : id;
        button.addEventListener('click', () => {
          const field = result.ambiguous === 'target' ? '#ask-b' : '#ask-a';
          $(field).value = id;
          runAsk();
        });
        choices.append(button);
      }
    }
    // Nothing in the project matched the question: offer example questions as one-click choices.
    for (const example of result.suggestions || []) {
      const button = document.createElement('button');
      button.textContent = `${example.mode === 'affected' ? 'Impact' : 'Question'}: ${example.a}`;
      button.addEventListener('click', () => { setAskMode(example.mode); $('#ask-a').value = example.a; runAsk(); });
      choices.append(button);
    }
    state.focus = null; $('#focus-bar').classList.add('hidden');
    state.activeCommunity = null; renderCommunityList();
    if (state.highlight) fitGraph(new Set(nodes.keys())); else draw();
  }

  // Mirrors scripts/ask_engine.py: a question word counts only where a word of some symbol or file name starts
  // ("payout", "sentpayouts" do; "you" inside "PaYOUts" does not). With none, the answer says so and offers examples.
  const NAME_WORD = /[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+/g;
  const HELP_WORDS = new Set(['help', 'hint', 'hints', 'example', 'examples']);
  function codeNames(eligible) {
    const names = [];
    for (const node of state.nodes) {
      if (!eligible(node)) continue;
      for (const name of new Set([String(node.label || ''), String(node.source_file || '').split(/[\\/]/).pop()]))
        if (name && !name.includes(' ')) names.push([name.toLowerCase(), new Set([...name.matchAll(NAME_WORD)].map(m => m.index))]);
    }
    return names;
  }
  function namesCode(term, names) {
    if (/[_\d]/.test(term)) return true;  // an identifier typed as it is
    const forms = [term, ...(term.length > 3 && term.endsWith('s') ? [term.slice(0, -1)] : [])];
    return names.some(([name, starts]) => forms.some(form => {
      for (let at = name.indexOf(form); at !== -1; at = name.indexOf(form, at + 1)) if (starts.has(at)) return true;
      return false;
    }));
  }
  function exampleQuestions(eligible) {
    const degree = node => (state.adjacency.get(node.id) || []).length;
    const top = (pattern, nodes) => nodes.filter(n => n && eligible(n) && n.source_file && pattern.test(String(n.label || '')))
      .sort((x, y) => degree(y) - degree(x)).map(n => String(n.label).match(pattern)[1] || String(n.label));
    const cls = /^([A-Z][A-Za-z0-9_]{3,})$/, method = /^\.?([A-Za-z_]\w{3,})\(\)$/;
    const classes = top(cls, state.nodes);
    const owner = classes.length ? state.nodes.find(n => n.label === classes[0]) : null;
    const methods = (owner ? top(method, (state.adjacency.get(owner.id) || []).map(e => state.nodeById.get(e.nodeId))) : []).concat(top(method, state.nodes));
    const out = classes.length ? [{mode: 'query', a: `How does ${classes[0]} work?`}, {mode: 'affected', a: classes[0]}] : [];
    if (methods.length) out.splice(1, 0, {mode: 'query', a: `What calls ${methods[0]}?`});
    return out.slice(0, 3);
  }
  function noCodeAnswer(unknown, eligible) {
    const suggestions = exampleQuestions(eligible);
    const lines = [...(unknown.length ? [`No class, method or file in this project is named like: ${unknown.join(', ')}.`] : []),
      'Graph search finds code by its names. For questions in your own words, switch to LLM · local model.'];
    if (suggestions.length) lines.push('', 'Try one of these:', ...suggestions.map(s => `  ${s.mode === 'affected' ? 'Impact' : 'Question'}: ${s.a}`));
    return {text: lines.join('\n'), nodes: {}, seeds: [], suggestions};
  }

  function showServerInstructions() {
    const windows = /^win/i.test(navigator.userAgentData?.platform || navigator.platform || '');
    for (const name of ['codex','claude']) {
      $('#server-command-'+name).textContent = windows
        ? `python "$env:USERPROFILE\\.${name}\\skills\\token-graph\\scripts\\explorer_server.py" --no-browser`
        : `python3 "$HOME/.${name}/skills/token-graph/scripts/explorer_server.py" --no-browser`;
    }
    $('#server-setup').classList.remove('hidden');
    $('#server-setup').scrollIntoView?.({behavior:'smooth', block:'nearest'});
  }

  // File-mode queries use the loaded graph only; this is a small, deterministic
  // name-matching/traversal fallback, not the bundled Python engine's ranking.
  function browserAsk(request) {
    const eligible = node => node && !(state.hideTests && node._test) && !(state.hideGenerated && node._generated);
    const describe = node => `${node.label || node.id} [${node.source_file || 'no file'}${node.source_location ? ':' + node.source_location : ''}]`;
    const resolve = (query, role) => {
      const value = String(query || '').trim().toLowerCase();
      if (!value) return {error: `Enter ${role === 'target' ? 'a destination' : 'a node name'}.`, ambiguous: null};
      const candidates = state.nodes.filter(eligible);
      const clean = text => String(text || '').toLowerCase().replace(/\(\)$/, '');
      let matches = candidates.filter(n => String(n.id).toLowerCase() === value);
      if (!matches.length) matches = candidates.filter(n => clean(n.label) === clean(value));
      if (!matches.length) matches = candidates.filter(n => String(n.source_file || '').toLowerCase() === value);
      if (!matches.length) matches = candidates.filter(n => clean(n.label).includes(clean(value)));
      if (!matches.length) return {error:`No node matches '${query}'.`, ambiguous:null};
      if (matches.length > 1) return {error:`Ambiguous name '${query}':\n` + matches.slice(0,25).map(n => `  ${describe(n)}\n    id: ${n.id}`).join('\n'), ambiguous:role};
      return {node: matches[0]};
    };
    const neighbors = (id, mode) => {
      const edges = mode === 'directed' ? [...(state.outgoing.get(id) || []), ...(state.incoming.get(id) || []).filter(e => e.relation === 'contains')] : state.adjacency.get(id) || [];
      return edges.filter(e => eligible(state.nodeById.get(e.nodeId)));
    };
    if (request.mode === 'query') {
      const stop = new Set(['how','what','where','which','the','and','are','does','with','from','for','this','that','code','flow']);
      const words = (String(request.a || '').toLowerCase().match(/[a-z0-9_]+/g) || []).filter(t => t.length > 1 && !stop.has(t));
      if (!words.length) return {text:'Enter a class, function, file name or specific keywords.',nodes:{},seeds:[]};
      if (words.every(t => HELP_WORDS.has(t))) return noCodeAnswer([], eligible);
      const names = codeNames(eligible), terms = words.filter(t => namesCode(t, names));
      if (!terms.length) return noCodeAnswer(words, eligible);
      const rank = node => { const label=String(node.label||'').toLowerCase(), file=String(node.source_file||'').toLowerCase(), id=String(node.id).toLowerCase(); return terms.reduce((n,t)=>n+(label.includes(t)?4:0)+(id.includes(t)?2:0)+(file.includes(t)?1:0),0); };
      const seeds=state.nodes.filter(eligible).map(n=>({node:n,score:rank(n)})).filter(x=>x.score>0).sort((a,b)=>b.score-a.score).slice(0,3).map(x=>x.node.id);
      if (!seeds.length) return {text:'No matching nodes found. Try a class, function or file name.',nodes:{},seeds:[]};
      const levels=new Map(seeds.map(id=>[id,0])), queue=[...seeds];
      for (let i=0;i<queue.length;i++) {const id=queue[i],level=levels.get(id);if(level>=2)continue;for(const edge of neighbors(id,'any'))if(!levels.has(edge.nodeId)){levels.set(edge.nodeId,level+1);queue.push(edge.nodeId);}}
      const maxChars=Math.max(100,Number(request.budget)||800)*4;
      const lines=['Browser graph search (name matching; verify source code):']; const shown={}; let used=lines[0].length, truncated=false;
      for (const id of queue) {const n=state.nodeById.get(id),line=`NODE ${describe(n)} · id: ${id}`;if(used+line.length+1>maxChars){truncated=true;break;}lines.push(line);shown[id]=levels.get(id);used+=line.length+1;}
      const shownIds=new Set(Object.keys(shown));
      for(const edge of state.links){if(!shownIds.has(String(edge.source))||!shownIds.has(String(edge.target)))continue;const a=state.nodeById.get(edge.source),b=state.nodeById.get(edge.target),line=`EDGE ${a?.label||edge.source} --${edge.relation||'links'}--> ${b?.label||edge.target}`;if(used+line.length+1>maxChars){truncated=true;break;}lines.push(line);used+=line.length+1;}
      if(truncated) lines.push('TRUNCATED: increase the answer size or narrow the question.');
      return {text:lines.join('\n'),nodes:shown,seeds:seeds.filter(id=>Object.hasOwn(shown,id))};
    }
    const from=resolve(request.a,request.mode==='path'?'source':'node');
    if(from.error)return {text:from.error,nodes:{},seeds:[],ambiguous:from.ambiguous};
    if(request.mode==='path') {
      const to=resolve(request.b,'target');if(to.error)return {text:to.error,nodes:{},seeds:[],ambiguous:to.ambiguous};
      if(from.node.id===to.node.id)return {text:'Both names refer to the same node.',nodes:{},seeds:[]};
      const findRoute=mode=>{const seen=new Map([[from.node.id,null]]),q=[from.node.id];for(let i=0;i<q.length;i++){const id=q[i],level=seen.get(id)?.depth||0;if(level>=12)continue;for(const edge of neighbors(id,mode)){if(seen.has(edge.nodeId))continue;seen.set(edge.nodeId,{previous:id,depth:level+1,relation:edge.relation});if(edge.nodeId===to.node.id){const route=[edge.nodeId];while(seen.get(route[0]))route.unshift(seen.get(route[0]).previous);return {route,seen};}q.push(edge.nodeId);}}return null;};
      let found=findRoute('directed'),undirected=false;if(!found){found=findRoute('any');undirected=true;}
      if(!found)return {text:'No connection found within 12 steps. A missing path does not prove there is no relationship.',nodes:{},seeds:[]};
      const lines=[undirected?'No directed path; shortest connection ignoring direction:':'Directed path:'];
      for(let i=0;i<found.route.length;i++){const id=found.route[i];lines.push(`${i ? '  → ' : ''}${describe(state.nodeById.get(id))}${i ? ` (${found.seen.get(id).relation||'links'})` : ''}`);}
      return {text:lines.join('\n'),nodes:Object.fromEntries(found.route.map((id,i)=>[id,i])),seeds:[from.node.id,to.node.id],route:found.route};
    }
    if(request.mode==='affected') {
      const relations=new Set(['calls','indirect_call','references','imports','imports_from','dynamic_import','re_exports','inherits','extends','implements','uses','mixes_in','embeds','requires']);
      const maxDepth=Math.min(4,Math.max(1,Number(request.depth)||2));const levels=new Map([[from.node.id,0]]),q=[from.node.id];
      for(let i=0;i<q.length;i++){const id=q[i],level=levels.get(id);if(level>=maxDepth)continue;for(const edge of state.incoming.get(id)||[]){if(!relations.has(edge.relation)||!eligible(state.nodeById.get(edge.nodeId))||levels.has(edge.nodeId))continue;levels.set(edge.nodeId,level+1);q.push(edge.nodeId);}}
      const lines=[`Potential dependants of ${describe(from.node)}:`];const shown=q.slice(0,501);for(const id of shown.slice(1))lines.push(`  ${levels.get(id)} step(s): ${describe(state.nodeById.get(id))}`);if(q.length===1)lines.push('No indexed dependants found. Dynamic calls and external dependencies may be missing.');else if(q.length>shown.length)lines.push(`TRUNCATED: ${q.length-shown.length} additional nodes omitted; use a narrower scope.`);
      return {text:lines.join('\n'),nodes:Object.fromEntries(shown.map(id=>[id,levels.get(id)])),seeds:[from.node.id]};
    }
    return {text:'Unknown graph operation.',nodes:{},seeds:[]};
  }

  async function runAsk() {  // graph engine / evidence search only — no local LLM, so no "AI working" state
    if (askMode === 'agent' && !serverToken) { showServerInstructions(); return; }
    if (!state.nodes.length) { $('#ask-hint').textContent = 'Load a project graph first.'; return; }
    if (serverToken && !state.projectPath) { $('#ask-hint').textContent = 'Open a project graph first.'; return; }
    const request = { project: state.projectPath, mode: askMode, a: $('#ask-a').value, b: $('#ask-b').value, depth: $('#ask-depth').value,
      budget: $('#ask-budget').value, hideTests: state.hideTests, hideGenerated: state.hideGenerated };
    const go = $('#ask-go');
    if (askMode === 'llm') {
      if (!serverToken) { showServerInstructions(); return; }
      const model = $('#llm-model').value, question = request.a.trim(), project = state.projectPath;
      if (!model) { $('#ask-hint').textContent = 'No local chat model is installed. Install one, e.g. "ollama pull qwen3:8b", then reopen this page.'; return; }
      if (!question) { $('#ask-hint').textContent = 'Enter a question for the local model.'; return; }
      if (agentProject !== project) clearAgentChat();
      appendAgentMessage('user', question);
      $('#ask-a').value = '';
      go.disabled = true; go.textContent = 'Thinking…';
      $('#ask-hint').textContent = `${model} is searching the graph and reading source…`;
      const started = Date.now();
      setAiBusy(true);  // the real local-model work: chip and Ask border pulse (js/10b-shell.js)
      try {
        const result = await api('/api/llm', { project, question, model, history: llmHistory.slice(-12) });
        if (state.projectPath !== project) return;
        appendAgentMessage('assistant', result.text, `LLM · ${model}`);
        llmHistory.push({ role: 'user', content: question }, { role: 'assistant', content: String(result.text).slice(0, 8000) });
        $('#ask-hint').textContent = `Answered by ${model} in ${Math.round((Date.now() - started) / 1000)} s on this computer. Verify file:line references before relying on them.`;
      } catch (error) {
        const text = /unknown endpoint/i.test(error.message) ? 'This Token Graph server was started before LLM answers existed. Restart it: Settings → Stop server, then open Token Graph again with the desktop shortcut.' : error.message;
        appendAgentMessage('assistant', text, 'Error');
        $('#ask-hint').textContent = text;
      } finally { setAiBusy(false); go.disabled = false; go.textContent = 'Ask'; }
      return;
    }
    if (askMode === 'agent') {
      if (!serverToken) { showServerInstructions(); return; }
      if (!request.a.trim()) { $('#ask-hint').textContent = 'Enter a search term or question.'; return; }
      const project = state.projectPath;
      if (agentProject !== project) clearAgentChat();
      go.disabled = true; go.textContent = 'Searching…';
      $('#ask-hint').textContent = 'Searching the local graph and checking source locations…';
      try {
        const history = agentHistory.slice(-12);
        const result = await api('/api/agent', {project, question: request.a, operation: 'query', limit: 10, hideTests: state.hideTests, hideGenerated: state.hideGenerated, history});
        if (state.projectPath !== project) return;
        appendAgentMessage('user', request.a);
        appendAgentMessage('assistant', result.text);
        agentHistory.push({role:'user', content:request.a}, {role:'assistant', content:result.text.slice(0,8000)});
        $('#ask-a').value = '';
        $('#ask-hint').textContent = 'Evidence retrieved locally. Each result identifies its source type and verification status.';
      } catch (error) { $('#ask-hint').textContent = error.message; }
      finally { go.disabled = false; go.textContent = 'Ask'; }
      return;
    }
    go.disabled = true; go.textContent = 'Asking…';
    $('#ask-hint').textContent = 'Searching the graph…';
    try {
      showAskResult(serverToken && state.projectPath ? await api('/api/ask', request) : browserAsk(request));
      $('#ask-hint').textContent = answerHint();
    } catch (error) {
      $('#ask-hint').textContent = error.message;
    } finally {
      go.disabled = false; go.textContent = 'Ask';
    }
  }

  async function checkAgent() {
    const select = $('#agent-model');
    select.replaceChildren(new Option('Checking Ollama…', ''));
    if (!serverToken) { $('#ask-hint').textContent = 'Start the local server to use the agent.'; return; }
    try {
      const {models} = await api('/api/agent/status');
      select.replaceChildren();
      if (!models.length) { select.add(new Option('No local models installed', '')); $('#ask-hint').textContent = 'Install a local model in Ollama to generate a semantic graph.'; return; }
      for (const model of models) select.add(new Option(model, model));
      if (models.includes('qwen3:8b')) select.value = 'qwen3:8b';
      $('#ask-hint').textContent = 'Ollama is ready for optional AI graph generation.';
    } catch (error) { select.replaceChildren(new Option('Ollama unavailable', '')); $('#ask-hint').textContent = error.message; }
  }

  document.querySelectorAll('.ask-modes [data-mode]').forEach(b => b.addEventListener('click', () => setAskMode(b.dataset.mode)));
  $('#ask-go').addEventListener('click', runAsk);
  $('#agent-clear').addEventListener('click', clearAgentChat);
  $('#server-connect').addEventListener('click', () => {
    try {
      const url = new URL($('#server-address').value.trim());
      if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname) || !url.searchParams.get('token')) throw new Error();
      location.href = url.href;
    } catch (_) { $('#server-connect-error').textContent = 'Enter the full local server address, including its token.'; }
  });
  for (const id of ['#ask-a', '#ask-b']) {
    $(id).addEventListener('keydown', event => { if (event.key === 'Enter') runAsk(); });
    $(id).addEventListener('input', event => suggestNodes(event.target.value));
  }
  $('#ask-a-pick').addEventListener('click', () => { if (state.selected) $('#ask-a').value = state.selected.id; });
  $('#ask-b-pick').addEventListener('click', () => { if (state.selected) $('#ask-b').value = state.selected.id; });
  $('#ask-clear').addEventListener('click', clearHighlight);
  $('#ask-only').addEventListener('click', () => {
    state.highlightOnly = !state.highlightOnly;
    $('#ask-only').setAttribute('aria-pressed', String(state.highlightOnly));
    fitGraph(state.highlight ? new Set(state.highlight.nodes.keys()) : null);
  });
  $('#ask-copy').addEventListener('click', async () => {
    $('#ask-copy').textContent = await copyText($('#ask-text').textContent) ? 'Copied ✓' : 'Copy failed';
    setTimeout(() => { $('#ask-copy').textContent = 'Copy text'; }, 1500);
  });

