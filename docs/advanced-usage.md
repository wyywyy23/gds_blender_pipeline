# Advanced Usage

See the [README](../README.md) for installation and the first-render workflow.

## Make Targets

| Target | Purpose |
| --- | --- |
| `aim-build GDS=...` | Build a scene under `.local/runs/<layout>/` |
| `aim-render GDS=... PRESET=...` | Render a scene previously created by `aim-build` |
| `aim-run GDS=... PRESET=...` | Rebuild and render with an existing preset |
| `aim-preprocess AIM_GDS=... AIM_VISUAL_GDS=...` | Create a visualization GDS at explicit paths |
| `aim-build-scene AIM_GDS=... AIM_VISUAL_GDS=... AIM_BLEND=...` | Preprocess and build at explicit paths |
| `aim-render-layers` | Generate and merge render-layer metadata |
| `aim-registry` | Generate the resolved layer registry |
| `aim-blendergds-config` | Generate the BlenderGDS stack |
| `aim-render-preset` | Render an explicit `.blend` with an explicit preset |
| `aim-prepare-render-blend` | Create a portable render-ready `.blend` |
| `aim-clean-generated` | Remove generated AIM metadata and stack files |

## Explicit Paths

### Preprocess

```sh
AIM_GDS=path/to/my_cell.gds \
AIM_VISUAL_GDS=.local/work/my_cell.visual.gds \
make aim-preprocess
```

### Build

```sh
AIM_GDS=path/to/my_cell.gds \
AIM_VISUAL_GDS=.local/work/my_cell.visual.gds \
AIM_BLEND=.local/work/my_cell.blend \
make aim-build-scene
```

`AIM_BLEND` is a base path. The color scheme is inserted before `.blend`, so
the default output above is `.local/work/my_cell.realistic.blend`.

## AIM Metadata

Regenerate metadata after changing the PDK tech file, custom layers, doping
rules, or static render layers:

```sh
make aim-registry aim-blendergds-config
```

Generated files:

```text
configs/aim/render_layers.doping.generated.yaml
configs/aim/render_layers.yaml
configs/aim/layer_registry.local.yaml
configs/blender/aim.yaml
```

Direct commands:

```sh
conda run -n gds-blender-pipeline python scripts/aim_generate_doping_render_layers.py \
  --doping-rules configs/aim/doping_rules.yaml \
  --output configs/aim/render_layers.doping.generated.yaml

conda run -n gds-blender-pipeline python scripts/aim_merge_render_layers.py \
  --doping configs/aim/render_layers.doping.generated.yaml \
  --static configs/aim/render_layers.static.yaml \
  --output configs/aim/render_layers.yaml

conda run -n gds-blender-pipeline python scripts/aim_build_layer_registry.py \
  --tech external_pdks/AIMPhotonics_ACT1/tech.py \
  --raw-custom configs/aim/raw_custom_layers.yaml \
  --render-layers configs/aim/render_layers.yaml \
  --doping-rules configs/aim/doping_rules.yaml \
  --output configs/aim/layer_registry.local.yaml \
  --class-name LayerMapAIM

conda run -n gds-blender-pipeline python scripts/aim_generate_blendergds_config.py \
  --render-layers configs/aim/render_layers.yaml \
  --output configs/blender/aim.yaml
```

## Preprocessing

Preprocessing flattens the layout, generates substrate and cladding regions,
resolves silicon doping, and writes explicit render layers.

```sh
conda run -n gds-blender-pipeline python scripts/aim_preprocess_gds.py \
  --input path/to/my_cell.gds \
  --registry configs/aim/layer_registry.local.yaml \
  --output .local/work/my_cell.visual.gds \
  --max-polygon-vertices 256
```

## Scene Building

```sh
blender --background --python scripts/aim_build_blender_scene.py -- \
  --gds .local/work/my_cell.visual.gds \
  --stack-config configs/blender/aim.yaml \
  --color-config configs/blender/colors/aim/realistic.yaml \
  --output .local/work/my_cell.realistic.blend \
  --no-merge-layers
```

The builder applies materials, handles cladding, fits the camera, removes
PN-conflict debug objects, and saves the scene.

## Render Presets

A preset stores camera, lighting, color management, Cycles settings, output,
and named layer-visibility runs. See the README for a minimal preset.

Useful optional fields:

```yaml
lighting:
  sun:
    object: Sun
    strength: 5.0
    # Optional; otherwise derived from the camera rotation.
    rotation_degrees: [35.0, 0.0, 25.0]

color_management:
  view_transform: AgX
  look: AgX - High Contrast

render:
  engine: CYCLES
  samples: 1024
  denoise: false
  adaptive_sampling: false
  file_format: PNG

runs:
  - name: no_cladding
    hide_layers: [cladding]
  - name: all_layers
    hide_layers: []
```

Layer names accept short forms such as `cladding` or `cbam`. The renderer
normalizes them to render-layer object names. Resolution is read from the
source scene. CPU/GPU selection remains machine-specific.

### Render With Make

```sh
AIM_RENDER_BLEND=.local/runs/my_cell/blender/my_cell.realistic.blend \
AIM_RENDER_PRESET=configs/blender/render_presets/my_view.yaml \
AIM_RENDER_RUNS="no_cladding all_layers" \
AIM_RENDER_OUTPUT_DIR=.local/runs/my_cell/renders \
make aim-render-preset
```

Omit `AIM_RENDER_RUNS` to render every run.

### Render Directly

```sh
blender --background .local/runs/my_cell/blender/my_cell.realistic.blend \
  --python scripts/aim_render_scene.py -- \
  --preset configs/blender/render_presets/my_view.yaml \
  --run no_cladding \
  --run all_layers \
  --output-dir .local/runs/my_cell/renders
```

Validate without rendering:

```sh
blender --background .local/runs/my_cell/blender/my_cell.realistic.blend \
  --python scripts/aim_render_scene.py -- \
  --preset configs/blender/render_presets/my_view.yaml \
  --dry-run
```

Output filenames use:

```text
<blend>.<preset>.<run>.<extension>
```

The renderer restores baseline object visibility between runs.

## Render-Farm/HPC `.blend`

This target applies one preset run without rendering. The output contains the
preset camera, lighting, color management, render settings, visibility state,
packed resources, and a portable render path.

```sh
AIM_RENDER_BLEND=.local/runs/my_cell/blender/my_cell.realistic.blend \
AIM_RENDER_PRESET=configs/blender/render_presets/my_view.yaml \
AIM_RENDER_READY_RUN=all_layers \
AIM_RENDER_READY_BLEND=.local/runs/my_cell/render_ready/my_cell.render-ready.blend \
make aim-prepare-render-blend
```

Direct command:

```sh
blender --background .local/runs/my_cell/blender/my_cell.realistic.blend \
  --python scripts/aim_prepare_render_blend.py -- \
  --preset configs/blender/render_presets/my_view.yaml \
  --run all_layers \
  --output .local/runs/my_cell/render_ready/my_cell.render-ready.blend
```

Options:

- `AIM_RENDER_READY_OUTPUT` or `--render-output`: stored render path.
- `AIM_RENDER_READY_PACK=0` or `--no-pack-resources`: leave resources unpacked.
- `--overwrite-source`: allow replacing the input `.blend`.

Render on a worker:

```sh
blender --background my_cell.render-ready.blend --render-frame 1
```

## Scene Controls

### Camera Fit

The builder fits BlenderGDS's top-down camera to visible imported layers.
`AIM_BLENDER_CAMERA_FIT_MARGIN=1.10` gives a 10% distance margin. Use
`--no-fit-camera` with the direct builder command to retain BlenderGDS's camera.

### Vertical Exaggeration

```sh
AIM_BLENDER_Z_SCALE=20 make aim-build GDS=path/to/my_cell.gds
```

`1.0` preserves configured dimensions. Higher values scale layer elevation and
thickness without changing XY dimensions.

### Cladding Modes

| Mode | Behavior |
| --- | --- |
| `boolean` | Import cladding and cutters, subtract openings, and bake the result; default |
| `solid` | Import uncut cladding without cutters |
| `omit` | Import neither cladding nor cutters |

```sh
AIM_BLENDER_CLADDING_MODE=solid make aim-build GDS=path/to/my_cell.gds
```

The default path fractures cutter polygons to 256 points, graph-colors them
into non-touching batches, applies Blender's Manifold solver sequentially, and
bakes the result. Use `exact` only as a diagnostic fallback:

```sh
AIM_BLENDER_CLADDING_BOOLEAN_SOLVER=exact \
AIM_BLENDER_APPLY_CLADDING_BOOLEAN=0 \
make aim-build GDS=path/to/my_cell.gds
```

`AIM_BLENDER_APPLY_CLADDING_BOOLEAN=0` keeps live modifiers.

#### Unfractured Cutter

Use this path when fragmented TUAM batches produce shading artifacts:

```sh
conda run -n gds-blender-pipeline python \
  scripts/aim_generate_unfractured_cladding_cutter.py \
  --gds .local/runs/my_cell/visual/my_cell.visual.gds \
  --output .local/runs/my_cell/my_cell.unfractured-cutter.npz

AIM_BLENDER_CLADDING_UNDERCUT_METHOD=unfractured_cutter \
AIM_BLENDER_CLADDING_UNFRACTURED_CUTTER=.local/runs/my_cell/my_cell.unfractured-cutter.npz \
make aim-build GDS=path/to/my_cell.gds
```

The sidecar contains validated planar triangles and directed boundary loops.

#### Tiled Explicit Mesh

Use this path when a large Boolean result produces invalid Blender n-gon
tessellation:

```sh
conda run -n gds-blender-pipeline python \
  scripts/aim_generate_tiled_cladding_mesh.py \
  --gds .local/runs/my_cell/visual/my_cell.visual.gds \
  --output .local/runs/my_cell/my_cell.tiled-cladding.npz \
  --tile-size-um 200

AIM_BLENDER_CLADDING_UNDERCUT_METHOD=tiled_explicit_mesh \
AIM_BLENDER_CLADDING_EXPLICIT_MESH=.local/runs/my_cell/my_cell.tiled-cladding.npz \
make aim-build GDS=path/to/my_cell.gds
```

For large coordinates with nanometer-scale boundary segments, set
`AIM_BLENDER_CLADDING_EXPLICIT_CHUNK_SIZE_UM=400` to use chunk-local object
coordinates.

### Remove Layers

```sh
AIM_BLENDER_DELETE_LAYERS="cbam v1am vaam" \
make aim-build GDS=path/to/my_cell.gds
```

Short names are resolved to render-layer names such as `CBAM_RENDER`.

## Color Schemes

| File | Style |
| --- | --- |
| `configs/blender/colors/aim/realistic.yaml` | Physically plausible materials; default |
| `configs/blender/colors/aim/fancy.yaml` | High color and light contrast |
| `configs/blender/colors/aim/marketing.yaml` | Graphite, champagne, and platinum |

Build several schemes with the explicit target:

```sh
AIM_GDS=path/to/my_cell.gds \
AIM_VISUAL_GDS=.local/work/my_cell.visual.gds \
AIM_BLEND=.local/work/my_cell.blend \
AIM_BLENDER_COLORS="configs/blender/colors/aim/realistic.yaml configs/blender/colors/aim/fancy.yaml" \
make aim-build-scene
```

Color keys must match layer names in `configs/blender/aim.yaml`.

## Make Variables

### Tools And AIM Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `BLENDER` | `blender` | Blender executable |
| `AIM_TECH` | `external_pdks/AIMPhotonics_ACT1/tech.py` | Private AIM tech file |
| `AIM_RAW_CUSTOM` | `configs/aim/raw_custom_layers.yaml` | Custom raw-layer map |
| `AIM_DOPING_RULES` | `configs/aim/doping_rules.yaml` | Doping rules |
| `AIM_RENDER_STATIC` | `configs/aim/render_layers.static.yaml` | Static render layers |
| `AIM_LAYER_REGISTRY` | `configs/aim/layer_registry.local.yaml` | Generated registry |
| `AIM_BLENDERGDS_CONFIG` | `configs/blender/aim.yaml` | Generated BlenderGDS stack |

### Staged Workflow

| Variable | Default | Purpose |
| --- | --- | --- |
| `GDS` | empty | Raw GDS for `aim-build`, `aim-render`, and `aim-run` |
| `PRESET` | empty | Preset for `aim-render` and `aim-run` |
| `SCHEME` | `realistic` | Color scheme for the staged workflow |
| `AIM_RUN_ROOT` | `.local/runs` | Staged output root |

### Explicit Paths And Scene Controls

| Variable | Default | Purpose |
| --- | --- | --- |
| `AIM_GDS` | empty | Raw GDS input |
| `AIM_VISUAL_GDS` | empty | Visualization GDS output |
| `AIM_BLEND` | empty | Base scene output; color name is inserted |
| `AIM_BLENDER_COLORS` | `configs/blender/colors/aim/realistic.yaml` | One or more color files |
| `AIM_PREPROCESS_MAX_POLYGON_VERTICES` | `256` | Polygon fracture limit |
| `AIM_BLENDER_Z_SCALE` | `1.0` | Vertical scale |
| `AIM_BLENDER_CAMERA_FIT_MARGIN` | `1.10` | Camera-fit margin |
| `AIM_BLENDER_CLADDING_MODE` | `boolean` | `boolean`, `solid`, or `omit` |
| `AIM_BLENDER_CLADDING_BOOLEAN_SOLVER` | `manifold` | `manifold` or `exact` |
| `AIM_BLENDER_CLADDING_UNDERCUT_METHOD` | `fractured_batches` | Undercut construction method |
| `AIM_BLENDER_CLADDING_UNFRACTURED_CUTTER` | empty | Logical-opening sidecar |
| `AIM_BLENDER_CLADDING_EXPLICIT_MESH` | empty | Explicit cladding sidecar |
| `AIM_BLENDER_CLADDING_EXPLICIT_CHUNK_SIZE_UM` | `0` | Explicit-mesh chunk size |
| `AIM_BLENDER_APPLY_CLADDING_BOOLEAN` | `1` | Bake Boolean modifiers |
| `AIM_BLENDER_DELETE_LAYERS` | empty | Imported layers to remove |
| `AIM_BLENDER_MERGE_LAYERS` | `0` | Merge BlenderGDS layer fragments |

### Rendering

| Variable | Default | Purpose |
| --- | --- | --- |
| `AIM_RENDER_BLEND` | empty | Source scene |
| `AIM_RENDER_PRESET` | empty | Render preset |
| `AIM_RENDER_RUNS` | all runs | Selected preset runs |
| `AIM_RENDER_OUTPUT_DIR` | preset directory | Output override |
| `AIM_RENDER_READY_RUN` | `all_layers` | Run baked into a portable scene |
| `AIM_RENDER_READY_BLEND` | generated beside source | Portable scene output |
| `AIM_RENDER_READY_OUTPUT` | `//renders/<name>` | Stored render path |
| `AIM_RENDER_READY_PACK` | `1` | Pack external resources |

## Private AIM Configuration

These files are local and ignored by Git:

| Path | Generated | Purpose |
| --- | --- | --- |
| `external_pdks/AIMPhotonics_ACT1/tech.py` | No | AIM tech file with `LayerMapAIM` |
| `configs/aim/raw_custom_layers.yaml` | No | Custom raw markers |
| `configs/aim/doping_rules.yaml` | No | Silicon and doping rules |
| `configs/aim/render_layers.static.yaml` | No | Static render layers |
| `configs/aim/render_layers.doping.generated.yaml` | Yes | Doping-derived render layers |
| `configs/aim/render_layers.yaml` | Yes | Merged render layers |
| `configs/aim/layer_registry.local.yaml` | Yes | Resolved registry |
| `configs/blender/aim.yaml` | Yes | BlenderGDS stack |

The registry builder parses the tech file with Python AST; it does not import
the PDK.

### Minimal `raw_custom_layers.yaml`

```yaml
raw_custom_layers:
  CUSTOM_MARKER:
    layer: [LAYER_NUMBER, DATATYPE]
    description: "Local marker description"
```

### Minimal `doping_rules.yaml`

```yaml
units:
  xy: um
  z: um

silicon_bodies:
  BODY_NAME:
    expression: "RAW_LAYER_EXPR"
    material_mode: single
    slice_name: full
    zmin: 0.0
    thickness: 0.1

doping_markers:
  MARKER_NAME:
    polarity: n
    rank: 10
    vertical_scope: full_height
    applies_to:
      BODY_NAME: [full]

render_output:
  intrinsic_template_sliced: "{body}_I_{slice}_RENDER"
  intrinsic_template_single: "{body}_I_RENDER"
  doped_template_sliced: "{body}_{polarity}{rank}_{slice}_RENDER"
  doped_template_single: "{body}_{polarity}{rank}_RENDER"
  conflict_template_sliced: "{body}_PN_CONFLICT_{slice}_RENDER"
  conflict_template_single: "{body}_PN_CONFLICT_RENDER"
```

### Minimal `render_layers.static.yaml`

```yaml
derived_regions:
  DERIVED_NAME:
    source: RAW_LAYER_NAME
    empty_source_policy: empty
    operations:
      - type: offset
        distance: 1.0
        join: round
        tolerance: 72

render_layers:
  STATIC_RENDER_LAYER:
    layer: [2000, 0]
    source: static
    role: custom
    material_class: custom_material
    expression: "RAW_LAYER_NAME | DERIVED_NAME"
    zmin: 0.0
    thickness: 1.0
```

## Local Make Targets

The tracked Makefile optionally includes `Makefile.local`, which is ignored by
Git. Put personal layout paths and debug targets there.

## Cleaning

```sh
make aim-clean-generated
```

This removes generated AIM YAML and `configs/blender/aim.yaml`. It does not
remove scenes, renders, private PDK files, or hand-maintained configuration.

## Troubleshooting

| Error | Check |
| --- | --- |
| Missing tech file | `AIM_TECH` and the private PDK path |
| Missing `LayerMapAIM` | Tech file and class name |
| BlenderGDS operator unavailable | GDSII Importer installation in the selected Blender profile |
| Missing render layer | Expressions in the tech file and local YAML |
| Empty output layer | Raw layout layers and derived-region offsets |
| Missing preset layer | Object names in the source `.blend` |
| Missing farm asset | Resource packing or staged shared assets |
