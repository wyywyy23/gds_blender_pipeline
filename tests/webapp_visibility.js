const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = process.env.GDS_STUDIO_ROOT || path.resolve(__dirname, '..');

// Exercise the app's real preset, mesh, checkbox and payload functions. Only the
// DOM/Three rendering surfaces and the network startup are replaced in this test.
class Element {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.dataset = {}; this.style = {}; this.classList = {toggle() {}}; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = [...items]; }
  addEventListener() {}
  querySelectorAll(selector) {
    const tags = selector.split(',');
    return this.children.flatMap(child => child instanceof Element ? [...(tags.includes(child.tag) ? [child] : []), ...child.querySelectorAll(selector)] : []);
  }
  querySelector(selector) {
    if (selector === '.swatch') return this.children.find(x => x.className === 'swatch');
    return this.querySelectorAll(selector)[0];
  }
}
function app() {
  const ids = new Map();
  const document = {
    getElementById(id) { if (!ids.has(id)) ids.set(id, new Element()); return ids.get(id); },
    createElement(tag) { return new Element(tag); },
    createTextNode(text) { return text; },
  };
  const context = {document, setTimeout() {}, clearTimeout() {}};
  vm.createContext(context);
  const source = fs.readFileSync(path.join(root, 'webapp/static/app.js'), 'utf8');
  const startup = source.lastIndexOf('\ntry{const status=await api(');
  assert.ok(startup > 0, 'locate the app network startup');
  vm.runInContext(source.slice(0, startup) + '\nglobalThis.testApp={state,applyPreset,loadMesh,payload,normalizeLayerName,setGraphics(value){T=value;scene={add(){},remove(){}};}};', context);
  const a = context.testApp;
  a.setGraphics({
    BufferGeometry: class {setAttribute() {} computeVertexNormals() {} dispose() {}},
    Float32BufferAttribute: class {},
    MeshStandardMaterial: class {constructor() {this.color={setRGB(){},getHexString(){return 'ffffff';}};} dispose() {}},
    Mesh: class {constructor(geometry,material) {Object.assign(this,{geometry,material,userData:{}});}},
    DoubleSide: 2,
  });
  a.state.options = {scheme:'realistic',width:3200,height:2000};
  return {...a, document, ids};
}
const fixture = hidden => ({file:'legacy/ramzi_oblique_100mm.yaml',etag:'test',preset:{
  name:'ramzi_oblique_100mm',camera:{type:'PERSP',location:[-110,-260,400],rotation_degrees:[35,0,-25],lens_mm:100,sensor_width_mm:36},
  render:{samples:1024},runs:[{name:'no_cladding',hide_layers:hidden},{name:'all_layers',hide_layers:[]}],
}});
const mesh = {bounds:[[0,0,0],[10,10,10]],triangles:2,units:'µm',notes:'test',meshes:['CLADDING_RENDER','M1AM_RENDER'].map(name=>({name,positions:[0,0,0,1,0,0,0,1,0],colors:{realistic:[1,1,1,1]}}))};
function checkbox(a) { return a.ids.get('layers').querySelectorAll('label').find(row=>row.dataset.name==='CLADDING_RENDER').querySelector('input'); }
function plain(value) { return JSON.parse(JSON.stringify(value)); }
const parity = JSON.parse(process.argv[2] || '{"cladding":"CLADDING_RENDER","LCLADDING_RENDER":"CLADDING_RENDER","M1AM":"M1AM_RENDER"}');
for (const [alias,expected] of Object.entries(parity)) assert.equal(app().normalizeLayerName(alias),expected,alias);
let captured;
for (const alias of ['cladding','CLADDING_RENDER','LCLADDING_RENDER',' cladding ','cladding-render']) {
  for (const presetFirst of [false,true]) {
    const a=app(); const value=fixture([alias,alias]); const original=JSON.stringify(value);
    if (presetFirst) a.applyPreset(value);
    a.loadMesh(mesh,'geometry');
    if (!presetFirst) a.applyPreset(value);
    assert.equal(checkbox(a).checked,false,`${alias}: unchecked after loading preset`);
    assert.equal(a.state.meshes[0].visible,false);
    assert.deepEqual(plain(a.payload().hidden_layers),['CLADDING_RENDER']);
    assert.equal(JSON.stringify(value),original,'original preset is immutable');
    checkbox(a).checked=true;checkbox(a).onchange();
    assert.equal(a.state.meshes[0].visible,true);
    assert.deepEqual(plain(a.payload().hidden_layers),[],'checking cladding removes every alias');
    assert.deepEqual(plain(a.payload().base.runs[0].hide_layers),[alias,alias],'base preset may retain aliases; current checkbox is authoritative');
    a.loadMesh(mesh,'refreshed');
    assert.equal(checkbox(a).checked,true,'preview refresh preserves the user toggle');
    checkbox(a).checked=false;checkbox(a).onchange();
    assert.deepEqual(plain(a.payload().hidden_layers),['CLADDING_RENDER']);
    const hidden=plain(a.payload());
    checkbox(a).checked=true;checkbox(a).onchange();
    captured={hidden,enabled:plain(a.payload())};
  }
}
const a=app();a.loadMesh(mesh,'geometry');a.applyPreset(fixture(['cladding','m1am']));
checkbox(a).checked=true;checkbox(a).onchange();
assert.deepEqual(plain(a.payload().hidden_layers),['M1AM_RENDER'],'showing cladding preserves another hidden layer');
assert.equal(a.state.meshes[1].visible,false);
a.applyPreset(fixture([]));assert.equal(checkbox(a).checked,true);assert.deepEqual(plain(a.payload().hidden_layers),[]);
console.log(JSON.stringify({cases:10,parityCases:Object.keys(parity).length,...captured}));
