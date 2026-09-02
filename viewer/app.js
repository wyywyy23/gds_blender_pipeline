(() => {
  "use strict";

  const DEFAULT_MODEL = "/.local/runs/ramzi/realtime/ramzi.realistic.glb";
  const canvas = document.getElementById("render-canvas");
  const loadingCard = document.getElementById("loading-card");
  const loadingMessage = document.getElementById("loading-message");
  const sceneStatus = document.getElementById("scene-status");
  const visibleCount = document.getElementById("visible-count");
  const modelDetail = document.getElementById("model-detail");
  const modelTitle = document.getElementById("model-title");
  const layerControls = document.getElementById("layer-controls");
  const materialControls = document.getElementById("material-controls");

  const state = {
    camera: null,
    meshes: [],
    records: [],
    hiddenLayers: new Set(),
    hiddenMaterials: new Set(),
    bounds: null,
  };

  const modelUrl = new URLSearchParams(window.location.search).get("model") || DEFAULT_MODEL;
  modelTitle.textContent = decodeURIComponent(modelUrl.split("/").pop() || "GDS model");

  function normalizeLayerName(name) {
    const base = String(name || "")
      .replace(/_primitive\d+$/i, "")
      .replace(/\.\d{3}$/, "");
    return /^L[A-Z0-9_]+$/.test(base) ? base.slice(1) : base || "Unassigned";
  }

  function extrasFor(node) {
    return node?.metadata?.gltf?.extras || node?.metadata?.extras || node?.metadata || {};
  }

  function inheritedExtra(mesh, key) {
    let node = mesh;
    while (node) {
      const extras = extrasFor(node);
      if (typeof extras[key] === "string" && extras[key]) {
        return extras[key];
      }
      node = node.parent;
    }
    return "";
  }

  function recordFor(mesh) {
    const layer = inheritedExtra(mesh, "gds_layer") || normalizeLayerName(mesh.name);
    const material = inheritedExtra(mesh, "gds_material") || mesh.material?.name || "Unassigned";
    return { mesh, layer, material };
  }

  function applyVisibility() {
    let shown = 0;
    for (const record of state.records) {
      const enabled = !state.hiddenLayers.has(record.layer)
        && !state.hiddenMaterials.has(record.material);
      record.mesh.setEnabled(enabled);
      if (enabled) shown += 1;
    }
    visibleCount.textContent = `${shown} / ${state.records.length} meshes`;
  }

  function checkboxRow(kind, value, count) {
    const label = document.createElement("label");
    label.className = "check-row";

    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = true;
    input.dataset.kind = kind;
    input.dataset.value = value;
    input.addEventListener("change", () => {
      const hidden = kind === "layers" ? state.hiddenLayers : state.hiddenMaterials;
      if (input.checked) hidden.delete(value);
      else hidden.add(value);
      applyVisibility();
    });

    const name = document.createElement("span");
    name.className = "check-name";
    name.textContent = value.replace(/^Mat_/, "");

    const badge = document.createElement("span");
    badge.className = "count-badge";
    badge.textContent = String(count);

    label.append(input, name, badge);
    return label;
  }

  function populateControls(container, kind, values) {
    container.replaceChildren();
    const counts = new Map();
    for (const value of values) counts.set(value, (counts.get(value) || 0) + 1);
    for (const [value, count] of [...counts].sort(([a], [b]) => a.localeCompare(b))) {
      container.append(checkboxRow(kind, value, count));
    }
  }

  function toggleGroup(kind, show) {
    const hidden = kind === "layers" ? state.hiddenLayers : state.hiddenMaterials;
    const controls = document.querySelectorAll(`input[data-kind="${kind}"]`);
    hidden.clear();
    for (const input of controls) {
      input.checked = show;
      if (!show) hidden.add(input.dataset.value);
    }
    applyVisibility();
  }

  function fitCamera() {
    if (!state.bounds || !state.camera) return;
    const { center, extent } = state.bounds;
    const radius = Math.max(extent.length() * 0.8, 0.000001);
    state.camera.setTarget(center);
    state.camera.radius = radius * 1.7;
    state.camera.lowerRadiusLimit = Math.max(radius * 0.003, 1e-9);
    state.camera.upperRadiusLimit = radius * 50;
    state.camera.minZ = Math.max(radius * 0.0001, 1e-9);
    state.camera.maxZ = Math.max(radius * 200, 1000);
    state.camera.panningSensibility = Math.max(2000 / radius, 10);
  }

  function setView(name) {
    if (!state.camera) return;
    const views = {
      iso: [-Math.PI / 4, Math.PI / 3],
      top: [-Math.PI / 2, 0.02],
      front: [-Math.PI / 2, Math.PI / 2],
    };
    const [alpha, beta] = views[name] || views.iso;
    state.camera.alpha = alpha;
    state.camera.beta = beta;
    fitCamera();
  }

  async function importModel(scene) {
    if (typeof BABYLON.ImportMeshAsync === "function") {
      return BABYLON.ImportMeshAsync(modelUrl, scene);
    }
    return BABYLON.SceneLoader.ImportMeshAsync(null, "", modelUrl, scene);
  }

  async function start() {
    if (!window.BABYLON) {
      throw new Error("Babylon.js did not load. Check the network connection or vendor the runtime locally.");
    }

    const engine = new BABYLON.Engine(canvas, true, {
      preserveDrawingBuffer: true,
      stencil: true,
    });
    const scene = new BABYLON.Scene(engine);
    scene.clearColor = new BABYLON.Color4(0.025, 0.04, 0.08, 1);

    const camera = new BABYLON.ArcRotateCamera(
      "presentation-camera",
      -Math.PI / 4,
      Math.PI / 3,
      10,
      BABYLON.Vector3.Zero(),
      scene,
    );
    camera.attachControl(canvas, true);
    camera.wheelDeltaPercentage = 0.01;
    camera.pinchDeltaPercentage = 0.01;
    camera.useNaturalPinchZoom = true;
    camera.inertia = 0.72;
    state.camera = camera;

    const ambient = new BABYLON.HemisphericLight(
      "ambient",
      new BABYLON.Vector3(0.25, 0.8, 0.45),
      scene,
    );
    ambient.intensity = 1.5;
    ambient.groundColor = new BABYLON.Color3(0.08, 0.1, 0.16);
    const key = new BABYLON.DirectionalLight(
      "key",
      new BABYLON.Vector3(-0.6, -1, -0.4),
      scene,
    );
    key.intensity = 2.1;

    sceneStatus.textContent = "Loading";
    const imported = await importModel(scene);
    state.meshes = imported.meshes.filter(
      (mesh) => typeof mesh.getTotalVertices === "function" && mesh.getTotalVertices() > 0,
    );
    if (!state.meshes.length) throw new Error("The GLB contains no renderable meshes.");

    state.records = state.meshes.map(recordFor);
    const minimumMaximum = BABYLON.Mesh.MinMax(state.meshes);
    state.bounds = {
      center: BABYLON.Vector3.Center(minimumMaximum.min, minimumMaximum.max),
      extent: minimumMaximum.max.subtract(minimumMaximum.min),
    };
    fitCamera();

    populateControls(layerControls, "layers", state.records.map((item) => item.layer));
    populateControls(materialControls, "materials", state.records.map((item) => item.material));
    const layerCount = new Set(state.records.map((item) => item.layer)).size;
    const materialCount = new Set(state.records.map((item) => item.material)).size;
    modelDetail.textContent = `${layerCount} layers · ${materialCount} materials`;
    applyVisibility();

    sceneStatus.textContent = "Ready";
    sceneStatus.classList.add("ready");
    loadingCard.hidden = true;
    engine.runRenderLoop(() => scene.render());
    window.addEventListener("resize", () => engine.resize());
  }

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  document.getElementById("fit-view").addEventListener("click", fitCamera);
  document.querySelectorAll("[data-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      toggleGroup(button.dataset.toggle, button.dataset.value === "all");
    });
  });

  start().catch((error) => {
    console.error(error);
    loadingMessage.textContent = error.message;
    loadingCard.classList.add("error");
    sceneStatus.textContent = "Error";
  });
})();
