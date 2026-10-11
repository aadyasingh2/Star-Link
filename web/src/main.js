import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DObject, CSS2DRenderer } from 'three/addons/renderers/CSS2DRenderer.js';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import './style.css';

const DATA_ROOT = '/data/';
const EARTH_RADIUS_KM = 6371;
const EARTH_SCALE = 1 / 1000;
const FADE_SECONDS = 0.5;
const NEXT_HOP_NOTE = 'Next-hop info: see Path Explorer.';
const [meta, positions, linkData, routeData, eventData] = await Promise.all(
  ['meta.json', 'positions.json', 'links.json', 'routes.json', 'events.json'].map(async (file) => {
    const response = await fetch(`${DATA_ROOT}${file}`);
    if (!response.ok) throw new Error(`Could not load ${file}: ${response.status}`);
    return response.json();
  }),
);

const nodes = meta.nodes;
const frameTimes = positions.times_s;
const frames = positions.frames_km;
const links = linkData.links;
const linkEvents = [...linkData.events].sort((a, b) => a.t - b.t);
const routeEvents = eventData.events;
const routeChangeCounts = new Map();
for (const event of routeEvents) {
  if (event.type === 'route_change') {
    routeChangeCounts.set(event.pair, (routeChangeCounts.get(event.pair) ?? 0) + 1);
  }
}
const duration = Number(meta.duration_s);
const stationStart = nodes.findIndex((node) => node.type === 'ground_station');
const stationNodes = nodes.slice(stationStart);
const stationByName = new Map(stationNodes.map((station) => [station.name, station]));
const routeNames = routeData.routers;
const routeColours = {
  Oracle: '#ffffff',
  LinkState: '#22d3ee',
  DistanceVector: '#fbbf24',
  SAPR: '#8b5cf6',
};
const pairIndexes = new Map();
routeData.pairs.forEach((pair, index) => {
  pairIndexes.set(`${pair.src}|${pair.dst}`, index);
  pairIndexes.set(`${pair.dst}|${pair.src}`, index);
});
const defaultPair = routeData.pairs[0];
const defaultSource = stationNodes.find((station) => station.id === defaultPair?.src) ?? stationNodes[0];
const defaultTarget = stationNodes.find((station) => station.id === defaultPair?.dst) ?? stationNodes[1];
const linkIndexes = new Map(links.map((link, index) => [`${link.a}|${link.b}`, index]));
const neighborLinks = Array.from({ length: nodes.length }, () => []);
links.forEach((link, id) => {
  neighborLinks[link.a].push({ node: link.b, link: id });
  neighborLinks[link.b].push({ node: link.a, link: id });
});

const state = {
  time: 0,
  playing: false,
  speed: 1,
  activeLinks: new Set(linkData.initial_link_ids),
  eventCursor: 0,
  lastEventTime: 0,
  frameLow: 0,
  selectedNode: null,
  hoveredNode: null,
  fadeOutAt: new Map(),
  activeView: 'Constellation',
  pathSource: defaultSource?.name ?? '',
  pathTarget: defaultTarget?.name ?? '',
  pathContextCache: null,
  pathDisplayContext: null,
  pathDisplayLinkFrame: -1,
  pathGeometryPairIndex: -1,
  pathGeometryFrameIndex: -1,
  pathGeometryReverse: false,
  pathGeometryLinkFrame: -1,
  reversePath: false,
  pathSort: 'hops',
  visibleRoutes: Object.fromEntries(routeNames.map((name) => [name, true])),
};

const root = document.querySelector('#scene-root');
const scene = new THREE.Scene();
scene.background = new THREE.Color('#07080f');
const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 300);
camera.position.set(2.1, 7.2, 20);
const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.15;
root.appendChild(renderer.domElement);
const labelRenderer = new CSS2DRenderer();
labelRenderer.domElement.className = 'label-layer';
root.appendChild(labelRenderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.07;
controls.minDistance = 8.3;
controls.maxDistance = 34;
controls.target.set(2.1, 0, 0);
controls.update();

scene.add(new THREE.AmbientLight(0x8294a3, 1.8));
const sun = new THREE.DirectionalLight(0xd4e8f3, 3.4);
sun.position.set(-10, 6, 14);
scene.add(sun);
const rimLight = new THREE.DirectionalLight(0x269bb3, 1.2);
rimLight.position.set(8, -4, -12);
scene.add(rimLight);

const earthGeometry = new THREE.SphereGeometry(EARTH_RADIUS_KM * EARTH_SCALE, 96, 64);
earthGeometry.rotateX(Math.PI / 2);
const textureLoader = new THREE.TextureLoader();
const earthTexture = textureLoader.load('/textures/blue-marble.png');
earthTexture.colorSpace = THREE.SRGBColorSpace;
earthTexture.anisotropy = renderer.capabilities.getMaxAnisotropy();
const earthMaterial = new THREE.MeshStandardMaterial({
  map: earthTexture,
  roughness: 0.96,
  metalness: 0,
});
const earth = new THREE.Mesh(earthGeometry, earthMaterial);
scene.add(earth);

const atmosphere = new THREE.Mesh(
  new THREE.SphereGeometry(EARTH_RADIUS_KM * EARTH_SCALE * 1.018, 64, 48),
  new THREE.ShaderMaterial({
    vertexShader: `
      varying vec3 vNormal;
      varying vec3 vView;
      void main() {
        vNormal = normalize(normalMatrix * normal);
        vec4 viewPosition = modelViewMatrix * vec4(position, 1.0);
        vView = normalize(-viewPosition.xyz);
        gl_Position = projectionMatrix * viewPosition;
      }
    `,
    fragmentShader: `
      varying vec3 vNormal;
      varying vec3 vView;
      void main() {
        float rim = pow(1.0 - max(dot(normalize(vNormal), normalize(vView)), 0.0), 2.3);
        gl_FragColor = vec4(0.19, 0.68, 0.82, rim * 0.23);
      }
    `,
    transparent: true,
    blending: THREE.AdditiveBlending,
    side: THREE.BackSide,
    depthWrite: false,
  }),
);
atmosphere.rotation.copy(earth.rotation);
scene.add(atmosphere);

const starCount = 1700;
const starPositions = new Float32Array(starCount * 3);
let randomSeed = Number(meta.seed) || 1;
const random = () => {
  randomSeed = (randomSeed * 1664525 + 1013904223) >>> 0;
  return randomSeed / 4294967296;
};
for (let i = 0; i < starCount; i += 1) {
  const direction = new THREE.Vector3(
    random() * 2 - 1,
    random() * 2 - 1,
    random() * 2 - 1,
  ).normalize();
  const radius = 35 + random() * 100;
  starPositions.set(direction.multiplyScalar(radius).toArray(), i * 3);
}
const starGeometry = new THREE.BufferGeometry();
starGeometry.setAttribute('position', new THREE.BufferAttribute(starPositions, 3));
const stars = new THREE.Points(
  starGeometry,
  new THREE.PointsMaterial({
    color: 0xb9d0df,
    size: 0.045,
    transparent: true,
    opacity: 0.58,
    sizeAttenuation: true,
    depthWrite: false,
  }),
);
scene.add(stars);

const satelliteGeometry = new THREE.SphereGeometry(0.055, 8, 6);
const nodeMaterial = new THREE.MeshBasicMaterial({
  color: 0xffffff,
  vertexColors: true,
  transparent: true,
  blending: THREE.AdditiveBlending,
  depthWrite: false,
});
const nodeMesh = new THREE.InstancedMesh(satelliteGeometry, nodeMaterial, nodes.length);
nodeMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
nodeMesh.frustumCulled = false;
const helper = new THREE.Object3D();
const planeColors = Array.from({ length: Number(meta.config['constellation.planes']) }, (_, plane) => (
  new THREE.Color('#8b5cf6').lerp(
    new THREE.Color('#22d3ee'),
    plane / Math.max(1, Number(meta.config['constellation.planes']) - 1),
  )
));
nodes.forEach((node, index) => {
  const color = node.type === 'satellite'
    ? planeColors[node.plane]
    : new THREE.Color('#f4fbff');
  nodeMesh.setColorAt(index, color);
});
if (nodeMesh.instanceColor) nodeMesh.instanceColor.needsUpdate = true;
scene.add(nodeMesh);

const linkPositionArray = new Float32Array(links.length * 6);
const linkColorArray = new Float32Array(links.length * 6);
const linkAlphaArray = new Float32Array(links.length * 2);
const linkGeometry = new THREE.BufferGeometry();
linkGeometry.setAttribute('position', new THREE.BufferAttribute(linkPositionArray, 3).setUsage(THREE.DynamicDrawUsage));
linkGeometry.setAttribute('color', new THREE.BufferAttribute(linkColorArray, 3));
linkGeometry.setAttribute('alpha', new THREE.BufferAttribute(linkAlphaArray, 1).setUsage(THREE.DynamicDrawUsage));
for (let id = 0; id < links.length; id += 1) {
  const color = links[id].type === 'intra'
    ? new THREE.Color('#22d3ee')
    : links[id].type === 'inter'
      ? new THREE.Color('#8b5cf6')
      : new THREE.Color('#f4fbff');
  linkColorArray.set(color.toArray(), id * 6);
  linkColorArray.set(color.toArray(), id * 6 + 3);
}
linkGeometry.setDrawRange(0, links.length * 2);
const linkMaterial = new THREE.ShaderMaterial({
  vertexShader: `
    attribute float alpha;
    attribute vec3 color;
    varying float vAlpha;
    varying vec3 vColor;
    void main() {
      vAlpha = alpha;
      vColor = color;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: `
    varying float vAlpha;
    varying vec3 vColor;
    void main() {
      gl_FragColor = vec4(vColor, vAlpha);
    }
  `,
  transparent: true,
  depthWrite: false,
  blending: THREE.AdditiveBlending,
});
const linkLines = new THREE.LineSegments(linkGeometry, linkMaterial);
linkLines.frustumCulled = false;
scene.add(linkLines);

const routeObjects = {};
for (const [index, name] of routeNames.entries()) {
  const color = routeColours[name] ?? meta.router_colours[name] ?? '#ffffff';
  const glowMaterial = new LineMaterial({
    color, linewidth: 9, transparent: true, opacity: 0.2,
    depthTest: false, blending: THREE.AdditiveBlending,
  });
  const coreMaterial = new LineMaterial({
    color, linewidth: 3, transparent: true, opacity: 0.95,
    depthTest: false, blending: THREE.AdditiveBlending,
  });
  const pulseMaterial = new LineMaterial({
    color, linewidth: 2.4, transparent: true, opacity: 0.72,
    depthTest: false, blending: THREE.AdditiveBlending,
    dashed: true, dashSize: 0.12, gapSize: 0.9,
  });
  const emptyGeometry = new LineGeometry();
  const glow = new Line2(emptyGeometry, glowMaterial);
  const core = new Line2(emptyGeometry, coreMaterial);
  const pulse = new Line2(emptyGeometry, pulseMaterial);
  for (const line of [glow, core, pulse]) {
    line.renderOrder = 6 + index;
    line.frustumCulled = false;
    scene.add(line);
  }
  const downMaterial = new LineMaterial({
    color: '#ff4d5a', linewidth: 5, transparent: true, opacity: 0.95,
    depthTest: false, blending: THREE.AdditiveBlending,
  });
  const downLine = new Line2(new LineGeometry(), downMaterial);
  downLine.renderOrder = 11 + index;
  downLine.frustumCulled = false;
  scene.add(downLine);
  routeObjects[name] = {
    glow, core, pulse, downLine, pulseMaterial, routeKey: '',
    positions: [], downPositions: [], downHops: [], pathIndexes: null,
  };
}
const endpointGeometry = new THREE.SphereGeometry(0.105, 12, 8);
const sourceMarker = new THREE.Mesh(endpointGeometry, new THREE.MeshBasicMaterial({
  color: '#ff4055', blending: THREE.AdditiveBlending,
}));
const targetMarker = new THREE.Mesh(endpointGeometry, new THREE.MeshBasicMaterial({
  color: '#35ec88', blending: THREE.AdditiveBlending,
}));
sourceMarker.renderOrder = 12;
targetMarker.renderOrder = 12;
scene.add(sourceMarker, targetMarker);

const stationLabels = [];
stationNodes.forEach((station, index) => {
  const label = document.createElement('div');
  label.className = 'station-label';
  label.textContent = station.name;
  const object = new CSS2DObject(label);
  scene.add(object);
  stationLabels.push({ nodeIndex: stationStart + index, object });
});

const trailGeometry = new THREE.BufferGeometry();
const trailPositions = new Float32Array(25 * 3);
trailGeometry.setAttribute('position', new THREE.BufferAttribute(trailPositions, 3).setUsage(THREE.DynamicDrawUsage));
const trail = new THREE.Line(
  trailGeometry,
  new THREE.LineDashedMaterial({
    color: '#22d3ee',
    dashSize: 0.16,
    gapSize: 0.1,
    transparent: true,
    opacity: 0.82,
    depthWrite: false,
  }),
);
trail.visible = false;
scene.add(trail);

const raycaster = new THREE.Raycaster();
raycaster.params.Mesh.threshold = 0.12;
const pointer = new THREE.Vector2();
const worldVectors = Array.from({ length: nodes.length }, () => new THREE.Vector3());
const labelVector = new THREE.Vector3();
const inspector = document.querySelector('#inspector');
const inspectorContent = document.querySelector('#inspector-content');
const tooltip = document.querySelector('#hover-tooltip');
const stage = document.querySelector('#scene');
let flyTarget = null;
let lastFrameTime = performance.now();
let frameCounter = 0;
let fpsWindowStart = lastFrameTime;
let sceneDirty = true;

function frameBracket(time) {
  let low = 0;
  let high = frameTimes.length - 1;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    if (frameTimes[mid] <= time) low = mid;
    else high = mid - 1;
  }
  return low;
}

function interpolatePosition(nodeIndex, time, target = new THREE.Vector3()) {
  const low = frameBracket(time);
  const high = Math.min(low + 1, frameTimes.length - 1);
  const range = frameTimes[high] - frameTimes[low];
  const mix = range > 0 ? THREE.MathUtils.clamp((time - frameTimes[low]) / range, 0, 1) : 0;
  const offset = nodeIndex * 3;
  const a = frames[low];
  const b = frames[high];
  return target.set(
    THREE.MathUtils.lerp(a[offset], b[offset], mix) * EARTH_SCALE,
    THREE.MathUtils.lerp(a[offset + 2], b[offset + 2], mix) * EARTH_SCALE,
    -THREE.MathUtils.lerp(a[offset + 1], b[offset + 1], mix) * EARTH_SCALE,
  );
}

function setSimulationTime(time) {
  state.time = THREE.MathUtils.clamp(time, 0, duration);
  if (state.time < state.lastEventTime) {
    state.activeLinks = new Set(linkData.initial_link_ids);
    state.fadeOutAt.clear();
    state.eventCursor = 0;
    while (state.eventCursor < linkEvents.length && linkEvents[state.eventCursor].t <= state.time) {
      applyLinkEvent(linkEvents[state.eventCursor]);
      state.eventCursor += 1;
    }
  } else {
    while (state.eventCursor < linkEvents.length && linkEvents[state.eventCursor].t <= state.time) {
      applyLinkEvent(linkEvents[state.eventCursor]);
      state.eventCursor += 1;
    }
  }
  state.lastEventTime = state.time;
  sceneDirty = true;
  document.querySelector('#time-slider').value = String(state.time);
  document.querySelector('#time-value').textContent = state.time.toFixed(1);
  document.querySelector('#link-count').textContent = state.activeLinks.size.toLocaleString();
  if (state.selectedNode !== null) renderInspector(state.selectedNode);
  if (state.activeView === 'Path Explorer') refreshPathExplorer();
}

function applyLinkEvent(event) {
  if (event.type === 'up') {
    state.activeLinks.add(event.link_id);
    state.fadeOutAt.delete(event.link_id);
  } else if (event.type === 'down') {
    state.activeLinks.delete(event.link_id);
    state.fadeOutAt.set(event.link_id, event.t);
  }
}

function updateSceneGeometry() {
  const time = state.time;
  for (let i = 0; i < nodes.length; i += 1) {
    const point = interpolatePosition(i, time, worldVectors[i]);
    helper.position.copy(point);
    helper.scale.setScalar(nodes[i].type === 'ground_station' ? 1.35 : 1);
    helper.updateMatrix();
    nodeMesh.setMatrixAt(i, helper.matrix);
  }
  nodeMesh.instanceMatrix.needsUpdate = true;

  for (let id = 0; id < links.length; id += 1) {
    const link = links[id];
    const start = worldVectors[link.a];
    const end = worldVectors[link.b];
    linkPositionArray.set(start.toArray(), id * 6);
    linkPositionArray.set(end.toArray(), id * 6 + 3);
    let alpha = state.activeLinks.has(id)
      ? link.type === 'gsl' ? 0.67 : link.type === 'intra' ? 0.27 : 0.18
      : 0;
    if (!state.activeLinks.has(id) && state.fadeOutAt.has(id)) {
      const fade = 1 - (time - state.fadeOutAt.get(id)) / FADE_SECONDS;
      alpha = fade > 0 ? (link.type === 'gsl' ? 0.67 : link.type === 'intra' ? 0.27 : 0.18) * fade : 0;
    }
    linkAlphaArray[id * 2] = alpha;
    linkAlphaArray[id * 2 + 1] = alpha;
  }
  linkGeometry.attributes.position.needsUpdate = true;
  linkGeometry.attributes.alpha.needsUpdate = true;

  for (const item of stationLabels) {
    item.object.position.copy(interpolatePosition(item.nodeIndex, time, labelVector).multiplyScalar(1.04));
  }
  updateTrail();
  if (state.activeView === 'Path Explorer') updateRouteGeometry();
}

function updateTrail() {
  const focus = state.selectedNode ?? state.hoveredNode;
  if (focus === null || nodes[focus].type !== 'satellite') {
    trail.visible = false;
    return;
  }
  const start = Math.max(0, state.time - 120);
  const samples = [];
  for (let i = 0; i < frameTimes.length; i += 1) {
    if (frameTimes[i] >= start && frameTimes[i] <= state.time) {
      samples.push(interpolatePosition(focus, frameTimes[i]));
    }
  }
  samples.push(interpolatePosition(focus, state.time));
  const count = Math.min(samples.length, 25);
  const stride = Math.max(1, Math.ceil(samples.length / count));
  let out = 0;
  for (let i = 0; i < samples.length; i += stride) {
    trailPositions.set(samples[i].toArray(), out * 3);
    out += 1;
  }
  if (samples.length && out < 25) {
    trailPositions.set(samples[samples.length - 1].toArray(), out * 3);
    out += 1;
  }
  trailGeometry.setDrawRange(0, Math.max(0, out));
  trailGeometry.attributes.position.needsUpdate = true;
  trail.computeLineDistances();
  trail.visible = out > 1;
}

function selectedPairContext() {
  const routeTime = THREE.MathUtils.clamp(
    Math.floor(state.time), routeData.times_s[0], routeData.times_s.at(-1),
  );
  const frameIndex = frameBracketFor(routeData.times_s, routeTime);
  const cached = state.pathContextCache;
  if (cached
    && cached.sourceName === state.pathSource
    && cached.targetName === state.pathTarget
    && cached.frameIndex === frameIndex
    && cached.reverseToggle === state.reversePath) return cached;
  const source = stationByName.get(state.pathSource);
  const target = stationByName.get(state.pathTarget);
  if (!source || !target || source.id === target.id) return null;
  const pairIndex = pairIndexes.get(`${source.id}|${target.id}`);
  if (pairIndex === undefined) return null;
  const routeSample = routeData.samples[frameIndex][pairIndex];
  const sourceIndex = nodes.indexOf(source);
  const targetIndex = nodes.indexOf(target);
  const reverse = (source.id !== routeData.pairs[pairIndex].src) !== state.reversePath;
  const context = {
    source,
    target,
    sourceName: state.pathSource,
    targetName: state.pathTarget,
    reverseToggle: state.reversePath,
    reverse,
    pairIndex,
    frameIndex,
    routeSample,
    displaySourceIndex: reverse ? targetIndex : sourceIndex,
    displayTargetIndex: reverse ? sourceIndex : targetIndex,
    key: `${pairIndex}:${frameIndex}:${reverse}`,
  };
  state.pathContextCache = context;
  return context;
}

function nearestLinkFrame(time) {
  const framesAt = linkData.delay_times_s;
  let nearest = 0;
  for (let index = 1; index < framesAt.length; index += 1) {
    if (Math.abs(framesAt[index] - time) < Math.abs(framesAt[nearest] - time)) {
      nearest = index;
    }
  }
  return nearest;
}

function routePath(context, routerIndex) {
  const path = context.routeSample.paths[routerIndex];
  if (!Array.isArray(path) || path.length === 0) return null;
  return context.reverse ? [...path].reverse() : path;
}

function linkDelayAtFrame(from, to, frameIndex) {
  const linkIndex = linkIndexes.get(`${Math.min(from, to)}|${Math.max(from, to)}`);
  if (linkIndex === undefined) return null;
  const delay = linkData.delay_ms[frameIndex]?.[linkIndex];
  return Number.isFinite(delay) ? delay : null;
}

function routeChangeCount(pairIndex) {
  return routeChangeCounts.get(pairIndex) ?? 0;
}

function renderPathCards(context) {
  const cards = document.querySelector('#path-cards');
  const comparison = document.querySelector('#path-comparison');
  const linkFrameIndex = nearestLinkFrame(state.time);
  const entries = routeNames.map((name, routerIndex) => {
    const path = routePath(context, routerIndex);
    const sample = context.routeSample;
    return {
      name,
      routerIndex,
      path,
      delay: sample.delay_ms[routerIndex],
      category: sample.classes[routerIndex],
      failure: sample.last_failure[routerIndex],
    };
  });
  entries.sort((a, b) => {
    if (state.pathSort === 'hops') {
      const hopsA = a.path ? a.path.length - 1 : Infinity;
      const hopsB = b.path ? b.path.length - 1 : Infinity;
      return hopsA - hopsB || a.routerIndex - b.routerIndex;
    }
    const delayA = Number.isFinite(a.delay) ? a.delay : Infinity;
    const delayB = Number.isFinite(b.delay) ? b.delay : Infinity;
    return delayA - delayB || a.routerIndex - b.routerIndex;
  });
  cards.innerHTML = entries.map((entry) => {
    const noRoute = !entry.path || entry.path.length < 2 || entry.category === 'loop';
    const isDrawable = !noRoute;
    const reason = entry.failure ?? (noRoute ? 'failure reason unavailable in export' : '');
    const hops = entry.path ? entry.path.length - 1 : '—';
    const total = Number.isFinite(entry.delay) ? `${entry.delay.toFixed(3)} ms` : '—';
    const hopRows = entry.path ? entry.path.slice(0, -1).map((nodeIndex, hopIndex) => {
      const nextIndex = entry.path[hopIndex + 1];
      const delay = linkDelayAtFrame(nodeIndex, nextIndex, linkFrameIndex);
      const down = delay === null;
      return `<li class="${down ? 'hop-down' : ''}">
        <span class="hop-number">${hopIndex + 1}</span>
        <span class="hop-nodes">${escapeHtml(nodes[nodeIndex].name || nodes[nodeIndex].id)} <i>→</i> ${escapeHtml(nodes[nextIndex].name || nodes[nextIndex].id)}</span>
        <span class="hop-delay">${down ? 'link down at this time' : `${delay.toFixed(3)} ms`}</span>
      </li>`;
    }).join('') : '';
    return `<article class="route-card" data-router="${entry.name}">
      <div class="route-card-heading">
        <span class="route-color" style="--route-color:${routeColours[entry.name] ?? '#ffffff'}"></span>
        <strong>${escapeHtml(entry.name)}</strong>
        <label class="route-visibility" title="Toggle this router route"><input type="checkbox" data-route-toggle="${escapeHtml(entry.name)}" ${state.visibleRoutes[entry.name] ? 'checked' : ''} /><span>SHOW</span></label>
      </div>
      <div class="route-metrics"><span><b>${hops}</b> HOPS</span><span><b>${total}</b> ACTUAL</span><span class="class-chip class-${escapeHtml(entry.category)}">${escapeHtml(entry.category)}</span></div>
      ${!isDrawable ? `<div class="route-failure">No route drawn${reason ? ` · ${escapeHtml(reason)}` : ''}</div>` : ''}
      ${entry.path ? `<ol class="hop-list">${hopRows}</ol>` : ''}
      <div class="route-change-count">route changes in this pair: ${routeChangeCount(context.pairIndex)}</div>
    </article>`;
  }).join('');
  cards.querySelectorAll('[data-route-toggle]').forEach((input) => {
    input.addEventListener('change', () => {
      state.visibleRoutes[input.dataset.routeToggle] = input.checked;
      updateRouteVisibility();
    });
  });
  const oracleDelay = context.routeSample.delay_ms[routeNames.indexOf('Oracle')];
  comparison.innerHTML = `<span class="eyebrow">ORACLE COMPARISON</span><div class="comparison-items">${
    routeNames.filter((name) => name !== 'Oracle').map((name) => {
      const delay = context.routeSample.delay_ms[routeNames.indexOf(name)];
      const difference = Number.isFinite(delay) && Number.isFinite(oracleDelay)
        ? `${(delay - oracleDelay).toFixed(3)} ms`
        : '—';
      const stretch = Number.isFinite(delay) && Number.isFinite(oracleDelay) && oracleDelay !== 0
        ? `${(delay / oracleDelay).toFixed(3)}×`
        : '—';
      return `<span><i class="route-color" style="--route-color:${routeColours[name]}"></i>${escapeHtml(name)} <b>Δ ${difference}</b> <small>${stretch}</small></span>`;
    }).join('')
  }</div>`;
}

function renderRouteEventStrip(context) {
  const wrap = document.querySelector('#event-strip-wrap');
  const strip = document.querySelector('#event-strip');
  wrap.hidden = false;
  const pairEvents = routeEvents.filter((event) =>
    event.type === 'route_change' && event.pair === context.pairIndex
  );
  document.querySelector('#event-strip-count').textContent = `${pairEvents.length} EVENTS`;
  const laneEnds = routeNames.map(() => []);
  const placements = pairEvents.map((event) => {
    const name = event.router;
    const routerIndex = Math.max(0, routeNames.indexOf(name));
    const x = THREE.MathUtils.clamp(event.t / duration, 0, 1) * strip.clientWidth;
    const lanes = laneEnds[routerIndex];
    let lane = lanes.findIndex((lastX) => x - lastX >= 6);
    if (lane < 0) lane = lanes.length;
    lanes[lane] = x;
    return { event, name, routerIndex, lane };
  });
  const laneOffsets = [];
  let totalLanes = 0;
  for (const lanes of laneEnds) {
    laneOffsets.push(totalLanes);
    totalLanes += Math.max(1, lanes.length);
  }
  strip.style.height = `${totalLanes * 7 + 2}px`;
  strip.innerHTML = placements.map(({ event, name, routerIndex, lane }) => {
    const left = THREE.MathUtils.clamp(event.t / duration, 0, 1) * 100;
    const top = laneOffsets[routerIndex] * 7 + lane * 7 + 1;
    return `<button class="event-tick" data-event-time="${event.t}" title="${escapeHtml(name)} route change · ${Number(event.t).toFixed(0)} s" style="left:${left}%;top:${top}px;--route-color:${routeColours[name] ?? '#ffffff'}" aria-label="${escapeHtml(name)} route change at ${event.t} seconds"></button>`;
  }).join('');
  strip.querySelectorAll('[data-event-time]').forEach((tick) => {
    tick.addEventListener('click', () => {
      setSimulationTime(Number(tick.dataset.eventTime));
    });
  });
}

function refreshPathExplorer() {
  const context = selectedPairContext();
  if (!context) {
    document.querySelector('#path-cards').innerHTML = '<div class="pair-unavailable">Choose two different ground stations present in routes.json.</div>';
    document.querySelector('#path-comparison').innerHTML = '';
    document.querySelector('#event-strip').replaceChildren();
    document.querySelector('#event-strip-count').textContent = '0 EVENTS';
    state.eventPairIndex = -1;
    state.pathDisplayContext = null;
    routeNames.forEach((name) => setRouteVisible(name, false));
    return;
  }
  const linkFrameIndex = nearestLinkFrame(state.time);
  if (state.pathDisplayContext !== context || state.pathDisplayLinkFrame !== linkFrameIndex) {
    state.pathDisplayContext = context;
    state.pathDisplayLinkFrame = linkFrameIndex;
    renderPathCards(context);
  }
  if (state.eventPairIndex !== context.pairIndex) {
    state.eventPairIndex = context.pairIndex;
    renderRouteEventStrip(context);
  }
}

function installRouteGeometry(object, positions, geometryType = 'route') {
  if (!positions.length || (geometryType === 'route' && positions.length < 2)) {
    object.routeKey = `${object.routeKey}:empty`;
    object.positions = [];
    object.glow.visible = false;
    object.core.visible = false;
    object.pulse.visible = false;
    object.downLine.visible = false;
    return;
  }
  const geometry = new LineGeometry();
  geometry.setPositions(positions);
  if (geometryType === 'route') {
    const previous = object.glow.geometry;
    object.glow.geometry = geometry;
    object.core.geometry = geometry;
    object.pulse.geometry = geometry;
    previous.dispose();
    for (const line of [object.glow, object.core, object.pulse]) line.computeLineDistances();
    object.positions = positions;
    object.glow.visible = true;
    object.core.visible = true;
    object.pulse.visible = true;
  } else {
    const previous = object.downLine.geometry;
    object.downLine.geometry = geometry;
    previous.dispose();
    object.downLine.computeLineDistances();
    object.downPositions = positions;
    object.downLine.visible = true;
  }
}

function updateLineCoordinates(geometry, path) {
  const starts = geometry.attributes.instanceStart;
  const ends = geometry.attributes.instanceEnd;
  if (!starts || !ends) return;
  for (let index = 0; index < path.length - 1; index += 1) {
    const start = worldVectors[path[index]];
    const end = worldVectors[path[index + 1]];
    starts.setXYZ(index, start.x, start.y, start.z);
    ends.setXYZ(index, end.x, end.y, end.z);
  }
  starts.needsUpdate = true;
  ends.needsUpdate = true;
}

function updateSegmentCoordinates(geometry, hops) {
  const starts = geometry.attributes.instanceStart;
  const ends = geometry.attributes.instanceEnd;
  if (!starts || !ends) return;
  for (let index = 0; index < hops.length; index += 1) {
    const [from, to] = hops[index];
    const start = worldVectors[from];
    const end = worldVectors[to];
    starts.setXYZ(index, start.x, start.y, start.z);
    ends.setXYZ(index, end.x, end.y, end.z);
  }
  starts.needsUpdate = true;
  ends.needsUpdate = true;
}

function updateRouteVisibility(context = selectedPairContext()) {
  for (const name of routeNames) {
    const object = routeObjects[name];
    const visible = state.visibleRoutes[name] && state.activeView === 'Path Explorer';
    object.glow.visible = visible && object.routeKey !== '';
    object.core.visible = visible && object.routeKey !== '';
    object.pulse.visible = visible && object.routeKey !== '';
    object.downLine.visible = visible && object.downPositions.length > 0;
  }
  const showEndpoints = context && state.activeView === 'Path Explorer';
  sourceMarker.visible = Boolean(showEndpoints);
  targetMarker.visible = Boolean(showEndpoints);
  if (context) {
    sourceMarker.position.copy(worldVectors[context.displaySourceIndex]);
    targetMarker.position.copy(worldVectors[context.displayTargetIndex]);
  }
}

function updateRouteGeometry() {
  const context = selectedPairContext();
  if (!context) {
    state.pathGeometryPairIndex = -1;
    state.pathGeometryFrameIndex = -1;
    state.pathGeometryLinkFrame = -1;
    routeNames.forEach((name) => {
      const object = routeObjects[name];
      object.routeKey = '';
      object.pathIndexes = null;
      object.downPositions = [];
      object.downHops = [];
      object.glow.visible = false;
      object.core.visible = false;
      object.pulse.visible = false;
      object.downLine.visible = false;
    });
    return;
  }
  const linkFrameIndex = nearestLinkFrame(state.time);
  if (state.pathGeometryPairIndex === context.pairIndex
    && state.pathGeometryFrameIndex === context.frameIndex
    && state.pathGeometryReverse === context.reverse
    && state.pathGeometryLinkFrame === linkFrameIndex) {
    for (const name of routeNames) {
      const object = routeObjects[name];
      if (object.pathIndexes?.length >= 2) {
        updateLineCoordinates(object.glow.geometry, object.pathIndexes);
        if (object.downHops.length) {
          updateSegmentCoordinates(object.downLine.geometry, object.downHops);
        }
      }
    }
    updateRouteVisibility(context);
    return;
  }
  state.pathGeometryPairIndex = context.pairIndex;
  state.pathGeometryFrameIndex = context.frameIndex;
  state.pathGeometryReverse = context.reverse;
  state.pathGeometryLinkFrame = linkFrameIndex;
  for (const [routerIndex, name] of routeNames.entries()) {
    const object = routeObjects[name];
    const path = routePath(context, routerIndex);
    const failure = context.routeSample.last_failure[routerIndex];
    const category = context.routeSample.classes[routerIndex];
    if (!path || category === 'loop' || failure === 'loop') {
      if (object.routeKey) {
        object.routeKey = '';
        object.glow.visible = false;
        object.core.visible = false;
        object.pulse.visible = false;
        object.downLine.visible = false;
        object.downPositions = [];
        object.downHops = [];
        object.pathIndexes = null;
      }
      continue;
    }
    const key = `${context.key}:${name}:${path.join(',')}:${linkFrameIndex}`;
    const downPositions = [];
    const downHops = [];
    for (let index = 0; index < path.length - 1; index += 1) {
      if (linkDelayAtFrame(path[index], path[index + 1], linkFrameIndex) === null) {
        downHops.push([path[index], path[index + 1]]);
        downPositions.push(...worldVectors[path[index]].toArray(), ...worldVectors[path[index + 1]].toArray());
      }
    }
    if (object.routeKey !== key) {
      object.routeKey = key;
      object.pathIndexes = path;
      if (path.length >= 2) {
        const pathPositions = path.flatMap((nodeIndex) => worldVectors[nodeIndex].toArray());
        installRouteGeometry(object, pathPositions);
      } else {
        object.routeKey = '';
        object.pathIndexes = null;
        object.glow.visible = false;
        object.core.visible = false;
        object.pulse.visible = false;
      }
      object.downPositions = downPositions;
      object.downHops = downHops;
      if (downPositions.length) installRouteGeometry(object, downPositions, 'down');
      else object.downLine.visible = false;
    }
    if (path.length >= 2) {
      updateLineCoordinates(object.glow.geometry, path);
      if (object.downHops.length) {
        updateSegmentCoordinates(object.downLine.geometry, object.downHops);
      }
    }
  }
  updateRouteVisibility(context);
}

function setRouteVisible(name, visible) {
  const object = routeObjects[name];
  if (!object) return;
  if (!visible) {
    object.glow.visible = false;
    object.core.visible = false;
    object.pulse.visible = false;
    object.downLine.visible = false;
  }
}

function updateDynamicDisplays() {
  const visibleLinks = state.activeLinks.size;
  document.querySelector('#link-count').textContent = visibleLinks.toLocaleString();
  if (state.selectedNode !== null) renderInspector(state.selectedNode);
}

function showInspector(index) {
  state.selectedNode = index;
  state.hoveredNode = null;
  tooltip.hidden = true;
  inspector.hidden = false;
  renderInspector(index);
  updateTrail();
  inspector.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  const nodePoint = interpolatePosition(index, state.time);
  const viewDirection = camera.position.clone().sub(controls.target).normalize();
  flyTarget = {
    target: nodePoint,
    position: nodePoint.clone().addScaledVector(viewDirection, Math.max(10, camera.position.distanceTo(controls.target) * 0.72)),
  };
}

function renderInspector(index) {
  if (state.selectedNode !== index) return;
  const node = nodes[index];
  document.querySelector('#inspector-name').textContent = node.name || node.id.replaceAll('_', ' ');
  const world = interpolatePosition(index, state.time);
  const positionKm = world.clone().multiplyScalar(1000);
  const details = node.type === 'satellite'
    ? `<div class="inspector-meta"><span>PLANE</span><strong>${node.plane + 1}</strong><span>INDEX</span><strong>${node.index_in_plane + 1}</strong></div>`
    : `<div class="inspector-meta"><span>LATITUDE</span><strong>${Number(node.lat).toFixed(4)}°</strong><span>LONGITUDE</span><strong>${Number(node.lon).toFixed(4)}°</strong></div>`;
  const neighbors = neighborLinks[index]
    .filter(({ link }) => state.activeLinks.has(link))
    .map(({ node: neighbor, link }) => {
      const edge = links[link];
      const neighborPosition = interpolatePosition(neighbor, state.time);
      const distanceKm = world.distanceTo(neighborPosition) * 1000;
      const delay = sampleLinkDelay(link, state.time);
      const life = sampleLifetime(link, state.time);
      const lifetimeLabel = life?.capped
        ? `capped at ${Number(linkData.look_ahead_cap_s).toFixed(0)} s`
        : life ? `${Number(life.seconds).toFixed(0)} s` : 'not sampled';
      return `<div class="neighbor-row">
        <div class="neighbor-name"><i class="type-dot ${edge.type}"></i><span>${escapeHtml(nodes[neighbor].name || nodes[neighbor].id)}</span></div>
        <div class="neighbor-facts"><span>${edge.type.toUpperCase()}</span><span>${distanceKm.toFixed(0)} KM</span></div>
        <div class="neighbor-facts"><span>${delay === null ? 'DELAY —' : `DELAY ${delay.toFixed(2)} MS`}</span><span>${escapeHtml(lifetimeLabel)}</span></div>
      </div>`;
    }).join('');
  inspectorContent.innerHTML = `
    <div class="node-identity"><span class="node-type">${node.type.replace('_', ' ').toUpperCase()}</span><span>${escapeHtml(node.id)}</span></div>
    ${details}
    <div class="position-readout"><span class="eyebrow">POSITION · ECEF</span><strong>${positionKm.x.toFixed(1)} <i>/</i> ${positionKm.y.toFixed(1)} <i>/</i> ${positionKm.z.toFixed(1)} <small>KM</small></strong></div>
    <div class="neighbors-heading"><span class="eyebrow">ACTIVE NEIGHBOURS</span><b>${neighborLinks[index].filter(({ link }) => state.activeLinks.has(link)).length}</b></div>
    <div class="neighbor-list">${neighbors || '<div class="empty-neighbors">No active neighbours at this time.</div>'}</div>
    <div class="route-note">${NEXT_HOP_NOTE}</div>`;
}

function sampleLinkDelay(linkIndex, time) {
  const times = linkData.delay_times_s;
  const low = frameBracketFor(times, time);
  const high = Math.min(low + 1, times.length - 1);
  const a = linkData.delay_ms[low]?.[linkIndex];
  const b = linkData.delay_ms[high]?.[linkIndex];
  if (Number.isFinite(a) && Number.isFinite(b)) {
    return THREE.MathUtils.lerp(a, b, (time - times[low]) / Math.max(1e-9, times[high] - times[low]));
  }
  if (Number.isFinite(a)) return a;
  if (Number.isFinite(b)) return b;
  return null;
}

function sampleLifetime(linkIndex, time) {
  const times = linkData.delay_times_s;
  const frame = frameBracketFor(times, time);
  return linkData.remaining_lifetime[frame]?.[linkIndex] ?? null;
}

function frameBracketFor(times, time) {
  let low = 0;
  let high = times.length - 1;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    if (times[mid] <= time) low = mid;
    else high = mid - 1;
  }
  return low;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);
}

function flyCamera() {
  if (!flyTarget) return;
  camera.position.lerp(flyTarget.position, 0.035);
  controls.target.lerp(flyTarget.target, 0.035);
  if (camera.position.distanceTo(flyTarget.position) < 0.03) flyTarget = null;
}

function onPointerMove(event) {
  const bounds = renderer.domElement.getBoundingClientRect();
  pointer.set(
    ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
    -((event.clientY - bounds.top) / bounds.height) * 2 + 1,
  );
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObject(nodeMesh, false)[0];
  const index = hit?.instanceId ?? null;
  state.hoveredNode = index;
  updateTrail();
  if (index === null) {
    tooltip.hidden = true;
  } else {
    const node = nodes[index];
    tooltip.textContent = node.name || node.id;
    tooltip.style.left = `${event.clientX + 14}px`;
    tooltip.style.top = `${event.clientY + 14}px`;
    tooltip.hidden = false;
  }
}

function onCanvasClick(event) {
  const bounds = renderer.domElement.getBoundingClientRect();
  pointer.set(
    ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
    -((event.clientY - bounds.top) / bounds.height) * 2 + 1,
  );
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObject(nodeMesh, false)[0];
  if (hit?.instanceId !== undefined) showInspector(hit.instanceId);
}

renderer.domElement.addEventListener('pointermove', onPointerMove);
renderer.domElement.addEventListener('pointerleave', () => {
  state.hoveredNode = null;
  tooltip.hidden = true;
  updateTrail();
});
renderer.domElement.addEventListener('click', onCanvasClick);

function render(timeNow) {
  requestAnimationFrame(render);
  const delta = Math.min((timeNow - lastFrameTime) / 1000, 0.1);
  lastFrameTime = timeNow;
  if (state.playing) {
    const next = state.time + delta * state.speed;
    setSimulationTime(Math.min(next, duration));
    if (next >= duration) setPlaying(false);
  }
  controls.update();
  flyCamera();
  for (const name of routeNames) {
    routeObjects[name].pulseMaterial.dashOffset = -(timeNow * 0.00015);
  }
  if (sceneDirty) {
    updateSceneGeometry();
    sceneDirty = false;
  }
  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);

  if (import.meta.env.DEV) {
    frameCounter += 1;
    if (timeNow - fpsWindowStart >= 500) {
      document.querySelector('#fps-readout').textContent = `${Math.round(frameCounter * 1000 / (timeNow - fpsWindowStart))} FPS`;
      frameCounter = 0;
      fpsWindowStart = timeNow;
    }
  }
}

function setPlaying(playing) {
  state.playing = playing;
  document.querySelector('#play-button').setAttribute('aria-label', playing ? 'Pause simulation' : 'Play simulation');
  document.querySelector('#play-icon').textContent = playing ? 'Ⅱ' : '▶';
}

function resize() {
  const width = root.clientWidth;
  const height = root.clientHeight;
  renderer.setSize(width, height);
  labelRenderer.setSize(width, height);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}

function setupInterface() {
  const satellites = nodes.filter((node) => node.type === 'satellite');
  const config = meta.config;
  document.querySelector('#satellite-count').textContent = satellites.length.toLocaleString();
  document.querySelector('#plane-count').textContent = Number(config['constellation.planes']).toLocaleString();
  document.querySelector('#station-count').textContent = stationNodes.length.toLocaleString();
  document.querySelector('#altitude').textContent = `${config['constellation.altitude_km']} km`;
  document.querySelector('#inclination').textContent = `${config['constellation.inclination_deg']}°`;
  document.querySelector('#dataset-duration').textContent = `${duration} S`;
  document.querySelector('#time-slider').max = String(duration);
  document.querySelector('#time-end').textContent = `${String(Math.floor(duration / 60)).padStart(2, '0')}:${String(duration % 60).padStart(2, '0')}`;
  document.querySelector('#provisional-badge').hidden = !meta.provisional;
  document.querySelector('#station-list').innerHTML = stationNodes.map((station) => `
    <button class="station-row" data-node-index="${nodes.indexOf(station)}">
      <span><i class="station-bullet"></i>${escapeHtml(station.name)}</span>
      <small>${Number(station.lat).toFixed(1)}° / ${Number(station.lon).toFixed(1)}°</small>
    </button>`).join('');
  document.querySelectorAll('.station-row').forEach((button) => {
    button.addEventListener('click', () => showInspector(Number(button.dataset.nodeIndex)));
  });
  document.querySelector('#station-options').innerHTML = stationNodes
    .map((station) => `<option value="${escapeHtml(station.name)}"></option>`).join('');
  document.querySelector('#path-source').value = state.pathSource;
  document.querySelector('#path-target').value = state.pathTarget;
  document.querySelectorAll('.nav-item').forEach((button) => {
    button.addEventListener('click', () => {
      state.activeView = button.dataset.view;
      document.querySelectorAll('.nav-item').forEach((item) => item.classList.toggle('active', item === button));
      const isConstellation = state.activeView === 'Constellation';
      const isPathExplorer = state.activeView === 'Path Explorer';
      document.querySelector('#view-placeholder').hidden = isConstellation || isPathExplorer;
      document.querySelector('#placeholder-title').textContent = state.activeView;
      document.querySelector('#constellation-panels').classList.toggle(
        'view-inactive', !isConstellation && !isPathExplorer,
      );
      document.querySelector('#constellation-panels').classList.toggle('path-mode', isPathExplorer);
      document.querySelector('#path-panel').hidden = !isPathExplorer;
      sceneDirty = true;
      if (isPathExplorer) refreshPathExplorer();
      updateRouteVisibility();
    });
  });
  document.querySelector('#sidebar-toggle').addEventListener('click', () => {
    const sidebar = document.querySelector('#sidebar');
    sidebar.classList.toggle('collapsed');
    document.querySelector('#sidebar-toggle').setAttribute(
      'aria-label',
      sidebar.classList.contains('collapsed') ? 'Expand navigation' : 'Collapse navigation',
    );
    setTimeout(resize, 250);
  });
  document.querySelector('#play-button').addEventListener('click', () => setPlaying(!state.playing));
  document.querySelector('#reset-button').addEventListener('click', () => {
    setPlaying(false);
    state.speed = 1;
    document.querySelectorAll('.speed-button').forEach((button) => button.classList.toggle('active', button.dataset.speed === '1'));
    setSimulationTime(0);
    state.selectedNode = null;
    inspector.hidden = true;
    updateTrail();
    flyTarget = { target: new THREE.Vector3(2.1, 0, 0), position: new THREE.Vector3(2.1, 7.2, 20) };
  });
  document.querySelectorAll('.speed-button').forEach((button) => {
    button.addEventListener('click', () => {
      state.speed = Number(button.dataset.speed);
      document.querySelectorAll('.speed-button').forEach((item) => item.classList.toggle('active', item === button));
    });
  });
  document.querySelector('#time-slider').addEventListener('input', (event) => {
    setSimulationTime(Number(event.target.value));
  });
  for (const input of [document.querySelector('#path-source'), document.querySelector('#path-target')]) {
    input.addEventListener('input', () => {
      sceneDirty = true;
      refreshPathExplorer();
    });
  }
  document.querySelectorAll('.path-tab').forEach((button) => {
    button.addEventListener('click', () => {
      state.pathSort = button.dataset.sort;
      document.querySelectorAll('.path-tab').forEach((tab) => tab.classList.toggle('active', tab === button));
      const context = selectedPairContext();
      if (context) renderPathCards(context);
    });
  });
  document.querySelector('#path-reverse').addEventListener('change', (event) => {
    state.reversePath = event.target.checked;
    sceneDirty = true;
    refreshPathExplorer();
  });
  document.querySelector('#zoom-in').addEventListener('click', () => {
    camera.position.addScaledVector(camera.position.clone().sub(controls.target).normalize(), -1.7);
  });
  document.querySelector('#zoom-out').addEventListener('click', () => {
    camera.position.addScaledVector(camera.position.clone().sub(controls.target).normalize(), 1.7);
  });
  document.querySelector('#inspector-close').addEventListener('click', () => {
    state.selectedNode = null;
    inspector.hidden = true;
    updateTrail();
  });
}

setupInterface();
resize();
window.addEventListener('resize', resize);
document.querySelector('#loading-state').hidden = true;
setSimulationTime(0);
updateSceneGeometry();
if (import.meta.env.DEV) {
  document.querySelector('#fps-readout').hidden = false;
  const visibilityRaycaster = new THREE.Raycaster();
  window.__saprDebug = {
    projectNode(index) {
      camera.updateMatrixWorld();
      const projected = worldVectors[index].clone().project(camera);
      const bounds = renderer.domElement.getBoundingClientRect();
      visibilityRaycaster.setFromCamera(new THREE.Vector2(projected.x, projected.y), camera);
      const earthHit = visibilityRaycaster.intersectObject(earth, false)[0];
      const nodeDistance = camera.position.distanceTo(worldVectors[index]);
      return {
        id: nodes[index].id,
        name: nodes[index].name || nodes[index].id,
        type: nodes[index].type,
        x: bounds.left + (projected.x + 1) * bounds.width / 2,
        y: bounds.top + (1 - projected.y) * bounds.height / 2,
        depth: projected.z,
        inFrustum: Math.abs(projected.x) <= 1 && Math.abs(projected.y) <= 1 && projected.z >= -1 && projected.z <= 1,
        earthOccluded: Boolean(earthHit && earthHit.distance < nodeDistance),
      };
    },
    raycastAt(x, y) {
      const bounds = renderer.domElement.getBoundingClientRect();
      pointer.set(((x - bounds.left) / bounds.width) * 2 - 1, -((y - bounds.top) / bounds.height) * 2 + 1);
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObject(nodeMesh, false)[0];
      return hit ? nodes[hit.instanceId].id : null;
    },
    routeLineVisible(name) {
      return Boolean(routeObjects[name]?.core.visible);
    },
  };
}
requestAnimationFrame(render);
