# gds_blender_pipeline

Utilities for turning AIM Photonics GDS layouts into visualization-focused GDS
and Blender scenes. The pipeline reads a raw layout, resolves AIM layers and
silicon doping rules, emits render-only GDS layers with explicit z heights, then
uses BlenderGDS plus AIM-specific post-processing to produce a `.blend` scene.

This repository is intentionally split between tracked code and local/private
foundry data. Foundry-derived files, raw layouts, generated GDS, and Blender
outputs are ignored by Git.

## What You Need To Supply

These files are required locally but should not be committed:

| Path | Required | Generated | Purpose |
| --- | --- | --- | --- |
| `external_pdks/AIMPhotonics_ACT1/tech.py` | yes | no | AIM PDK tech file containing `LayerMapAIM`. The registry builder parses this file with Python AST, so it does not import the PDK. |
| `configs/aim/raw_custom_layers.yaml` | yes | no | Local layer map for custom raw markers not defined in the AIM tech file. |
| `configs/aim/doping_rules.yaml` | yes | no | Silicon body, slice, polarity, priority, and render-layer numbering rules. |
| `configs/aim/render_layers.static.yaml` | yes | no | Hand-maintained non-doping render layers: substrate, cladding, TUAM-derived cutters, nitride waveguides, contacts, vias, metals, black-box proxies. |
| `examples/<case>/raw/*.gds` | yes for examples | no | Raw input layouts to preprocess. |
| `configs/aim/render_layers.doping.generated.yaml` | no | yes | Doping-derived render layers generated from `doping_rules.yaml`. |
| `configs/aim/render_layers.yaml` | no | yes | Merged doping and static render-layer metadata. |
| `configs/aim/layer_registry.local.yaml` | no | yes | Local resolved registry combining PDK layers, custom raw layers, render layers, and processing rules. |
| `configs/blender/aim.yaml` | no | yes | BlenderGDS stack config generated from render-layer metadata. |

Tracked files that are safe to edit include the scripts, `Makefile`,
`environment.yml`, and color schemes such as
`configs/blender/colors/aim/realistic.yaml`.

### Minimal Local Config Shapes

`configs/aim/raw_custom_layers.yaml`:

```yaml
raw_custom_layers:
  CUSTOM_MARKER:
    layer: [LAYER_NUMBER, DATATYPE]
    description: "Local marker description"
```

`configs/aim/doping_rules.yaml` defines processing semantics rather than raw PDK
layer numbers. At minimum it should provide:

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

`configs/aim/render_layers.static.yaml` should contain optional
`derived_regions` plus a `render_layers` mapping. Render layer entries usually
include a target GDS layer/datatype, z placement, thickness, material class, and
either an input expression or a preprocessing action:

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

Use your internal/private copies as the real source of truth for AIM layer
numbers and process assumptions.

## Requirements

- Conda or Mamba.
- `make`.
- Blender.
- BlenderGDS installed and enabled in Blender. The scene builder looks for the
  `bpy.ops.import_scene.gdsii` operator and tries common add-on module names,
  but it cannot install BlenderGDS for you.
- Local AIM PDK tech and AIM config files listed above.

Create or update the Python environment:

```sh
make env
make env-info
```

The Conda environment installs Python dependencies used by the preprocessing
scripts. Blender and BlenderGDS are managed separately.

## Pipeline

The default AIM flow is:

1. Generate doping render layers from `configs/aim/doping_rules.yaml`.
2. Merge doping-generated layers with `configs/aim/render_layers.static.yaml`.
3. Parse `external_pdks/AIMPhotonics_ACT1/tech.py` and build
   `configs/aim/layer_registry.local.yaml`.
4. Generate `configs/blender/aim.yaml` for BlenderGDS.
5. Preprocess raw GDS into visual/render GDS.
6. Import the visual GDS in Blender, apply AIM-specific booleans/materials, and
   save a `.blend` file.

The preprocessing step flattens the input GDS, generates substrate and cladding
volumes from the layout bounding box, resolves silicon doping into explicit
render layers, writes PN-conflict debug layers, and copies static expression
layers such as nitride waveguides, contacts, vias, metals, and black-box
proxies.

## Makefile Usage

Build generated AIM render metadata:

```sh
make aim-render-layers
```

Build the local layer registry:

```sh
make aim-registry
```

Generate the BlenderGDS stack config:

```sh
make aim-blendergds-config
```

Preprocess the default example:

```sh
make aim-preprocess-example
```

Build default example Blender scenes for every AIM color scheme:

```sh
make aim-blender-scene-example
```

The default output files are written under `examples/aim_custom_tx_cell_undercut/blender/`
with the color scheme inserted before `.blend`, such as
`tx_array_checkered.fancy.blend`, `tx_array_checkered.marketing.blend`, and
`tx_array_checkered.realistic.blend`.

Use a specific Blender executable if `blender` is not on your `PATH`:

```sh
BLENDER=/path/to/blender make aim-blender-scene-example
```

Clean generated local artifacts:

```sh
make aim-clean-generated
```

`aim-clean-generated` removes generated AIM YAML, generated BlenderGDS stack
YAML, visual GDS outputs, and `.blend` outputs. It does not remove private PDK
or hand-maintained AIM config files.

## Running On Your Own Layout

Place raw layouts under an ignored directory such as:

```text
examples/my_case/raw/my_cell.gds
```

Preprocess one layout directly:

```sh
conda run -n gds-blender-pipeline python scripts/aim_preprocess_gds.py \
  --input examples/my_case/raw/my_cell.gds \
  --registry configs/aim/layer_registry.local.yaml \
  --output examples/my_case/visual/my_cell.visual.gds
```

Build a Blender scene directly:

```sh
blender --background --python scripts/aim_build_blender_scene.py -- \
  --gds examples/my_case/visual/my_cell.visual.gds \
  --stack-config configs/blender/aim.yaml \
  --color-config configs/blender/colors/aim/realistic.yaml \
  --output examples/my_case/blender/my_cell.blend
```

The scene builder removes PN-conflict debug objects by default. To also strip
large imported render layers from the saved `.blend`, pass a comma-separated
or space-separated list with `--delete-layers`. Short AIM names such as `cbam`
are resolved to render-layer names such as `CBAM_RENDER`:

```sh
blender --background --python scripts/aim_build_blender_scene.py -- \
  --gds examples/my_case/visual/my_cell.visual.gds \
  --stack-config configs/blender/aim.yaml \
  --color-config configs/blender/colors/aim/realistic.yaml \
  --output examples/my_case/blender/my_cell.blend \
  --delete-layers cbam,v1am,vaam
```

To preprocess every `.gds` file in an example raw directory:

```sh
EXAMPLE_DIR=examples/my_case make aim-preprocess-all-examples
```

The `aim-blender-scene-example` target is tuned for one layout at a time and
renders all schemes listed in `AIM_BLENDER_COLORS`. For a different file name,
override `EXAMPLE_GDS`, `EXAMPLE_VISUAL_GDS`, and `EXAMPLE_BLEND`; `EXAMPLE_BLEND`
is used as the base output path, with the scheme name inserted before `.blend`.
For a single custom output, use the direct Blender command above. To drop extra
layers when using the Makefile target, set `AIM_BLENDER_DELETE_LAYERS`:

```sh
AIM_BLENDER_DELETE_LAYERS="cbam v1am vaam" make aim-blender-scene-example
```

## Useful Direct Commands

Generate doping-only render layers:

```sh
conda run -n gds-blender-pipeline python scripts/aim_generate_doping_render_layers.py \
  --doping-rules configs/aim/doping_rules.yaml \
  --output configs/aim/render_layers.doping.generated.yaml
```

Merge generated and static render layers:

```sh
conda run -n gds-blender-pipeline python scripts/aim_merge_render_layers.py \
  --doping configs/aim/render_layers.doping.generated.yaml \
  --static configs/aim/render_layers.static.yaml \
  --output configs/aim/render_layers.yaml
```

Build the registry with a non-default tech file or layer-map class:

```sh
conda run -n gds-blender-pipeline python scripts/aim_build_layer_registry.py \
  --tech external_pdks/AIMPhotonics_ACT1/tech.py \
  --raw-custom configs/aim/raw_custom_layers.yaml \
  --render-layers configs/aim/render_layers.yaml \
  --doping-rules configs/aim/doping_rules.yaml \
  --output configs/aim/layer_registry.local.yaml \
  --class-name LayerMapAIM
```

Regenerate the BlenderGDS stack config:

```sh
conda run -n gds-blender-pipeline python scripts/aim_generate_blendergds_config.py \
  --render-layers configs/aim/render_layers.yaml \
  --output configs/blender/aim.yaml
```

## Color Schemes

Blender materials are controlled by YAML files in
`configs/blender/colors/aim/`. The example Blender target renders every YAML
file in this directory by default.

```text
configs/blender/colors/aim/*.yaml
```

Available AIM color schemes:

- `configs/blender/colors/aim/realistic.yaml`: muted, physically plausible materials.
- `configs/blender/colors/aim/fancy.yaml`: high color and light contrast, grouped by material role.
- `configs/blender/colors/aim/marketing.yaml`: shiny, more monotone graphite/champagne/platinum style.

Render only a subset of schemes with:

```sh
AIM_BLENDER_COLORS=configs/blender/colors/aim/fancy.yaml make aim-blender-scene-example
```

The color YAML keys must match render-layer names in `configs/blender/aim.yaml`.
After changing render-layer names or adding layers, regenerate the stack config
and update the color YAML.

Quick validation:

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

## Git Hygiene

The repository ignores foundry/private inputs, generated layouts, generated
Blender scenes, and common cache files. Before pushing, it is worth checking:

```sh
git status --short
git status --ignored --short
git ls-files --others --exclude-standard
```

If you are unsure why a file is ignored:

```sh
git check-ignore -v path/to/file
```

Do not commit PDK tech files, private AIM layer maps, raw foundry GDS, generated
visual GDS, or `.blend` outputs unless you have deliberately sanitized them and
changed the ignore policy for that artifact.

## Troubleshooting

- `FileNotFoundError: external_pdks/.../tech.py`: place the private AIM tech
  file at `AIM_TECH`, or override `AIM_TECH=/path/to/tech.py`.
- `Could not find class LayerMapAIM`: pass the correct class with
  `--class-name`, or update the PDK tech file path.
- `BlenderGDS operator ... is not available`: install and enable BlenderGDS in
  the Blender executable you are running.
- Missing render layer during preprocessing: check that layer names referenced
  by expressions in `doping_rules.yaml` and `render_layers.static.yaml` exist in
  the PDK tech file or `raw_custom_layers.yaml`.
- Empty outputs for expected layers: inspect whether the raw GDS actually
  contains those source layers and whether derived-region offsets are too
  aggressive for the geometry.
