# GDS Blender Pipeline

Turn an AIM Photonics GDS layout into a Blender scene, render it locally from a
reusable preset, or export a render-ready `.blend` for a render farm or HPC
cluster.

The pipeline creates visualization geometry rather than a fabrication GDS. It
resolves AIM layers and silicon doping, writes explicit render layers with z
heights, imports them through BlenderGDS, and applies AIM-specific materials and
post-processing.

All commands below are run from the repository root. Private foundry data, raw
layouts, generated GDS files, renders, and `.blend` files are ignored by Git.

## Workflows At A Glance

```text
private AIM config + raw GDS
              |
              v
     generated layer metadata
              |
              v
       visual/render GDS
              |
              v
       editable Blender scene
          /              \
         v                v
 local preset renders   render-ready .blend
                         for farm/HPC
```

| Goal | Make target | Main input | Main output |
| --- | --- | --- | --- |
| [Set up Python dependencies](#requirements-and-setup) | `make env` | `environment.yml` | Conda environment `gds-blender-pipeline` |
| [Generate AIM layer metadata](#workflow-1-generate-aim-metadata) | `make aim-registry aim-blendergds-config` | Private AIM YAML and PDK tech file | Generated YAML under `configs/aim/` and `configs/blender/aim.yaml` |
| [Preprocess one GDS](#workflow-2-preprocess-raw-gds) | `make aim-preprocess-example` | `examples/<case>/raw/<file>.gds` | `examples/<case>/visual/<file>.visual.gds` |
| [Preprocess every GDS in a case](#workflow-2-preprocess-raw-gds) | `make aim-preprocess-all-examples` | `examples/<case>/raw/*.gds` | Matching files under `examples/<case>/visual/` |
| [Build editable Blender scenes](#workflow-3-build-an-editable-blender-scene) | `make aim-blender-scene-example` | One visual GDS, stack config, color YAML | One `.blend` per color scheme under `examples/<case>/blender/` |
| [Render an existing scene locally](#workflow-4-render-locally-from-a-preset) | `make aim-render-preset` | Existing `.blend` and render preset | Images in the preset output directory |
| [Prepare a farm/HPC artifact](#workflow-5-prepare-a-render-farmhpc-blend) | `make aim-prepare-render-blend` | Existing `.blend`, preset, and one run | Portable `.blend` under `examples/<case>/render_ready/` |
| [Remove generated local files](#cleaning-generated-files) | `make aim-clean-generated` | Generated files | Generated YAML plus visual GDS and Blender files for the active case removed |

Use Make for the repository defaults and repeatable workflows. Use the direct
shell commands in the sections below when working with arbitrary paths or when
you need script-specific options.

## Folder And File Layout

The recommended case layout is:

```text
gds_blender_pipeline/
├── external_pdks/
│   └── AIMPhotonics_ACT1/tech.py       # private input
├── configs/
│   ├── aim/
│   │   ├── raw_custom_layers.yaml      # private input
│   │   ├── doping_rules.yaml           # private input
│   │   ├── render_layers.static.yaml   # private input
│   │   ├── render_layers.yaml          # generated
│   │   └── layer_registry.local.yaml   # generated
│   └── blender/
│       ├── aim.yaml                     # generated BlenderGDS stack
│       ├── colors/aim/*.yaml            # tracked material presets
│       └── render_presets/*.yaml        # tracked camera/render presets
├── examples/
│   ├── aim/                             # default AIM workflow case
│   │   ├── raw/*.gds                    # AIM input layouts
│   │   ├── visual/*.visual.gds          # generated visualization GDS
│   │   ├── blender/
│   │   │   ├── *.blend                  # editable scenes built from GDS
│   │   │   └── renders/                 # local preset renders
│   │   └── render_ready/
│   │       ├── *.blend                  # prepared farm/HPC artifacts
│   │       └── renders/                 # prepared-file render output
│   ├── sky130/                          # SKY130 example layouts
│   └── <case>/                          # optional additional cases
└── scripts/                             # pipeline commands
```

`examples/aim/` is the default for the AIM Make targets. The current SKY130
layout is `examples/sky130/wrapped_vga_clock.gds`; it is kept as a separate
example and is not processed by the AIM-specific layer rules.

The important input/output convention is:

| Directory | Role | Created by pipeline? | Tracked by Git? |
| --- | --- | --- | --- |
| `external_pdks/` | Private PDK source | No | No |
| `configs/aim/` | Private process rules plus generated AIM metadata | Partly | No |
| `configs/blender/colors/aim/` | Material/color schemes | No | Yes |
| `configs/blender/render_presets/` | Camera, Cycles, output, and visibility presets | No | Yes |
| `examples/<case>/raw/` | Raw GDS input | No | No |
| `examples/<case>/visual/` | Preprocessed render GDS | Yes | No |
| `examples/<case>/blender/` | Editable Blender scenes built from visual GDS | Yes | No |
| `examples/<case>/blender/renders/` | Images rendered directly from editable scenes | Yes | No |
| `examples/<case>/render_ready/` | Prepared render-farm/HPC `.blend` files | Yes | No |
| `examples/<case>/render_ready/renders/` | Images rendered from prepared files | Yes | No |

## Requirements And Setup

Install these separately:

- Conda or Mamba.
- `make`.
- Blender.
- BlenderGDS installed and enabled in Blender. The scene builder searches for
  the `bpy.ops.import_scene.gdsii` operator and tries common add-on module names,
  but it cannot install BlenderGDS.
- The private AIM inputs listed in [Private AIM Configuration](#private-aim-configuration).

Create or update the Python environment and verify it:

```sh
make env
make env-info
```

The Conda environment runs the GDS preprocessing scripts. Blender and
BlenderGDS are managed separately. If Blender is not on `PATH`, provide it to
any Make workflow with:

```sh
BLENDER=/path/to/blender make aim-blender-scene-example
```

## Quick Start: Build The Default Example

After installing the requirements and supplying the private AIM files:

```sh
make env
make aim-blender-scene-example
```

That single scene command runs its prerequisites automatically:

1. Generate and merge render-layer metadata.
2. Build the local layer registry and BlenderGDS stack.
3. Preprocess the default raw GDS.
4. Build a `.blend` for every color scheme.

Default input:

```text
examples/aim/raw/tx_array_checkered.gds
```

Default outputs:

```text
examples/aim/visual/tx_array_checkered.visual.gds
examples/aim/blender/tx_array_checkered.fancy.blend
examples/aim/blender/tx_array_checkered.marketing.blend
examples/aim/blender/tx_array_checkered.realistic.blend
```

Open one in Blender to adjust the scene and discover a camera/render preset:

```sh
blender examples/aim/blender/tx_array_checkered.realistic.blend
```

## Run The Pipeline On Your Own Layout

Create a case directory and place the input GDS under `raw/`:

```text
examples/my_case/raw/my_cell.gds
```

### Complete Workflow With Make

For one layout and one color scheme:

```sh
EXAMPLE_DIR=examples/my_case \
EXAMPLE_GDS=examples/my_case/raw/my_cell.gds \
EXAMPLE_VISUAL_GDS=examples/my_case/visual/my_cell.visual.gds \
EXAMPLE_BLEND=examples/my_case/blender/my_cell.blend \
AIM_BLENDER_COLORS=configs/blender/colors/aim/realistic.yaml \
make aim-blender-scene-example
```

Because the target inserts the color-scheme name before `.blend`, the final
scene is:

```text
examples/my_case/blender/my_cell.realistic.blend
```

Omit `AIM_BLENDER_COLORS` to build one scene for every YAML file under
`configs/blender/colors/aim/`.

To preprocess every `.gds` in `examples/my_case/raw/`:

```sh
EXAMPLE_DIR=examples/my_case make aim-preprocess-all-examples
```

### Complete Workflow With Direct Commands

Run the metadata workflow once after process-rule changes, then preprocess and
build the layout:

```sh
conda run -n gds-blender-pipeline python scripts/aim_preprocess_gds.py \
  --input examples/my_case/raw/my_cell.gds \
  --registry configs/aim/layer_registry.local.yaml \
  --output examples/my_case/visual/my_cell.visual.gds \
  --max-polygon-vertices 256

blender --background --python scripts/aim_build_blender_scene.py -- \
  --gds examples/my_case/visual/my_cell.visual.gds \
  --stack-config configs/blender/aim.yaml \
  --color-config configs/blender/colors/aim/realistic.yaml \
  --output examples/my_case/blender/my_cell.realistic.blend \
  --z-scale 1 \
  --camera-fit-margin 1.10 \
  --cladding-mode boolean \
  --no-merge-layers
```

Inputs:

- Raw GDS: `examples/my_case/raw/my_cell.gds`.
- Generated registry: `configs/aim/layer_registry.local.yaml`.
- Generated BlenderGDS stack: `configs/blender/aim.yaml`.
- Tracked color scheme: `configs/blender/colors/aim/realistic.yaml`.

Outputs:

- Visual GDS: `examples/my_case/visual/my_cell.visual.gds`.
- Blender scene: `examples/my_case/blender/my_cell.realistic.blend`.

## Workflow 1: Generate AIM Metadata

Run this workflow whenever the PDK tech file, raw custom layers, doping rules,
or static render layers change.

### With Make

```sh
make aim-registry aim-blendergds-config
```

These targets create:

```text
configs/aim/render_layers.doping.generated.yaml
configs/aim/render_layers.yaml
configs/aim/layer_registry.local.yaml
configs/blender/aim.yaml
```

Individual targets are also available:

```sh
make aim-render-layers
make aim-registry
make aim-blendergds-config
```

### With Direct Commands

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

## Workflow 2: Preprocess Raw GDS

Preprocessing flattens the input GDS, generates substrate and cladding regions,
resolves silicon doping into explicit render layers, writes PN-conflict debug
layers, and copies static expression layers such as waveguides, contacts, vias,
metals, black-box proxies, and the PAAM passivation-opening cutter.

### With Make

Default example:

```sh
make aim-preprocess-example
```

Custom case:

```sh
EXAMPLE_GDS=examples/my_case/raw/my_cell.gds \
EXAMPLE_VISUAL_GDS=examples/my_case/visual/my_cell.visual.gds \
make aim-preprocess-example
```

The Make target also regenerates metadata when required.

### With A Direct Command

```sh
conda run -n gds-blender-pipeline python scripts/aim_preprocess_gds.py \
  --input examples/my_case/raw/my_cell.gds \
  --registry configs/aim/layer_registry.local.yaml \
  --output examples/my_case/visual/my_cell.visual.gds
```

## Workflow 3: Build An Editable Blender Scene

### With Make

```sh
make aim-blender-scene-example
```

This builds one scene per selected color scheme. See
[Make Variable Reference](#make-variable-reference) for path and geometry
overrides.

### With A Direct Command

```sh
blender --background --python scripts/aim_build_blender_scene.py -- \
  --gds examples/my_case/visual/my_cell.visual.gds \
  --stack-config configs/blender/aim.yaml \
  --color-config configs/blender/colors/aim/realistic.yaml \
  --output examples/my_case/blender/my_cell.realistic.blend \
  --no-merge-layers
```

The builder removes PN-conflict debug objects by default, applies materials,
handles the cladding Boolean, fits the camera, and saves the scene. See
[Scene-Building Controls](#scene-building-controls) for optional geometry and
camera settings.

## Workflow 4: Render Locally From A Preset

A render preset stores the camera, lighting, color management, Cycles settings,
output directory, and one or more named layer-visibility runs. Presets live
under:

```text
configs/blender/render_presets/*.yaml
```

The included `trx_top_oblique_100mm.yaml` defines these runs:

- `no_backend_or_cladding`: hide CBAM, M1AM, V1AM, M2AM, VAAM, MLAM, and cladding.
- `no_cladding`: hide only cladding.
- `all_layers`: retain the source `.blend` file's baseline visibility.

After adjusting a scene interactively in Blender, copy the camera location,
rotation in degrees, and lens from the Camera properties into a new preset.
For example, save this as `configs/blender/render_presets/my_view.yaml`:

```yaml
version: 1
name: my_view

camera:
  object: Camera
  type: PERSP
  location: [6050.0, 50.0, 10000.0]
  rotation_degrees: [29.527, 0.0, 32.57]
  lens_mm: 100.0

lighting:
  sun:
    object: Sun
    strength: 6.0

color_management:
  view_transform: AgX
  look: AgX - High Contrast

render:
  engine: CYCLES
  samples: 1024
  denoise: false
  adaptive_sampling: false
  file_format: PNG

output:
  directory: examples/my_case/blender/renders/my_view

runs:
  - name: no_cladding
    hide_layers: [cladding]
  - name: all_layers
    hide_layers: []
```

Preset layer names accept short names such as `cladding` or `cbam`; the runner
normalizes them to Blender render-layer object names. Presets currently support
perspective cameras and Cycles. Render resolution remains the value saved in
the source scene, and CPU/GPU device selection remains a machine-side setting.

### With Make

Render every run in the preset:

```sh
make aim-render-preset
```

Choose the scene, selected runs, and output directory:

```sh
AIM_RENDER_BLEND=examples/my_case/blender/my_cell.realistic.blend \
AIM_RENDER_PRESET=configs/blender/render_presets/my_view.yaml \
AIM_RENDER_RUNS="no_cladding all_layers" \
AIM_RENDER_OUTPUT_DIR=examples/my_case/blender/renders/my_view \
make aim-render-preset
```

If `AIM_RENDER_OUTPUT_DIR` is omitted, images go to the preset's
`output.directory`. Output filenames have this form:

```text
<blend>.<preset>.<run>.<image-extension>
```

### With A Direct Command

```sh
blender --background examples/my_case/blender/my_cell.realistic.blend \
  --python scripts/aim_render_scene.py -- \
  --preset configs/blender/render_presets/my_view.yaml \
  --run no_cladding \
  --run all_layers \
  --output-dir examples/my_case/blender/renders/my_view
```

Omit every `--run` flag to render all runs. Validate the preset, layer names,
and paths without writing images with:

```sh
blender --background examples/my_case/blender/my_cell.realistic.blend \
  --python scripts/aim_render_scene.py -- \
  --preset configs/blender/render_presets/my_view.yaml \
  --dry-run
```

The renderer restores baseline object visibility between runs, so one run
cannot leak visibility changes into the next.

## Workflow 5: Prepare A Render-Farm/HPC `.blend`

This workflow applies a preset without rendering locally. The exporter bakes
one visibility state per output file, so select exactly one preset run and
create one prepared `.blend` per farm job variant.

The prepared artifact contains:

- Preset camera, lighting, color-management, and Cycles settings.
- The selected run's `hide_render` state.
- A portable render path under `//renders/` by default.
- Packed external resources by default.
- Scene metadata recording the source filename, preset, and run.

No repository scripts are needed on the render node.

### With Make

```sh
AIM_RENDER_BLEND=examples/my_case/blender/my_cell.realistic.blend \
AIM_RENDER_PRESET=configs/blender/render_presets/my_view.yaml \
AIM_RENDER_READY_RUN=no_cladding \
AIM_RENDER_READY_BLEND=examples/my_case/render_ready/my_cell.no-cladding.render-ready.blend \
make aim-prepare-render-blend
```

If `AIM_RENDER_READY_BLEND` is omitted and the source is under the case's
`blender/` directory, the output is named:

```text
examples/<case>/render_ready/<source>.<preset>.<run>.blend
```

This keeps editable scenes imported from GDS separate from files prepared for
deployment. For a source outside a directory named `blender`, the exporter
creates `render_ready/` beside the source file.

Set `AIM_RENDER_READY_OUTPUT` to change the render path stored inside the file.
Set `AIM_RENDER_READY_PACK=0` if shared resources will be staged separately.

### With A Direct Command

```sh
blender --background examples/my_case/blender/my_cell.realistic.blend \
  --python scripts/aim_prepare_render_blend.py -- \
  --preset configs/blender/render_presets/my_view.yaml \
  --run no_cladding \
  --output examples/my_case/render_ready/my_cell.no-cladding.render-ready.blend
```

Useful options:

- `--render-output //another/relative/path` changes the stored render path.
- `--no-pack-resources` leaves external resources unpacked.
- `--overwrite-source` explicitly allows replacing the source `.blend`.

After transferring the file, render a frame on the worker with:

```sh
blender --background my_cell.no-cladding.render-ready.blend --render-frame 1
```

Cycles CPU/GPU device selection remains a farm-side choice so the scheduler can
use the appropriate arguments for each node type.

## Scene-Building Controls

### Camera Fitting

The builder preserves GDS XY dimensions and automatically fits BlenderGDS's
top-down camera to visible imported layers. Camera distance and clipping planes
follow the chip bounding box; the Sun transform and chip geometry are not
scaled.

The default `--camera-fit-margin 1.10` gives a 10% camera-distance margin,
approximately 4.5% image-space padding on each side of the limiting dimension.
Use `--no-fit-camera` to retain BlenderGDS's fixed camera placement.

### Vertical Exaggeration

`--z-scale` changes layer elevations and thicknesses without changing XY:

```sh
AIM_BLENDER_Z_SCALE=20 make aim-blender-scene-example
```

`z-scale=1` preserves configured physical dimensions. Values above one are
visualization aids and should be recorded with rendered output. The scale
applies to every configured layer, including substrate and cladding depths.

### Cladding

Choose a cladding mode with `--cladding-mode` or
`AIM_BLENDER_CLADDING_MODE`:

```text
boolean  Import cladding and both cutters, then add the opening Booleans (default).
solid    Import solid, uncut cladding without importing either cutter.
omit     Import neither cladding nor its cutters.
```

The cladding extends from `z = -2.000 um` through a `0.500 um` passivation cap
above the MLAM top, ending at `z = 5.980 um`. The full-height TUAM-derived
undercut cutter opens the cladding down through the BOX. A separate cutter copied
from raw PAAM opens only the passivation cap: it spans `z = 5.479` to
`5.981 um`, providing a `0.001 um` Boolean overlap below the MLAM top and above
the cladding surface. This avoids coincident cladding/MLAM top faces while
exposing top metal only inside PAAM.

For example:

```sh
AIM_BLENDER_CLADDING_MODE=solid make aim-blender-scene-example
```

The `solid` and `omit` modes filter the temporary BlenderGDS stack before
extrusion, which avoids creating expensive meshes for large layouts. The older
`--no-cladding-boolean` flag remains a deprecated alias for
`--cladding-mode solid`.

The 256-point preprocessing limit remains in effect. The TUAM and PAAM cladding
cutters are graph-colored into non-touching batches and subtracted with Exact
Boolean modifiers. Use `--apply-cladding-boolean` to bake the same batched
modifiers immediately. The Make equivalent is:

```sh
AIM_BLENDER_APPLY_CLADDING_BOOLEAN=1 \
make aim-blender-scene-example
```

### Removing Large Layers

Remove additional imported layers from the saved `.blend` with repeated or
comma-separated `--delete-layers` values. Short AIM names such as `cbam` resolve
to names such as `CBAM_RENDER`:

```sh
blender --background --python scripts/aim_build_blender_scene.py -- \
  --gds examples/my_case/visual/my_cell.visual.gds \
  --stack-config configs/blender/aim.yaml \
  --color-config configs/blender/colors/aim/realistic.yaml \
  --output examples/my_case/blender/my_cell.realistic.blend \
  --delete-layers cbam,v1am,vaam
```

The Make equivalent is:

```sh
AIM_BLENDER_DELETE_LAYERS="cbam v1am vaam" make aim-blender-scene-example
```

## Color Schemes

Material schemes live under `configs/blender/colors/aim/`:

- `realistic.yaml`: muted, physically plausible materials.
- `fancy.yaml`: high color and light contrast grouped by material role.
- `marketing.yaml`: shiny graphite, champagne, and platinum styling.

Build only one scheme with:

```sh
AIM_BLENDER_COLORS=configs/blender/colors/aim/fancy.yaml \
make aim-blender-scene-example
```

Color YAML keys must match render-layer names in `configs/blender/aim.yaml`.
After adding or renaming render layers, regenerate the stack and update the
color files. Validate the mapping with:

```sh
conda run -n gds-blender-pipeline python -c '
from pathlib import Path
import yaml

stack = yaml.safe_load(Path("configs/blender/aim.yaml").read_text())
for path in sorted(Path("configs/blender/colors/aim").glob("*.yaml")):
    layers = yaml.safe_load(path.read_text())["layers"]
    missing = [key for key in stack if key not in layers]
    extra = [key for key in layers if key not in stack]
    print(path.name, "missing", missing, "extra", extra)
'
```

## Make Variable Reference

All variables can be overridden on the command line as shown in the examples.

### Tools And AIM Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `BLENDER` | `blender` | Blender executable |
| `AIM_TECH` | `external_pdks/AIMPhotonics_ACT1/tech.py` | Private AIM tech file |
| `AIM_RAW_CUSTOM` | `configs/aim/raw_custom_layers.yaml` | Custom raw-layer map |
| `AIM_DOPING_RULES` | `configs/aim/doping_rules.yaml` | Silicon doping rules |
| `AIM_RENDER_STATIC` | `configs/aim/render_layers.static.yaml` | Static render layers |
| `AIM_LAYER_REGISTRY` | `configs/aim/layer_registry.local.yaml` | Generated registry |
| `AIM_BLENDERGDS_CONFIG` | `configs/blender/aim.yaml` | Generated BlenderGDS stack |

### Example And Scene Paths

| Variable | Default | Purpose |
| --- | --- | --- |
| `EXAMPLE_DIR` | `examples/aim` | Active case directory |
| `EXAMPLE_GDS` | `<EXAMPLE_DIR>/raw/tx_array_checkered.gds` | Raw GDS input |
| `EXAMPLE_VISUAL_GDS` | `<EXAMPLE_DIR>/visual/tx_array_checkered.visual.gds` | Visual GDS output |
| `EXAMPLE_BLEND` | `<EXAMPLE_DIR>/blender/tx_array_checkered.blend` | Base scene output; color name is inserted |
| `EXAMPLE_RENDER_READY_DIR` | `<EXAMPLE_DIR>/render_ready` | Prepared farm/HPC artifacts |
| `AIM_BLENDER_COLORS` | Every `configs/blender/colors/aim/*.yaml` | Color schemes to build |
| `AIM_BLENDER_Z_SCALE` | `1.0` | Vertical scale only |
| `AIM_BLENDER_CAMERA_FIT_MARGIN` | `1.10` | Automatic camera-fit margin |
| `AIM_BLENDER_CLADDING_MODE` | `boolean` | `boolean`, `solid`, or `omit` |
| `AIM_BLENDER_DELETE_LAYERS` | empty | Imported layers to remove |

### Render And Farm Output

| Variable | Default | Purpose |
| --- | --- | --- |
| `AIM_RENDER_BLEND` | Example `trx_top.realistic.blend` | Existing source scene |
| `AIM_RENDER_PRESET` | `configs/blender/render_presets/trx_top_oblique_100mm.yaml` | Render preset |
| `AIM_RENDER_RUNS` | empty/all runs | Runs selected for local rendering |
| `AIM_RENDER_OUTPUT_DIR` | preset directory | Override local render directory |
| `AIM_RENDER_READY_RUN` | `all_layers` | One run baked into a prepared `.blend` |
| `AIM_RENDER_READY_BLEND` | `<case>/render_ready/<generated-name>.blend` | Prepared `.blend` output path |
| `AIM_RENDER_READY_OUTPUT` | `//renders/<prepared-name>` | Render path stored in prepared file |
| `AIM_RENDER_READY_PACK` | `1` | Pack external resources; use `0` to disable |

## Private AIM Configuration

These files must be supplied locally and must not be committed:

| Path | Required | Generated | Purpose |
| --- | --- | --- | --- |
| `external_pdks/AIMPhotonics_ACT1/tech.py` | Yes | No | AIM PDK tech file containing `LayerMapAIM` |
| `configs/aim/raw_custom_layers.yaml` | Yes | No | Custom raw markers absent from the tech file |
| `configs/aim/doping_rules.yaml` | Yes | No | Silicon bodies, slices, polarity, priority, and numbering |
| `configs/aim/render_layers.static.yaml` | Yes | No | Substrate, cladding, waveguides, contacts, vias, and metals |
| `configs/aim/render_layers.doping.generated.yaml` | No | Yes | Doping-derived render layers |
| `configs/aim/render_layers.yaml` | No | Yes | Merged doping and static layers |
| `configs/aim/layer_registry.local.yaml` | No | Yes | Resolved input/output layer registry |
| `configs/blender/aim.yaml` | No | Yes | BlenderGDS z-stack config |

The registry builder parses the tech file with Python AST and does not import
the PDK. Internal/private copies remain the source of truth for AIM layer
numbers and process assumptions.

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
    role: example
    material_class: example_material
    expression: "RAW_LAYER_NAME | DERIVED_NAME"
    zmin: 0.0
    thickness: 1.0
```

## Cleaning Generated Files

```sh
make aim-clean-generated
```

This removes generated AIM YAML, `configs/blender/aim.yaml`, visual GDS files
for the active example, editable `.blend` files in its Blender directory, and
prepared `.blend` files in its `render_ready` directory. It does not remove
private PDK files, hand-maintained AIM configuration, or renders in nested
directories.

## Git Hygiene

Before committing, inspect tracked, untracked, and ignored files:

```sh
git status --short
git status --ignored --short
git ls-files --others --exclude-standard
```

To find the rule that ignores a file:

```sh
git check-ignore -v path/to/file
```

Do not commit PDK tech files, private AIM layer maps, raw foundry GDS, generated
visual GDS, or `.blend` output unless it has been deliberately sanitized and
the ignore policy has been changed for that artifact.

## Troubleshooting

- `FileNotFoundError: external_pdks/.../tech.py`: place the private AIM tech
  file at `AIM_TECH`, or override that variable with its actual path.
- `Could not find class LayerMapAIM`: pass the correct `--class-name`, or check
  the PDK tech path.
- `BlenderGDS operator ... is not available`: install and enable BlenderGDS in
  the Blender executable being used.
- Missing render layer during preprocessing: verify that expressions in
  `doping_rules.yaml` and `render_layers.static.yaml` reference names available
  in the tech file or `raw_custom_layers.yaml`.
- Empty expected output layers: check that the raw GDS contains the source
  layers and that derived-region offsets are not too aggressive.
- A preset run reports a missing layer: use a preset created for that scene, or
  update the run's `hide_layers` list to match objects in the `.blend`.
- Blender cannot find a farm asset: prepare again with resource packing enabled,
  or stage the shared asset at the path expected by the `.blend`.
