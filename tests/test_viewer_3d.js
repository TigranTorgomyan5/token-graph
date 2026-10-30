const fs = require('fs'), vm = require('vm'), assert = require('assert');
const root = require('path').resolve(__dirname, '..');
async function main() {
for (const name of ['index.html', 'token-graph/assets/project-explorer.html', 'token-graph/assets/graph-viewer.html']) {
  const html = fs.readFileSync(require('path').join(root, name), 'utf8');
  let code = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]).find(s => s.includes('(() =>'));
  new vm.Script(code);
  code = code.replace(/\}\)\(\);\s*$/, 'globalThis.viewerTest = {state, normalizeGraph, projectNode, findNode, fitGraph, setDimension, select, draw, setView, recoverGraph, browserAsk, runAsk, setAskMode, importProjectFolder, switchGraph}; })();');
  const elements = new Map(), arcs = [];
  const context = new Proxy({measureText:t=>({width:String(t).length*7}), arc:(...args)=>arcs.push(args)}, {get:(target,k)=>target[k] || (()=>{})});
  function el(key) {
    if (!elements.has(key)) {
      const classes = new Set(), handlers = {};
      elements.set(key, {value:'',textContent:'',innerHTML:'',dataset:{},style:{},width:1000,height:650,clientWidth:1000,clientHeight:650,handlers,
        classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle:(x,on)=>{if(on)classes.add(x);else classes.delete(x)}},
        addEventListener:(event,handler)=>{handlers[event]=handler},setAttribute(){},append(){},appendChild(){},replaceChildren(){},click(){},getContext:()=>context,
        getBoundingClientRect:()=>({left:0,top:0,width:1000,height:650}),setPointerCapture(){},focus(){},querySelector:s=>el(key+' '+s),querySelectorAll:()=>[]});
    }
    return elements.get(key);
  }
  const sandbox = {Option:function(text,value){this.text=text;this.value=value},console,Intl,Map,Set,URLSearchParams,location:{protocol:'file:',search:''},navigator:{},localStorage:{getItem:()=>null,setItem(){}},window:{devicePixelRatio:1,addEventListener(){}},
    document:{querySelector:el,querySelectorAll:()=>[],createElement:()=>el('created'+elements.size),addEventListener(){},activeElement:{tagName:'BODY'}},setInterval(){},clearInterval(){}};
  vm.createContext(sandbox);vm.runInContext(code,sandbox);
  const api=sandbox.viewerTest;
  const fixture={nodes:Array.from({length:60},(_,i)=>({id:'node'+i,label:'Symbol'+i,source_file:'src/'+i+'.py',community:i%4})),edges:Array.from({length:59},(_,i)=>({source:'node'+i,target:'node'+(i+1),relation:'calls'}))};
  for(let i=20;i<40;i++) fixture.edges.push({source:"node0",target:"node"+i,relation:"calls"});
  api.normalizeGraph(fixture);
  const search=api.browserAsk({mode:'query',a:'Symbol10',budget:400});
  assert(search.text.includes('Symbol10')); assert(Object.keys(search.nodes).length>0);
  const truncated=api.browserAsk({mode:'query',a:'Symbol',budget:100});
  assert(truncated.text.includes('TRUNCATED')); assert(truncated.text.length < 500);
  const path=api.browserAsk({mode:'path',a:'node0',b:'node3'});
  assert.equal(path.route.join(','),'node0,node1,node2,node3');
  const reversed=api.browserAsk({mode:'path',a:'node3',b:'node0'});
  assert(reversed.text.includes('ignoring direction')); assert.equal(reversed.route.join(','),'node3,node2,node1,node0');
  const impact=api.browserAsk({mode:'affected',a:'node3',depth:2});
  assert(Object.hasOwn(impact.nodes,'node2')); assert(Object.hasOwn(impact.nodes,'node1')); assert(!Object.hasOwn(impact.nodes,'node4'));
  assert(api.browserAsk({mode:'query',a:'what how',budget:400}).text.includes('specific keywords'));
  el('#ask-a').value='Symbol10'; await api.runAsk(); assert(el('#ask-text').textContent.includes('Symbol10')); assert(api.state.highlight.nodes.size>0);
  el('#server-setup').classList.add('hidden'); el('#build-project').handlers.click(); assert(!el('#server-setup').classList.contains('hidden'));
  assert.equal(api.state.is3d,true);
  api.setView('docs');
  assert.equal(el('#graph-empty').classList.contains('hidden'),false);
  assert.equal(el('#graph-empty-title').textContent,'No documentation indexed');
  api.recoverGraph();
  assert.equal(api.state.view,'all');
  assert.equal(el('#graph-empty').classList.contains('hidden'),true);

  assert(api.state.nodes.every(n=>Number.isFinite(n._x3)&&Number.isFinite(n._y3)&&Number.isFinite(n._z3)));
  assert(new Set(api.state.nodes.map(n=>n._z3)).size>1);
  const sample=api.state.nodes[0],before=api.projectNode(sample);
  api.state.yaw+=.7;const rotated=api.projectNode(sample);assert(Math.abs(before.x-rotated.x)>1);
  const eventAt=p=>({clientX:500+api.state.x+p.x*api.state.scale,clientY:325+api.state.y+p.y*api.state.scale});
  assert.equal(api.findNode(eventAt(rotated)).id,sample.id);
  const canvas=el('#graph-canvas'),down={clientX:300,clientY:300,button:0,pointerId:1,shiftKey:false};
  api.setAskMode('path'); el('#ask-a').value='node0'; el('#ask-b').value='node3';
  await api.runAsk(); assert(el('#ask-text').textContent.includes('Directed path')); assert(api.state.highlight.routeEdges.has('node0|node1'));
  api.setAskMode('affected'); el('#ask-a').value='node3'; el('#ask-depth').value='2';
  await api.runAsk(); assert(el('#ask-text').textContent.includes('Potential dependants')); assert(api.state.highlight.nodes.has('node2'));
  api.setAskMode('query');
  const oldYaw=api.state.yaw;canvas.handlers.pointerdown(down);canvas.handlers.pointermove({...down,clientX:340});assert(api.state.yaw!==oldYaw);canvas.handlers.pointerup({...down,clientX:340});
  const oldX=api.state.x;canvas.handlers.pointerdown({...down,shiftKey:true});canvas.handlers.pointermove({...down,clientX:320});assert.equal(api.state.x,oldX+20);canvas.handlers.pointercancel();assert.equal(api.state.dragging,false);
  const oldScale=api.state.scale;canvas.handlers.wheel({...down,deltaY:-100,preventDefault(){}});assert(api.state.scale>oldScale);
  api.setDimension(false);const flat=api.projectNode(sample);assert.equal(flat.x,sample.x);assert.equal(flat.y,sample.y);assert.equal(flat.perspective,1);
  assert.equal(api.findNode(eventAt(flat)).id,sample.id);
  api.setDimension(true);api.select(sample);api.draw();assert(api.state.selected.id===sample.id);
  assert(arcs.length>0 && arcs.every(a=>a.slice(0,3).every(Number.isFinite)));
  api.normalizeGraph({nodes:[{id:'a',label:'Duplicate',source_file:'src/a.py',community:0},{id:'b',label:'Duplicate',source_file:'tests/test_b.py',community:0},{id:'c',label:'GeneratedOnly',source_file:'gen/code.py',community:0}],edges:[{source:'a',target:'c',relation:'calls'},{source:'b',target:'a',relation:'calls'}]});
  assert.equal(api.browserAsk({mode:'affected',a:'Duplicate'}).ambiguous,'node');
  api.state.hideTests=true;
  assert(!api.browserAsk({mode:'affected',a:'Duplicate'}).ambiguous);
  assert(api.browserAsk({mode:'path',a:'a',b:'GeneratedOnly'}).text.includes('No node matches'));
  api.state.hideGenerated=false;
  assert.equal(api.browserAsk({mode:'path',a:'a',b:'GeneratedOnly'}).route.join(','),'a,c');
  api.state.hideTests=false;
  api.normalizeGraph({nodes:[{id:1,label:'First',community:0},{id:2,label:'Second',community:0}],edges:[{source:1,target:2,relation:'calls'}]});
  assert.equal(api.browserAsk({mode:'path',a:'1',b:'2'}).route.join(','),'1,2');
  api.setAskMode('query'); el('#ask-a').value='First'; await api.runAsk(); assert(api.state.highlight.nodes.has('1'));
  let secretReads=0;
  const localGraph={nodes:[{id:'src',label:'Source',source_file:'src/main.py',source_location:'L3',community:0},{id:'secret',label:'Secret',source_file:'.env',community:0}],edges:[]};
  const files=[{webkitRelativePath:'Projects/demo/graphify-out/graph.json',text:async()=>JSON.stringify(localGraph)},
    {webkitRelativePath:'Projects/demo/src/main.py',size:40,text:async()=> 'line1\nline2\nselected source\nline4'},
    {webkitRelativePath:'Projects/demo/.env',size:20,text:async()=>{secretReads++;return 'SECRET';}}];
  await api.importProjectFolder(files);
  api.select(api.state.nodeById.get('src')); await Promise.resolve();
  assert(el('#source-excerpt').textContent.includes('selected source'));
  api.select(api.state.nodeById.get('secret')); await Promise.resolve();
  assert.equal(secretReads,0); assert(el('#source-excerpt').textContent.includes('unavailable'));
  api.normalizeGraph({nodes:[{id:'single',label:'Single',community:0}],edges:[]});assert(Number.isFinite(api.state.scale));assert(Number.isFinite(api.projectNode(api.state.nodes[0]).x));
  for (const path of process.argv.slice(2)) {
    const graph=JSON.parse(fs.readFileSync(path,'utf8'));
    api.normalizeGraph(graph);
    const points=api.state.nodes.filter(n=>!n._generated).map(api.projectNode);
    assert(points.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)),path+' non-finite projection');
    const inView=points.filter(p=>{const x=500+api.state.x+p.x*api.state.scale,y=325+api.state.y+p.y*api.state.scale;return x>=0&&x<=1000&&y>=0&&y<=650});
    assert(!points.length||inView.length>0,path+' all nodes off screen');
    console.log('  Real graph: '+graph.nodes.length+' nodes; '+inView.length+' projected in view');
  }
  console.log(name+': full initialization, 3D layout/projection, rotation, picking, pan, zoom, 2D fallback, selection and single-node checks passed');
}

}
main().catch(error=>{console.error(error);process.exit(1)});
