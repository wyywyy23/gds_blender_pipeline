# GDS Blender Pipeline

Convert GDS layouts into editable Blender scenes and reproducible PNG renders.

**Current PDK workflow:** AIM Photonics. GDS import and extrusion use the
external [GDSII Importer](https://extensions.blender.org/add-ons/import-gdsii/),
which accepts configurable PDK layer stacks.

Output is visualization geometry, not a fabrication GDS.

## Dependencies

- Conda or Mamba
- `make`
- Blender
- [GDSII Importer](https://extensions.blender.org/add-ons/import-gdsii/),
  installed and enabled in Blender

```sh
make env
make env-info
```

If Blender is not on `PATH`, add `BLENDER=/path/to/blender` to Make commands.

## Required Files

| File | Description |
| --- | --- |
| `external_pdks/AIMPhotonics_ACT1/tech.py` | Private AIM technology file containing `LayerMapAIM` |
| `configs/aim/raw_custom_layers.yaml` | Custom raw-layer markers |
| `configs/aim/doping_rules.yaml` | Silicon and doping rules |
| `configs/aim/render_layers.static.yaml` | Static render layers and z stack |
| `path/to/layout.gds` | Layout to render |

The registry, BlenderGDS stack, visualization GDS, Blender scene, and renders
are generated locally and ignored by Git. See
[Private AIM Configuration](docs/advanced-usage.md#private-aim-configuration)
for the YAML formats.

## First Render

Run all commands from the repository root.

### 1. Build The Scene

```sh
make aim-build GDS=path/to/my_cell.gds
```

Scene:

```text
.local/runs/my_cell/blender/my_cell.realistic.blend
```

The default color scheme is `realistic`; standard geometry settings are
applied automatically.

### 2. Choose A Camera View

```sh
blender .local/runs/my_cell/blender/my_cell.realistic.blend
```

- Compose the desired view in the 3D Viewport.
- Select `Camera`, then choose **View > Align View > Align Active Camera to
  View** (`Ctrl` + `Alt` + `Numpad 0`).
- Record its **Location**, **Rotation**, and **Focal Length**.

### 3. Create A Preset

Create `configs/blender/render_presets/my_view.yaml` and replace the camera
values:

```yaml
version: 1
name: my_view

camera:
  object: Camera
  type: PERSP
  location: [100.0, -200.0, 300.0]
  rotation_degrees: [35.0, 0.0, -25.0]
  lens_mm: 100.0

render:
  engine: CYCLES
  samples: 64
  denoise: true
  file_format: PNG

output:
  directory: .local/runs/my_cell/renders

runs:
  - name: all_layers
    hide_layers: []
```

Camera rotations are in degrees. Increase `samples` after the first test
render if needed.

### 4. Render The PNG

```sh
make aim-render \
  GDS=path/to/my_cell.gds \
  PRESET=configs/blender/render_presets/my_view.yaml
```

Output:

```text
.local/runs/my_cell/renders/my_cell.realistic.my_view.all_layers.png
```

## Color Schemes

| Scheme | Style |
| --- | --- |
| `realistic` | Default, physically plausible materials |
| `fancy` | Higher color and light contrast |
| `marketing` | Graphite, champagne, and platinum styling |

```sh
make aim-build GDS=path/to/my_cell.gds SCHEME=fancy
```

Pass the same `SCHEME` to `aim-render` to render that scene.

## Output Layout

```text
.local/runs/<layout>/
├── visual/              # internal visualization GDS
├── blender/             # editable scenes
└── renders/             # PNG renders
```

## Advanced Usage

See [Advanced Usage](docs/advanced-usage.md) for direct commands, render runs,
geometry controls, farm/HPC export, Make variables, cleanup, and troubleshooting.

## Common Errors

| Error | Fix |
| --- | --- |
| Missing `tech.py` | Supply the private PDK file or set `AIM_TECH` |
| BlenderGDS operator unavailable | Install and enable GDSII Importer in the selected Blender installation |
| Missing preprocessing layer | Check the tech file, `raw_custom_layers.yaml`, and render expressions |
| Missing preset layer | Use layer names present in the generated scene |
