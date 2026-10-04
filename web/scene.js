import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';

// Schematic geometry, never a surveyed route. All animated streams use supplied records.
const C={paper:0xf3f0e8,ink:0x172a2c,stone:0xd5d0c2,light:0xebe5d8,road:0xc6c4b9,copper:0xb86237,hot:0xe28b4b,teal:0x39767a,cool:0x73a2a1,glass:0x779498,forest:0x7d8b61,brass:0xb79867};
const clamp=(x,lo=0,hi=1)=>Math.max(lo,Math.min(hi,Number(x)||0));
const v3=p=>new THREE.Vector3(...p);
const finite=(x,f=0)=>Number.isFinite(+x)?Math.max(0,+x):f;
const mw=x=>`${finite(x).toFixed(2)} MW`;

export class ThermalScene {
  constructor(container,{onSelect=()=>{}}={}) {
    this.container=container;this.onSelect=onSelect;this.site='U';this.selected='source';
    this.state={site:'U',hour:14,month:10,ambient_c:14,it_mw:0,cooling_mw:0,other_mw:0,source_mw:0,hp_mw:0,heat_electricity_mw:0,delivered:[0,0,0],demand:[0,0,0],charge_mw:0,discharge_mw:0,soc_mwh:0,storage_capacity_mwh:1,rejected_mw:0};
    this.cutaway=true;this.disposed=false;this.time=0;this.dirty=true;this.visible=true;
    this.motionQuery=matchMedia('(prefers-reduced-motion: reduce)');this.reduced=this.motionQuery.matches;
    this.motionHandler=e=>{this.reduced=e.matches;this.dirty=true;};this.motionQuery.addEventListener('change',this.motionHandler);
    this.pickables=[];this.labels=[];this.routes=[];this.targets={};this.mats=new Map();this.resources=new Set();
    this.raycaster=new THREE.Raycaster();this.pointer=new THREE.Vector2();this.projected=new THREE.Vector3();
    this.installStyles();container.classList.add('thermal-world');container.dataset.scene='schematic';
    this.overlay=document.createElement('div');this.overlay.className='thermal-label-layer';container.append(this.overlay);
    try{this.renderer=new THREE.WebGLRenderer({antialias:true,alpha:true,powerPreference:'high-performance'});}catch(error){this.showFallback('The 3D view needs WebGL. All forecasts, dispatch decisions and system balances remain available below.');return;}
    this.renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.6));this.renderer.shadowMap.enabled=true;this.renderer.shadowMap.type=THREE.PCFSoftShadowMap;this.renderer.shadowMap.autoUpdate=false;
    this.renderer.toneMapping=THREE.ACESFilmicToneMapping;this.renderer.toneMappingExposure=1.2;this.renderer.outputColorSpace=THREE.SRGBColorSpace;
    this.canvas=this.renderer.domElement;this.canvas.className='thermal-canvas';this.canvas.tabIndex=0;
    this.canvas.setAttribute('aria-label','Interactive thermal district. Drag or use arrow keys to orbit. Scroll or use plus and minus to zoom. Home resets the view. Select a numbered building to inspect energy flow; double-click a building to focus.');container.prepend(this.canvas);
    this.scene=new THREE.Scene();this.scene.background=new THREE.Color(C.paper);this.scene.fog=new THREE.Fog(C.paper,210,400);
    this.camera=new THREE.PerspectiveCamera(34,1,.5,600);this.camera.position.set(92,96,120);
    this.controls=new OrbitControls(this.camera,this.canvas);this.controls.target.set(0,1,0);
    Object.assign(this.controls,{enableDamping:true,dampingFactor:.065,minDistance:36,maxDistance:245,minPolarAngle:.25,maxPolarAngle:1.36,enablePan:true,screenSpacePanning:false,zoomSpeed:.75,rotateSpeed:.62});
    this.controls.addEventListener('change',()=>{this.dirty=true;});this.controls.addEventListener('start',()=>{this.cameraMove=null;});
    this.hemisphere=new THREE.HemisphereLight(0xfff8e8,0x777e6f,2.1);this.scene.add(this.hemisphere);
    this.sun=new THREE.DirectionalLight(0xffecc8,3.5);this.sun.position.set(-45,85,55);this.sun.castShadow=true;
    this.sun.shadow.mapSize.set(2048,2048);Object.assign(this.sun.shadow.camera,{left:-75,right:75,top:70,bottom:-70,near:1,far:220});this.sun.shadow.bias=-.0003;this.sun.shadow.normalBias=.14;this.sun.shadow.radius=3;this.scene.add(this.sun);
    this.fillLight=new THREE.DirectionalLight(0xd8e8e8,1.15);this.fillLight.position.set(50,30,-40);this.scene.add(this.fillLight);
    this.world=new THREE.Group();this.scene.add(this.world);this.boxGeo=new THREE.BoxGeometry(1,1,1);this.cylGeo=new THREE.CylinderGeometry(1,1,1,24);this.sphereGeo=new THREE.SphereGeometry(1,14,9);this.leafGeo=new THREE.IcosahedronGeometry(1,1);
    this.groundMat=this.mat(C.light,{roughness:1});this.foliageMat=this.mat(C.forest,{roughness:1});this.snowMat=this.mat(0xf8f8ed,{transparent:true,opacity:0,roughness:1,depthWrite:false});
    this.windowMat=this.mat(C.glass,{emissive:0xffbe69,emissiveIntensity:.05,metalness:.2,roughness:.35});this.waterMat=this.mat(0x8ba7a3,{roughness:.3,metalness:.2});this.rackLEDMat=this.mat(C.cool,{emissive:C.cool,emissiveIntensity:.9});
    this.listeners=[];const listen=(el,event,fn)=>{el.addEventListener(event,fn);this.listeners.push([el,event,fn]);};
    listen(this.canvas,'pointerdown',e=>{this.down={x:e.clientX,y:e.clientY};});
    listen(this.canvas,'pointerup',e=>{if(this.down&&Math.hypot(e.clientX-this.down.x,e.clientY-this.down.y)<6){const hit=this.hit(e);if(hit){this.select(hit);this.onSelect(hit);}}});
    listen(this.canvas,'pointermove',e=>{const hit=this.hit(e);this.canvas.style.cursor=hit?'pointer':'grab';if(hit!==this.hovered){this.hovered=hit;this.labels.forEach(l=>l.el.classList.toggle('is-hover',l.id===hit));this.dirty=true;}});
    listen(this.canvas,'pointerleave',()=>{this.hovered=null;this.labels.forEach(l=>l.el.classList.remove('is-hover'));});
    listen(this.canvas,'dblclick',e=>{const hit=this.hit(e);if(hit)this.focus(hit);});listen(this.canvas,'keydown',e=>this.keyboard(e));
    listen(this.canvas,'webglcontextlost',e=>{e.preventDefault();this.contextLost=true;this.showFallback('The 3D graphics context was interrupted. Forecasts and energy balances remain available.');});
    listen(this.canvas,'webglcontextrestored',()=>{this.contextLost=false;this.container.querySelector('.thermal-fallback')?.remove();this.dirty=true;});
    this.resizeObserver=new ResizeObserver(()=>this.resize());this.resizeObserver.observe(container);
    this.intersectionObserver=new IntersectionObserver(es=>{this.visible=es[0].isIntersecting;if(this.visible)this.dirty=true;});this.intersectionObserver.observe(container);
    this.build();this.resize();this.update(this.state);this.lastFrame=performance.now();this.tick=this.tick.bind(this);this.raf=requestAnimationFrame(this.tick);
  }
  installStyles(){
    if(document.getElementById('thermal-world-style'))return;const style=document.createElement('style');style.id='thermal-world-style';style.textContent=`
      .thermal-world{position:relative;isolation:isolate;overflow:hidden;background:#f3f0e8;min-height:340px;--world-ink:#253b3c;--world-label:rgba(249,247,240,.93)}
      .thermal-canvas{display:block;width:100%;height:100%;touch-action:none;outline:none}.thermal-canvas:focus-visible{outline:2px solid #b86237;outline-offset:-4px}
      .thermal-label-layer{position:absolute;inset:0;pointer-events:none;z-index:2;overflow:hidden}
      .thermal-node{position:absolute;left:0;top:0;display:flex;align-items:center;gap:7px;transform:translate(-50%,-100%);border:0;background:transparent;padding:4px 6px;color:var(--world-ink);text-align:left;font:500 10px/1.2 system-ui,sans-serif;letter-spacing:.01em;cursor:pointer;pointer-events:auto;white-space:nowrap;transition:background .2s,color .2s,opacity .2s;border-radius:2px}
      .thermal-node::after{content:'';position:absolute;left:50%;top:100%;width:1px;height:11px;background:currentColor;opacity:.4}
      .thermal-node b{display:flex;justify-content:center;align-items:center;width:20px;height:20px;border:1px solid currentColor;border-radius:50%;font-size:9px;font-weight:550;background:var(--world-label);flex-shrink:0}
      .thermal-node span{display:block}.thermal-node small{display:block;font-size:10px;margin-top:3px;opacity:.8;font-variant-numeric:tabular-nums}.thermal-node .thermal-node-title{text-transform:uppercase;font-size:9px;letter-spacing:.095em}
      .thermal-node.is-selected,.thermal-node.is-hover,.thermal-node:focus-visible{background:var(--world-label);outline:none;box-shadow:0 2px 16px #172a2c12}.thermal-node.is-selected b{background:#b86237;border-color:#b86237;color:white}.thermal-node.is-selected{z-index:3}
      .thermal-node.is-muted{opacity:.55}.thermal-node:focus-visible{outline:2px solid #b86237;outline-offset:2px}
      .thermal-fallback{position:absolute;inset:0;z-index:5;display:grid;place-content:center;padding:40px;max-width:500px;margin:auto;font:16px/1.6 system-ui;color:#253b3c;background:#f3f0e8}.thermal-fallback strong{font:28px Georgia,serif;margin-bottom:12px}
      @media(max-width:600px){.thermal-node{gap:4px;padding:3px}.thermal-node b{width:17px;height:17px;font-size:8px}.thermal-node .thermal-node-title{font-size:8px;letter-spacing:.03em}.thermal-node small{font-size:9px}}
      @media(prefers-reduced-motion:reduce){.thermal-node{transition:none}}
    `;document.head.append(style);
  }
  showFallback(message){let el=this.container.querySelector('.thermal-fallback');if(!el){el=document.createElement('div');el.className='thermal-fallback';el.setAttribute('role','status');this.container.append(el);}el.replaceChildren();const title=document.createElement('strong');title.textContent='The district, in numbers.';el.append(title,document.createTextNode(message));this.container.dataset.webgl='unavailable';}
  mat(color,opts={}){const key=JSON.stringify([color,opts]);if(!this.mats.has(key))this.mats.set(key,new THREE.MeshStandardMaterial({color,roughness:.7,metalness:.05,...opts}));return this.mats.get(key);}
  mesh(geo,mat,parent=this.world){const m=new THREE.Mesh(geo,typeof mat==='number'?this.mat(mat):mat);m.castShadow=true;m.receiveShadow=true;parent.add(m);return m;}
  box(w,h,d,x,y,z,mat,parent=this.world){const m=this.mesh(this.boxGeo,mat,parent);m.scale.set(w,h,d);m.position.set(x,y,z);return m;}
  cyl(r,h,x,y,z,mat,parent=this.world){const m=this.mesh(this.cylGeo,mat,parent);m.scale.set(r,h,r);m.position.set(x,y,z);return m;}
  sphere(r,x,y,z,mat,parent=this.world){const m=this.mesh(this.sphereGeo,mat,parent);m.scale.setScalar(r);m.position.set(x,y,z);return m;}
  group(dynamic=false){const g=new THREE.Group();g.userData.dynamic=dynamic;this.world.add(g);return g;}
  pick(m,id){m.userData.id=id;this.pickables.push(m);return m;}
  target(id,position){this.targets[id]=v3(position);}
  label(id,num,title,position){const el=document.createElement('button');el.type='button';el.className='thermal-node';el.setAttribute('aria-label',`Inspect ${title}`);const badge=document.createElement('b');badge.textContent=num;const text=document.createElement('span'),name=document.createElement('span');name.className='thermal-node-title';name.textContent=title;const value=document.createElement('small');value.textContent='—';text.append(name,value);el.append(badge,text);this.overlay.append(el);el.addEventListener('click',()=>{this.select(id);this.onSelect(id);});this.labels.push({id,el,value,pos:v3(position)});}
  roundedRect(width,depth,r=3){const x=-width/2,y=-depth/2,s=new THREE.Shape();s.moveTo(x+r,y);s.lineTo(x+width-r,y);s.quadraticCurveTo(x+width,y,x+width,y+r);s.lineTo(x+width,y+depth-r);s.quadraticCurveTo(x+width,y+depth,x+width-r,y+depth);s.lineTo(x+r,y+depth);s.quadraticCurveTo(x,y+depth,x,y+depth-r);s.lineTo(x,y+r);s.quadraticCurveTo(x,y,x+r,y);return s;}
  path(points,mat,r=.14,parent=this.world){const curve=this.curve(points),geo=new THREE.TubeGeometry(curve,Math.max(16,points.length*12),r,8,false);this.resources.add(geo);const mesh=this.mesh(geo,mat,parent);return{curve,mesh};}
  curve(points){const p=points.map(v3),curve=new THREE.CurvePath();let last=p[0];for(let i=1;i<p.length-1;i++){const a=p[i-1],b=p[i],c=p[i+1],r=Math.min(1.3,a.distanceTo(b)*.3,b.distanceTo(c)*.3),enter=b.clone().add(a.clone().sub(b).normalize().multiplyScalar(r)),leave=b.clone().add(c.clone().sub(b).normalize().multiplyScalar(r));curve.add(new THREE.LineCurve3(last,enter));curve.add(new THREE.QuadraticBezierCurve3(enter,b,leave));last=leave;}curve.add(new THREE.LineCurve3(last,p.at(-1)));return curve;}
  stream(id,points,{color=C.copper,max=3,reverse=false,r=.17}={}){const g=this.group(true),mat=new THREE.MeshStandardMaterial({color,emissive:color,emissiveIntensity:0,metalness:.42,roughness:.4}),route=this.path(points,mat,r,g),beadMaterial=new THREE.MeshBasicMaterial({color:color===C.teal?0xbde2dc:0xffd2a1});this.resources.add(mat);this.resources.add(beadMaterial);const beads=new THREE.InstancedMesh(this.sphereGeo,beadMaterial,20);beads.instanceMatrix.setUsage(THREE.DynamicDrawUsage);g.add(beads);const item={id,...route,beads,max,reverse,value:0,phase:this.routes.length*.087,positions:Array.from({length:241},(_,i)=>route.curve.getPointAt(i/240))};this.routes.push(item);return item;}
  returnPipe(points,r=.095){return this.path(points.map(p=>[p[0]+.48,p[1]-.12,p[2]+.48]),0x7b958a,r);}
  plaque(text,x,z,w=15,color='#6b7975'){const canvas=document.createElement('canvas');canvas.width=768;canvas.height=112;const ctx=canvas.getContext('2d');ctx.clearRect(0,0,768,112);ctx.fillStyle=color;ctx.font='500 39px system-ui';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(text,384,56);const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;this.resources.add(texture);const mat=new THREE.MeshBasicMaterial({map:texture,transparent:true,depthWrite:false,side:THREE.DoubleSide});this.resources.add(mat);const geo=new THREE.PlaneGeometry(w,w*112/768);this.resources.add(geo);const mesh=new THREE.Mesh(geo,mat);mesh.rotation.x=-Math.PI/2;mesh.position.set(x,.14,z);this.world.add(mesh);return mesh;}

  build(){
    this.world.clear();this.pickables=[];this.routes=[];this.targets={};this.fans=[];this.shell=[];this.tankLevels=[];this.customerHeat=[];this.streetLamps=[];
    this.labels.forEach(x=>x.el.remove());this.labels=[];this.resources.forEach(r=>r.dispose?.());this.resources.clear();
    const boardGeo=new THREE.ExtrudeGeometry(this.roundedRect(108,84,6),{depth:2.2,bevelEnabled:true,bevelThickness:.5,bevelSize:.5,bevelSegments:3,steps:1});this.resources.add(boardGeo);const board=this.mesh(boardGeo,this.mat(0xd3ccbb));board.rotation.x=-Math.PI/2;board.position.y=-2.8;
    const topGeo=new THREE.ShapeGeometry(this.roundedRect(107.5,83.5,6));this.resources.add(topGeo);const top=this.mesh(topGeo,this.groundMat);top.rotation.x=-Math.PI/2;top.position.y=.015;
    this.floorMat=this.mat(C.paper,{roughness:1});const floor=this.box(1500,.2,1500,0,-3.5,0,this.floorMat);floor.castShadow=false;
    this.context=this.group();if(this.site==='U')this.urbanContext();else this.ruralContext();
    this.infrastructure();this.dataCenter();this.heatPlant();this.storage();this.customers();this.network();this.gridSupply();
    this.selection=this.mesh(new THREE.RingGeometry(5.2,5.28,96),new THREE.MeshBasicMaterial({color:C.copper,transparent:true,opacity:.8,depthWrite:false,side:THREE.DoubleSide}));this.resources.add(this.selection.geometry);this.resources.add(this.selection.material);this.selection.rotation.x=-Math.PI/2;this.selection.position.y=.22;this.selection.visible=false;this.selection.userData.dynamic=true;
    this.plaque(this.site==='U'?'111 EIGHTH AVENUE  /  NEW YORK':'LAKE HAWKEYE  /  LANSING',-15,35,39,'#78817a');this.plaque('ILLUSTRATIVE DISTRICT · NOT TO SCALE',3,39,27,'#93988d');
    this.batchStatic();this.batchGroup(this.interior);this.setCutaway(this.cutaway,false);this.select(this.selected);this.renderer.shadowMap.needsUpdate=true;
  }
  infrastructure(){
    const road=this.mat(C.road,{roughness:1});this.box(5,.045,70,17,.075,-1,road);this.box(89,.045,4,0,.078,22,road);this.box(89,.04,3.5,0,.075,-29,road);
    for(let z=-31;z<32;z+=7)this.box(.07,.018,2,17,.108,z,0xe7e4d9);for(let x=-42;x<43;x+=7)this.box(2,.018,.06,x,.11,22,0xe7e4d9);
    this.box(19,.3,30,2,.2,-5,0xc7c6ba);this.box(18.5,.07,29.5,2,.385,-5,C.light);this.box(30,.2,24,-25,.18,-9,0xbdbfb3);
    for(let x=-36;x<=-16;x+=5)this.box(2.8,.035,.06,x,.305,1.8,0xe6e4d8);for(const[x,z]of[[-37,23],[-8,23],[18,-25],[18,13],[40,23]])this.lamp(x,z);
  }
  urbanContext(){
    const defs=[[-42,-32,8,9,10],[-31,-34,8,7,15],[-19,-35,9,6,12],[-6,-34,8,7,19],[7,-35,8,6,13],[36,-33,10,8,17],[47,-29,7,13,11],[46,-12,7,13,14],[46,5,7,13,8],[-44,8,8,12,9],[-42,30,10,8,7],[-30,29,8,7,6]];
    for(const[x,z,w,d,h]of defs)this.contextBuilding(x,z,w,d,h);
    for(const[x,z,s]of[[-45,-19,.8],[-42,-2,.9],[-40,18,1],[-21,26,.75],[-10,28,.8],[10,-27,.85],[42,17,.75],[42,-21,.9],[24,33,.7],[35,32,1.1]])this.tree(x,z,s);
    for(let i=0;i<5;i++)this.box(3.2,.03,.55,-42+i*4,.125,23.8,0xe9e6dc);
    for(const[x,z,rot,color]of[[-29,22,0,0xa7a494],[17,-22,1,0xd3bea7],[17,1,1,0x889690],[34,22,0,0xe2d8c6]]){const g=this.group();g.position.set(x,.15,z);g.rotation.y=rot*Math.PI/2;this.box(2,.7,1,0,.5,0,color,g);this.box(.9,.4,.86,-.05,1.02,0,0x5d7473,g);}
  }
  ruralContext(){
    // The lake is context, not an additional source of recovered server heat.
    const shape=new THREE.Shape();shape.moveTo(-51,-36);shape.lineTo(-37,-36);shape.bezierCurveTo(-29,-20,-43,-6,-34,9);shape.bezierCurveTo(-30,17,-33,24,-36,36);shape.lineTo(-51,36);shape.closePath();
    const geo=new THREE.ShapeGeometry(shape,20);this.resources.add(geo);const lake=this.mesh(geo,this.waterMat);lake.rotation.x=-Math.PI/2;lake.position.y=.1;lake.castShadow=false;
    for(let i=0;i<6;i++){const x=-45+i%2*3,z=-26+i*10;this.path([[x,.14,z],[x+3,.14,z+.3],[x+5,.14,z-.1]],this.mat(0xc5d6ca,{transparent:true,opacity:.55}),.028);}
    this.plaque('CAYUGA LAKE',-45,13,15,'#477575');
    const positions=[[-34,-31],[-27,-33],[-20,-32],[-10,-31],[0,-34],[9,-31],[30,-32],[42,-30],[45,-23],[46,-15],[45,-7],[47,3],[46,14],[-30,28],[-23,32],[-18,29],[-11,32],[-5,29],[1,33],[30,33],[39,29]];positions.forEach(([x,z],i)=>this.tree(x,z,.8+(i%3)*.25));
    this.box(1.1,.15,9,-42,.3,27,0xaa997b);this.box(7,.15,1.2,-44,.3,30,0xaa997b);for(let i=0;i<5;i++)this.contextBuilding(28+i%3*7,-32-Math.floor(i/3)*5,4.7,4.5,2.6,true);
  }
  contextBuilding(x,z,w,d,h,house=false){
    this.box(w+.6,.16,d+.6,x,.15,z,0xd7d5c7,this.context);this.box(w,h,d,x,h/2+.25,z,house?0xc8cab7:0xc8c9bf,this.context);this.box(w+.18,.3,d+.18,x,h+.3,z,0xe0ddcf,this.context);this.windows(x,z,w,d,h,{context:true,parent:this.context});
    if(house)this.roof(x,z,w+.4,d+.4,h+.3,1.5,C.stone,this.context);else{this.box(w*.5,.75,d*.45,x-.3,h+.83,z,0xbbc0b5,this.context);for(let i=0;i<Math.floor(w/2.5);i++)this.box(.75,.6,.75,x-w/3+i*2.1,h+.73,z+d*.26,0xb0b9ad,this.context);}
  }
  windows(x,z,w,d,h,{parent=this.world,context=false,id=null}={}){const rows=Math.max(1,Math.floor((h-1)/1.6)),cols=Math.max(2,Math.floor(w/1.65)),inst=new THREE.InstancedMesh(this.boxGeo,context?this.mat(0x9daaa2,{roughness:.75}):this.windowMat,rows*cols*2),m=new THREE.Matrix4(),q=new THREE.Quaternion();let n=0;for(const side of[-1,1])for(let r=0;r<rows;r++)for(let c=0;c<cols;c++){m.compose(new THREE.Vector3(x-w/2+(c+.5)*w/cols,.9+r*1.6,z+side*(d/2+.018)),q,new THREE.Vector3(context?.55:.68,.76,.035));inst.setMatrixAt(n++,m);}parent.add(inst);if(id)this.pick(inst,id);return inst;}
  tree(x,z,s=1){this.cyl(.12*s,1.8*s,x,.9*s,z,0x898570,this.context);const crown=this.mesh(this.leafGeo,this.foliageMat,this.context);crown.position.set(x,2.7*s,z);crown.scale.set(1.25*s,1.8*s,1.15*s);const small=this.mesh(this.leafGeo,this.foliageMat,this.context);small.position.set(x+.65*s,2.45*s,z+.4*s);small.scale.set(.9*s,1.25*s,.9*s);}
  roof(x,z,w,d,y,h,color,parent=this.world){const shape=new THREE.Shape();shape.moveTo(-w/2,0);shape.lineTo(0,h);shape.lineTo(w/2,0);shape.closePath();const geo=new THREE.ExtrudeGeometry(shape,{depth:d,bevelEnabled:false});this.resources.add(geo);const roof=this.mesh(geo,color,parent);roof.position.set(x,y,z-d/2);return roof;}
  lamp(x,z){this.cyl(.06,3.7,x,1.95,z,0x73877e);this.box(.7,.09,.46,x,3.85,z,C.ink);const light=this.box(.5,.055,.3,x,3.78,z,this.mat(0xf5dcab,{emissive:0xffd598,emissiveIntensity:.2}));this.streetLamps.push(light);}

  dataCenter(){
    const x=-24,z=-10,w=26,d=17,h=this.site==='U'?8:6.7;this.box(w+.7,.38,d+.7,x,.55,z,0xd8d4c7);this.target('source',[x,3.5,z]);
    const walls=this.group();this.pick(this.box(w,h,.45,x,h/2+.7,z-d/2,0xcfcabc,walls),'source');this.pick(this.box(.45,h,d,x-w/2,h/2+.7,z,0xcfcabc,walls),'source');this.box(w,.32,d,x,.84,z,0x6d8580);
    const shell=this.group(true);this.shell.push(shell);this.pick(this.box(w,h,.4,x,h/2+.7,z+d/2,0xded7c7,shell),'source');this.pick(this.box(.4,h,d,x+w/2,h/2+.7,z,0xded7c7,shell),'source');this.pick(this.box(w+.7,.38,d+.7,x,h+.9,z,0xe9e2d2,shell),'source');this.windows(x,z,w,d,h,{parent:shell,id:'source'});this.box(w+.4,.06,d+.4,x,h+1.12,z,this.snowMat,shell);
    this.box(w+.5,.35,3,x,h+.9,z-d/2+1.3,0xe3dccb);for(let i=0;i<9;i++)this.box(.18,h,.25,x-w/2+.8+i*3,h/2+.7,z-d/2-.24,0xbeb8a8);
    for(let i=0;i<4;i++){const xx=x-9+i*6;this.box(2.4,.7,1.7,xx,h+1.4,z-d/2+1.3,0xa0aea6);for(let j=0;j<5;j++)this.box(1.8,.055,.055,xx,h+1.77,z-d/2+.7+j*.25,0x5b7370);}
    this.interior=this.group(true);
    for(let row=0;row<3;row++)for(let col=0;col<7;col++){const xx=x-10+col*3.25,zz=z-4.7+row*4.1;this.pick(this.box(1.75,3.1,2.5,xx,2.48,zz,C.ink,this.interior),'source');this.box(1.48,2.75,.055,xx,2.48,zz+1.27,0x33484a,this.interior);for(let unit=0;unit<6;unit++){this.box(1.21,.055,.075,xx,1.3+unit*.45,zz+1.31,0x59716c,this.interior);this.box(.09,.08,.065,xx+.49,1.43+unit*.45,zz+1.32,this.rackLEDMat,this.interior);}}
    this.path([[-36,4.8,-15],[-14,4.8,-15],[-10,4.8,-15],[-10,1.1,-15]],C.copper,.2,this.interior);this.path([[-36,4.8,-14.25],[-14,4.8,-14.25],[-9.35,4.8,-14.25],[-9.35,1.1,-14.25]],C.teal,.16,this.interior);
    this.label('source','01','Data center',[x,h+3,z-1]);this.plaque('ELECTRICITY → COMPUTE → HEAT',x,2,22,'#5e7771');
    const cooling=this.group();this.box(22,.3,6,-23,.4,-23,0xb9c1b5,cooling);this.target('cooling',[-23,2.5,-23]);
    for(let i=0;i<4;i++){const xx=-31+i*5.4;this.pick(this.box(4.3,2.3,4.3,xx,1.7,-23,0x899e94,cooling),'cooling');this.cyl(1.48,.18,xx,2.95,-23,C.ink,cooling);this.cyl(.32,.23,xx,3.08,-23,0xbfc9bc,cooling);const fan=this.group(true);fan.position.set(xx,3.05,-23);for(let blade=0;blade<4;blade++){const b=this.box(1.35,.08,.38,.66,0,0,0x8b9e93,fan);b.position.set(Math.cos(blade*Math.PI/2)*.65,0,Math.sin(blade*Math.PI/2)*.65);b.rotation.y=-blade*Math.PI/2;}this.fans.push(fan);for(let j=0;j<5;j++)this.box(3.4,.065,.055,xx,.9+j*.35,-20.83,0xc2ccc0,cooling);}
    this.label('cooling','07','Cooling',[-25,6,-25]);
  }
  heatPlant(){
    this.target('hp',[2.5,2,-7]);this.pick(this.box(1.5,3.9,3,-6,2.3,-10,C.ink),'hp');for(let i=0;i<13;i++)this.box(.075,3.4,2.8,-6.6+i*.1,2.3,-10,0xaec0b4);this.box(1.9,.2,3.4,-6,.35,-10,C.copper);this.box(1.9,.2,3.4,-6,4.3,-10,C.copper);
    for(let i=0;i<3;i++){const z=-14+i*6.2;this.pick(this.box(6.9,2.75,4.8,3,1.8,z,0xd9dccc),'hp');this.box(6.1,.13,4.1,3,3.25,z,0x82988d);this.pick(this.cyl(.75,2,5.4,2.35,z,C.teal),'hp');this.cyl(.38,.28,5.4,3.53,z,C.brass);for(let j=0;j<8;j++)this.box(.065,1.9,3.8,.2+j*.31,1.85,z,0x637e74);this.path([[2,3.4,z-1.5],[2,4.3,z-1.5],[5,4.3,z-1.5],[5,3.6,z-1.5]],C.copper,.14);this.box(1,.7,.09,3.1,2.3,z+2.46,C.ink);this.box(.6,.3,.05,3.1,2.38,z+2.53,this.rackLEDMat);}
    // Common headers make the shared capacity visible: these are parallel modules,
    // not three independent claims on the same recovery tranche.
    this.path([[-5.2,1.2,-10],[-2.7,1.2,-10]],C.copper,.18);
    this.path([[-2.7,1.2,-14],[-2.7,1.2,-1.6]],C.copper,.18);
    this.path([[7.5,1.2,-14],[7.5,1.2,-1.6]],C.copper,.21);
    for(let i=0;i<3;i++){const z=-14+i*6.2;this.path([[-2.7,1.2,z],[-.5,1.2,z]],C.copper,.13);this.path([[6.5,1.2,z],[7.5,1.2,z]],C.copper,.15);}
    this.label('hp','02','Heat upgrade',[5,9,-5]);this.plaque('SHARED HEAT PLANT',2,8.5,15,'#536d63');
  }
  storage(){
    this.target('storage',[-7,3.5,15]);this.box(13,.3,9,-7,.35,15,0xc6c7b8);
    for(let i=0;i<2;i++){const x=-10.2+i*6.5,z=15;this.cyl(2.5,.25,x,.68,z,C.ink);this.cyl(2.3,.2,x,.88,z,0x8b9f90);const g=this.group(true),shellMat=this.mat(0xc6d4c5,{transparent:true,opacity:.19,roughness:.25,metalness:.3,depthWrite:false});this.pick(this.cyl(2.2,7.7,x,4.8,z,shellMat,g),'storage');const fill=this.cyl(2.02,.1,x,1.05,z,this.mat(C.copper,{emissive:0xb25e32,emissiveIntensity:.12,roughness:.5}),g);this.tankLevels.push(fill);const cap=this.sphere(2.2,x,8.7,z,0xd8dece);cap.scale.y=.25;this.pick(cap,'storage');for(const y of[1,3.5,6,8.7]){const geo=new THREE.TorusGeometry(2.24,.065,6,40);this.resources.add(geo);const ring=this.mesh(geo,0x97aaa0);ring.rotation.x=Math.PI/2;ring.position.set(x,y,z);}for(const side of[-1,1])this.box(.085,7.5,.1,x+side*2.23,4.8,z,0x9cafa2);for(let j=0;j<7;j++)this.box(.48,.075,.12,x+2.28,1.5+j,z+.1,0x6a887a);this.path([[x,8.9,z],[x,9.5,z],[x+1.4,9.5,z],[x+1.4,1,z]],C.copper,.12);}
    this.label('storage','03','Thermal storage',[-8,12,18]);
  }
  customers(){
    const housing=this.group(),commercial=this.group(),community=this.group();this.target('customer0',[31,4,-15]);this.target('customer1',[32,3,6]);this.target('customer2',[25,2,30]);
    const defs=this.site==='U'?[[26,-17,6,7,9],[34,-16,6,8,12],[29,-7,6,5,7]]:[[25,-18,5,6,4],[33,-19,5,6,4.7],[30,-10,5,6,4]];
    for(const[x,z,w,d,h]of defs){this.box(w+.5,.25,d+.5,x,.3,z,0xcecaba,housing);this.pick(this.box(w,h,d,x,h/2+.45,z,0xd4b99a,housing),'customer0');this.windows(x,z,w,d,h,{parent:housing,id:'customer0'});this.box(w+.4,.25,d+.4,x,h+.56,z,0xebdfca,housing);this.box(w+.35,.04,d+.35,x,h+.72,z,this.snowMat,housing);if(this.site==='R')this.roof(x,z,w+.4,d+.4,h+.6,1.8,0x7e9588,housing);else for(let y=2;y<h;y+=2.2)this.box(w+.15,.13,.5,x,y,z+d/2+.14,0xbd9f7d,housing);this.box(1.1,1.8,.07,x,1.4,z+d/2+.045,C.ink,housing);}
    this.label('customer0','04','Housing',[30,this.site==='U'?16:10,-16]);
    const x=32,z=7,w=15,d=12,h=this.site==='U'?6:4.7;this.box(w+.8,.3,d+.8,x,.3,z,0xc1c6b8,commercial);this.pick(this.box(w,h,d,x,h/2+.45,z,0xa6b7a9,commercial),'customer1');this.windows(x,z,w,d,h,{parent:commercial,id:'customer1'});this.box(w+.3,.3,d+.3,x,h+.6,z,0xe2e2d2,commercial);this.box(w+.3,.06,d+.3,x,h+.8,z,this.snowMat,commercial);this.box(w*.58,2.5,d*.55,x-2,h+1.9,z-1,0xb8c4b3,commercial);this.box(w*.58+.2,.18,d*.55+.2,x-2,h+3.24,z-1,0xe6e4d3,commercial);for(let i=0;i<5;i++)this.box(1.7,.08,2.4,x-5+i*2.2,h+.85,z+3.5,C.teal,commercial);this.label('customer1','05','Commercial',[32,h+6,7]);
    const cx=25,cz=30;this.box(21,.3,12,cx,.3,cz,0xd8cfb9,community);this.pick(this.box(18,4.2,7,cx,2.55,cz+1.1,0xdfc29e,community),'customer2');this.pick(this.box(6,4.2,11,cx-7,2.55,cz-1,0xdfc29e,community),'customer2');this.windows(cx,cz+1.1,18,7,4.2,{parent:community,id:'customer2'});this.box(18.5,.23,7.5,cx,4.79,cz+1.1,0xf0e3ca,community);this.box(6.5,.23,11.5,cx-7,4.79,cz-1,0xf0e3ca,community);this.box(9,.15,4.5,cx+2,4.7,cz-4.3,C.copper,community);for(let i=0;i<4;i++)this.box(.13,4.25,.13,cx-1+i*2.2,2.53,cz-6.4,C.copper,community);this.box(8,.1,2,cx+2,.5,cz-4.7,0x95a383,community);this.label('customer2','06','Community',[25,8.5,31]);
    for(const[index,x,z]of[[0,23,-15],[1,23,7],[2,16,29]]){const portMat=new THREE.MeshStandardMaterial({color:C.copper,emissive:C.hot,emissiveIntensity:0,roughness:.7});this.resources.add(portMat);const port=this.pick(this.box(.9,1.5,1.9,x,1.1,z,portMat),'customer'+index);port.userData.dynamic=true;this.customerHeat.push(port);this.path([[x,1,z],[x+1.5,1,z],[x+1.5,2.5,z]],C.copper,.12);}
  }
  network(){
    const source=[[-10,1.3,-15],[-10,1.3,-10],[-6.5,1.3,-10]];this.stream('source',source,{max:2.5,r:.22});this.returnPipe(source,.13);
    const plantOut=[[7.5,1.2,-7],[12,1.2,-7],[12,1.2,2]];this.stream('plant_out',plantOut,{max:4,r:.23});this.returnPipe(plantOut,.14);
    const main=[[12,1.2,2],[18.2,1.2,2]];this.stream('main',main,{max:4,r:.23});this.returnPipe(main,.14);
    const branches=[[[18.2,1.2,2],[20.3,1.2,2],[20.3,1.2,-15],[23,1.2,-15]],[[18.2,1.2,2],[20.3,1.2,2],[20.3,1.2,7],[23,1.2,7]],[[18.2,1.2,2],[18.2,1.2,19],[13,1.2,19],[13,1.2,29],[16,1.2,29]]];branches.forEach((points,i)=>{this.stream('customer'+i,points,{max:2,r:.17});this.returnPipe(points,.1);});
    const charge=[[12,1.2,2],[12,1.2,13],[1,1.2,13],[-2,1.2,13],[-2,1.2,15]];this.stream('storage',charge,{max:1.5,r:.18});this.returnPipe(charge,.12);
    this.path([[-2,1.2,15],[-10.2,1.2,15]],C.copper,.15);
    this.stream('reject',[[-10.5,1.5,-11],[-8.5,1.5,-11],[-8.5,1.5,-23],[-14,1.5,-23]],{color:0x9f7a5b,max:3,r:.17});for(const z of[-11,-6,0,6,12,18])this.cyl(.27,.035,20.3,.15,z,0xa68c66);
  }
  gridSupply(){
    this.stream('electricity',[[-51,.5,-13],[-43,.5,-13],[-41,.5,-7]],{color:C.teal,max:12,r:.12});
    this.stream('facility_electricity',[[-41,.5,-7],[-37,.5,-7]],{color:C.teal,max:12,r:.12});
    this.box(5,.22,7,-43,.32,-8,0xb9c0b2);for(let i=0;i<2;i++){this.cyl(.8,2.1,-44+i*2.1,1.5,-8,0x899f96);this.cyl(.22,.7,-44+i*2.1,2.8,-8,C.ink);}this.plaque('GRID',-46,-3,7,'#527a71');this.stream('heat_electricity',[[-41,.5,-7],[-40,.5,5],[-1,.5,5],[1,.5,1]],{color:C.teal,max:1.5,r:.08});
  }
  batchStatic(){
    this.world.updateMatrixWorld(true);const groups=new Map();this.world.traverse(o=>{if(!o.isMesh||o.isInstancedMesh||o===this.selection||![this.boxGeo,this.cylGeo,this.sphereGeo,this.leafGeo].includes(o.geometry))return;let parent=o;while(parent&&parent!==this.world){if(parent.userData.dynamic)return;parent=parent.parent;}const key=[o.geometry.uuid,o.material.uuid,o.userData.id||'',o.castShadow,o.receiveShadow].join(':');if(!groups.has(key))groups.set(key,[]);groups.get(key).push(o);});this.makeBatches(groups,this.world,true);
  }
  batchGroup(parent){const groups=new Map();parent.updateMatrixWorld(true);parent.traverse(o=>{if(!o.isMesh||o.isInstancedMesh||o.geometry!==this.boxGeo)return;const key=[o.geometry.uuid,o.material.uuid,o.userData.id||''].join(':');if(!groups.has(key))groups.set(key,[]);groups.get(key).push(o);});this.makeBatches(groups,parent,false);}
  makeBatches(groups,parent,useWorld){for(const meshes of groups.values()){if(meshes.length<3)continue;const first=meshes[0],inst=new THREE.InstancedMesh(first.geometry,first.material,meshes.length);inst.castShadow=first.castShadow;inst.receiveShadow=first.receiveShadow;inst.userData.id=first.userData.id;meshes.forEach((m,i)=>{inst.setMatrixAt(i,useWorld?m.matrixWorld:m.matrix);m.removeFromParent();const index=this.pickables.indexOf(m);if(index>=0)this.pickables.splice(index,1);});parent.add(inst);if(inst.userData.id)this.pickables.push(inst);}}

  update(input={}){
    const oldSite=this.site;this.state={...this.state,...input};this.site=this.state.site==='R'?'R':'U';if(!this.renderer)return;if(oldSite!==this.site){this.build();this.reset();}
    const s=this.state,h=((+s.hour||0)%24+24)%24,m=clamp(s.month,1,12),ambient=Number(s.ambient_c)||0;
    // Seasonal solar clock is illustrative, not a site ephemeris measurement.
    const seasonal=Math.cos((m-6.5)/12*Math.PI*2),dayLength=12+3.1*seasonal,sunrise=12-dayLength/2,sunset=12+dayLength/2,daylight=clamp(Math.min((h-sunrise+.6)/1.4,(sunset-h+.6)/1.4));this.daylight=daylight;
    this.scene.background.set(0x183437).lerp(new THREE.Color(C.paper),daylight);this.scene.fog.color.copy(this.scene.background);this.container.style.setProperty('--world-ink',daylight>.4?'#253b3c':'#e9dfc9');this.container.style.setProperty('--world-label',daylight>.4?'rgba(249,247,240,.93)':'rgba(23,47,49,.94)');this.container.dataset.timeOfDay=daylight>.5?'day':'night';
    this.sun.intensity=.6+daylight*2.9;this.hemisphere.intensity=.58+daylight*1.52;this.fillLight.intensity=.5+daylight*.65;const angle=(h-6)/12*Math.PI;this.sun.position.set(Math.cos(angle)*65,Math.max(22,Math.sin(angle)*85),40);this.sun.color.set(0x9bcbd8).lerp(new THREE.Color(daylight<.85?0xf9c198:0xffecc8),daylight);this.hemisphere.color.set(0x9ec6d0).lerp(new THREE.Color(0xfff8e8),daylight);this.hemisphere.groundColor.set(0x29444a).lerp(new THREE.Color(0x777e6f),daylight);this.fillLight.color.set(0x79a9be).lerp(new THREE.Color(0xd8e8e8),daylight);this.renderer.toneMappingExposure=1.05+daylight*.15;
    this.windowMat.emissiveIntensity=(1-daylight)*1.05+.04;this.rackLEDMat.emissiveIntensity=.35+clamp(finite(s.it_mw)/10)*.65;this.foliageMat.color.set([12,1,2].includes(Math.round(m))?0x989c83:[9,10,11].includes(Math.round(m))?0xb79861:0x7d8b61);
    const snow=clamp((2-ambient)/9)*([11,12,1,2,3].includes(Math.round(m))?1:0);this.snowMat.opacity=snow*.94;this.groundMat.color.set(C.light).lerp(new THREE.Color(0xf0f1e8),snow*.8).multiplyScalar(.62+daylight*.38);this.floorMat.color.set(0x28474a).lerp(new THREE.Color(C.paper),daylight);this.waterMat.color.set(ambient<-5?0xc1d1c5:0x8ba7a3);this.streetLamps.forEach(l=>{l.material.emissiveIntensity=(1-daylight)*1.8+.02;});
    const delivered=Array.isArray(s.delivered)?s.delivered:[0,0,0],sum=delivered.reduce((a,b)=>a+finite(b),0),facility=finite(s.it_mw)+finite(s.cooling_mw)+finite(s.other_mw),heatElectric=finite(s.heat_electricity_mw);
    const values={electricity:facility+heatElectric,facility_electricity:facility,heat_electricity:heatElectric,source:finite(s.source_used_mw??s.source_mw),plant_out:finite(s.hp_mw),main:finite(s.network_heat_mw??(sum+finite(s.network_loss_mw))),customer0:finite(delivered[0]),customer1:finite(delivered[1]),customer2:finite(delivered[2]),storage:Math.max(finite(s.charge_mw),finite(s.discharge_mw)),reject:finite(s.rejected_mw)};
    for(const r of this.routes){r.value=values[r.id]||0;if(r.id==='storage')r.reverse=finite(s.discharge_mw)>finite(s.charge_mw);r.beads.visible=r.value>1e-4;r.mesh.material.emissiveIntensity=r.value>1e-4?(1-daylight)*(.18+.5*clamp(r.value/r.max)):.0;}
    const ratio=clamp(finite(s.soc_mwh)/Math.max(.001,finite(s.storage_capacity_mwh,1)));this.tankLevels.forEach(fill=>{const height=Math.max(.07,ratio*7.4);fill.scale.y=height;fill.position.y=1.05+height/2;});
    this.customerHeat.forEach((port,i)=>{port.material.emissiveIntensity=clamp(finite(delivered[i])/Math.max(.1,finite(s.demand?.[i])))*.38;});
    const labelValues={source:mw(facility)+' electric',cooling:mw(s.cooling_mw)+' electric',hp:mw(s.hp_mw)+' thermal',storage:`${finite(s.soc_mwh).toFixed(1)} / ${finite(s.storage_capacity_mwh).toFixed(1)} MWh`,customer0:mw(delivered[0])+' heat',customer1:mw(delivered[1])+' heat',customer2:mw(delivered[2])+' heat'};
    for(const label of this.labels){label.value.textContent=labelValues[label.id]??'';label.el.classList.toggle('is-muted',label.id.startsWith('customer')&&values[label.id]<1e-4);}
    this.renderer.shadowMap.needsUpdate=true;this.dirty=true;this.container.dataset.flowSource='simulation-record';
  }
  select(id){this.selected=this.targets[id]?id:null;this.labels.forEach(l=>{l.el.classList.toggle('is-selected',l.id===this.selected);l.el.setAttribute('aria-pressed',String(l.id===this.selected));});if(this.selection){this.selection.visible=!!this.selected;if(this.selected){const p=this.targets[this.selected];this.selection.position.set(p.x,.24,p.z);this.selection.scale.setScalar(id==='source'?2:id==='storage'?1.35:1.2);}}this.dirty=true;}
  setCutaway(on,focus=false){this.cutaway=!!on;if(!this.renderer)return;this.shell.forEach(g=>{g.visible=!this.cutaway;});if(this.interior)this.interior.visible=this.cutaway;if(focus)this.focus('source');this.renderer.shadowMap.needsUpdate=true;this.dirty=true;}
  focus(id){const target=this.targets[id];if(!target)return;const offset=this.camera.position.clone().sub(this.controls.target).normalize().multiplyScalar(id==='source'?67:49);this.moveCamera(target.clone().add(offset),target);}
  reset(){if(!this.camera)return;this.moveCamera(new THREE.Vector3(92,96,120).multiplyScalar(this.container.clientWidth<600?1.14:1),new THREE.Vector3(0,1,0));}
  moveCamera(to,target){this.cameraMove={start:performance.now(),duration:this.reduced?0:950,from:this.camera.position.clone(),to,targetFrom:this.controls.target.clone(),targetTo:target.clone()};this.dirty=true;}
  keyboard(e){if(e.key==='Home'||e.key==='0'){e.preventDefault();this.reset();return;}if(e.key==='+'||e.key==='='||e.key==='-'){e.preventDefault();const offset=this.camera.position.clone().sub(this.controls.target);offset.multiplyScalar(e.key==='-'?1.12:.88);this.camera.position.copy(this.controls.target).add(offset);this.dirty=true;return;}if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;e.preventDefault();this.cameraMove=null;const spherical=new THREE.Spherical().setFromVector3(this.camera.position.clone().sub(this.controls.target));if(e.key==='ArrowLeft')spherical.theta-=.10;if(e.key==='ArrowRight')spherical.theta+=.10;if(e.key==='ArrowUp')spherical.phi-=.08;if(e.key==='ArrowDown')spherical.phi+=.08;spherical.phi=clamp(spherical.phi,this.controls.minPolarAngle,this.controls.maxPolarAngle);this.camera.position.copy(this.controls.target).add(new THREE.Vector3().setFromSpherical(spherical));this.controls.update();this.dirty=true;}
  hit(e){const rect=this.canvas.getBoundingClientRect();this.pointer.set((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1);this.raycaster.setFromCamera(this.pointer,this.camera);const hits=this.raycaster.intersectObjects(this.pickables,false);for(const hit of hits){let obj=hit.object,visible=true;while(obj){if(!obj.visible){visible=false;break;}obj=obj.parent;}if(visible)return hit.object.userData.id;}return null;}
  resize(){if(!this.renderer)return;const w=this.container.clientWidth,h=this.container.clientHeight;if(!w||!h)return;this.renderer.setSize(w,h,false);this.camera.aspect=w/h;this.camera.fov=w/h<1?44:34;this.camera.updateProjectionMatrix();this.dirty=true;}
  updateLabels(){const w=this.container.clientWidth,h=this.container.clientHeight,used=[],ordered=[...this.labels].sort((a,b)=>(b.id===this.selected?1:0)-(a.id===this.selected?1:0));for(const l of ordered){const p=this.projected.copy(l.pos).project(this.camera),x=(p.x*.5+.5)*w,y=(-p.y*.5+.5)*h,isSelected=l.id===this.selected;let show=p.z>-1&&p.z<1&&x>45&&x<w-45&&y>42&&y<h-30;const labelWidth=w<600?103:123;if(!isSelected&&used.some(pos=>Math.abs(pos[0]-x)<labelWidth&&Math.abs(pos[1]-y)<40))show=false;if(w<480&&!isSelected&&['cooling','hp'].includes(l.id))show=false;l.el.style.visibility=show?'visible':'hidden';l.el.style.left=`${x}px`;l.el.style.top=`${y}px`;if(show)used.push([x,y]);}}
  tick(now){
    if(this.disposed)return;this.raf=requestAnimationFrame(this.tick);if(!this.visible||document.hidden||this.contextLost)return;const elapsed=now-this.lastFrame;if(elapsed<1000/40)return;const dt=Math.min(elapsed/1000,.08);this.lastFrame=now;if(!this.reduced)this.time+=dt;
    if(this.cameraMove){const a=this.cameraMove,t=a.duration?clamp((now-a.start)/a.duration):1,ease=1-(1-t)**3;this.camera.position.lerpVectors(a.from,a.to,ease);this.controls.target.lerpVectors(a.targetFrom,a.targetTo,ease);if(t===1)this.cameraMove=null;this.dirty=true;}this.controls.update();
    if(!this.reduced||this.dirty){const matrix=new THREE.Matrix4(),quat=new THREE.Quaternion(),scale=new THREE.Vector3();for(const route of this.routes){const ratio=clamp(route.value/route.max),count=route.value>1e-4?Math.max(2,Math.round(3+ratio*17)):0;route.beads.count=count;const speed=.045+ratio*.055;for(let i=0;i<count;i++){let t=(this.time*speed+i/count+route.phase)%1;if(route.reverse)t=1-t;const index=Math.min(239,Math.floor(t*240)),p=route.positions[index].clone().lerp(route.positions[index+1],t*240-index);scale.setScalar(.105+ratio*.10);matrix.compose(p,quat,scale);route.beads.setMatrixAt(i,matrix);}route.beads.instanceMatrix.needsUpdate=true;}if(!this.reduced)this.fans.forEach((fan,i)=>{fan.rotation.y+=dt*(.5+finite(this.state.cooling_mw)*2)*(i%2?1:-1);});this.updateLabels();this.renderer.render(this.scene,this.camera);this.dirty=false;this.frameCount=(this.frameCount||0)+1;if(!this.perfStart)this.perfStart=now;if(now-this.perfStart>2500){this.container.dataset.fps=(this.frameCount*1000/(now-this.perfStart)).toFixed(1);this.container.dataset.drawCalls=String(this.renderer.info.render.calls);this.container.dataset.triangles=String(this.renderer.info.render.triangles);this.frameCount=0;this.perfStart=now;}}
  }
  dispose(){this.disposed=true;cancelAnimationFrame(this.raf);this.resizeObserver?.disconnect();this.intersectionObserver?.disconnect();this.motionQuery.removeEventListener('change',this.motionHandler);this.listeners?.forEach(([el,event,fn])=>el.removeEventListener(event,fn));this.controls?.dispose();this.resources.forEach(r=>r.dispose?.());this.mats.forEach(m=>m.dispose());[this.boxGeo,this.cylGeo,this.sphereGeo,this.leafGeo].forEach(g=>g?.dispose());this.renderer?.dispose();this.canvas?.remove();this.overlay?.remove();}
}
