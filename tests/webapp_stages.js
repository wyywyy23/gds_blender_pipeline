const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=process.env.GDS_STUDIO_ROOT||path.resolve(__dirname,'..');
const specs=JSON.parse(process.argv[2]);
const ids=new Map(),timers=new Map();let sequence=0;
class Element {
  constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.style={};this.classList={toggle(){}};this.checked=false;this.listeners={};}
  set id(value){this._id=value;ids.set(value,this);}get id(){return this._id;}
  append(...items){this.children.push(...items);for(const child of items)if(child instanceof Element)child.parent=this;}
  replaceChildren(...items){this.children=[];this.append(...items);}
  addEventListener(name,fn){this.listeners[name]=fn;}
  setCustomValidity(value){this.validity=value;}
  querySelectorAll(selector){const tags=selector.split(',');return this.children.flatMap(c=>c instanceof Element?[...(tags.includes(c.tag)?[c]:[]),...c.querySelectorAll(selector)]:[]);}
  querySelector(selector){return this.querySelectorAll(selector)[0];}
}
const document={getElementById(id){if(!ids.has(id)){const el=new Element();el.id=id;}return ids.get(id);},createElement(tag){return new Element(tag);},createTextNode(text){return text;}};
const context={document,setTimeout(fn){const id=++sequence;timers.set(id,fn);return id;},clearTimeout(id){timers.delete(id);}};vm.createContext(context);
const source=fs.readFileSync(path.join(root,'webapp/static/app.js'),'utf8');const stop=source.lastIndexOf('\ntry{const status=await api(');assert.ok(stop>0);
vm.runInContext(source.slice(0,stop)+'\nglobalThis.app={state,renderOptions,updateButtons,geometrySignature,previewSignature,resetStage,payload};',context);
const a=context.app,el=id=>document.getElementById(id),defaults=()=>Object.fromEntries(specs.map(s=>[s.key,s.default]));
a.state.specs=specs;a.state.options=defaults();a.state.ready=a.state.buildReady=a.state.three=true;a.state.layout={id:'test'};
function ready(){
 a.state.options=defaults();a.state.bounds=[[0,0,0],[10,10,10]];a.state.previewBounds=a.state.bounds;a.state.previewZScale=1;a.state.meshes=[{scale:{z:1}}];
 a.state.visual={visual_id:'v0005',visual_fingerprint:'checked'};a.state.checkedFingerprint='checked';a.state.visualSignature=a.geometrySignature();a.state.signature=a.previewSignature();a.state.job=null;a.state.invalidResolution=false;
 timers.clear();a.renderOptions();a.updateButtons();assert.equal(el('build').disabled,false);
}
function change(spec){const field=el('option-'+spec.key);if(typeof spec.default==='boolean')field.checked=!spec.default;else if(spec.choices)field.value=spec.choices.find(v=>v!==spec.default);else field.value=String(spec.default+(spec.step||1));field.oninput();}
const containers={visual:'visual-options',scene:'options',preset:'preset-options',preview:'preview-options'};
ready();
for(const spec of specs){let node=el('option-'+spec.key);while(node&&!Object.values(containers).includes(node.id))node=node.parent;assert.equal(node?.id,containers[spec.stage],spec.key+' placement');}
for(const spec of specs){
 ready();const signature=a.geometrySignature();change(spec);
 assert.equal(el('build').disabled,spec.stage==='visual',spec.key+' build gate');
 assert.equal(a.geometrySignature()===signature,spec.stage!=='visual',spec.key+' visual identity');
 assert.equal(timers.size>0,spec.stage==='visual',spec.key+' visual check');
 if(spec.stage==='preview')assert.equal(el('preview').textContent,'Refresh preview');
 if(spec.stage==='visual')assert.equal(el('preview').textContent,'Refresh visual GDS & preview');
 assert.equal(a.payload().options[spec.key],a.state.options[spec.key]);
}
ready();const z=el('option-z_scale');z.value='2';z.oninput();assert.equal(a.state.meshes[0].scale.z,2);assert.equal(a.state.bounds[1][2],20);z.value='3';z.oninput();assert.equal(a.state.meshes[0].scale.z,3);assert.equal(a.state.bounds[1][2],30);assert.equal(el('build').disabled,false);
ready();a.state.options.fill_cheese=true;a.state.options.metal_bevel=false;a.state.options.samples=2048;a.state.options.preview_limit=500000;a.resetStage('scene');assert.equal(a.state.options.metal_bevel,true);assert.equal(a.state.options.fill_cheese,true);assert.equal(a.state.options.samples,2048);assert.equal(a.state.options.preview_limit,500000);
ready();a.state.options.fill_cheese=true;a.state.options.samples=2048;a.resetStage('preset');assert.equal(a.state.options.fill_cheese,true);assert.equal(a.state.options.samples,1024);
ready();a.state.job='busy';a.updateButtons();for(const spec of specs)assert.equal(el('option-'+spec.key).disabled,true);for(const id of ['reset-visual-options','reset-options','reset-preset-options','reset-preview-options'])assert.equal(el(id).disabled,true);
console.log(JSON.stringify({options:specs.length,allStages:true,sceneBuildWithoutRefresh:true,previewOnlyDoesNotBlockBuild:true,localScale:true,scopedDefaults:true}));
