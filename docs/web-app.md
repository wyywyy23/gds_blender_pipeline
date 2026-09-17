# Local web app setup

GDS Studio runs from this repository on your computer. It does not require FacultyOS,
Codex, an API key, an LLM, or a hosted backend. Runtime assets are served locally;
internet access is needed only when installing dependencies. The current geometry
pipeline supports AIM Photonics and one top-level GDS cell per input.

## Install and start

Install Python 3.11 or newer, Node.js with npm, and Blender with the
[GDSII Importer](https://extensions.blender.org/add-ons/import-gdsii/) enabled.
Blender is required only for scene building and final rendering.

```sh
git clone https://github.com/wyywyy23/gds_blender_pipeline.git
cd gds_blender_pipeline
python3 scripts/setup_webapp.py
python3 scripts/start_webapp.py
```

The installer creates `.venv` in this clone and installs the locked Three.js package
under `webapp/node_modules`. It does not install a PDK or Blender extension.
The launcher opens `http://127.0.0.1:8768`. Ctrl+C stops it. To leave the service
running, use `--background`; use `--no-browser` for a terminal-only launch. For another
clone or an occupied port, add `--port 8770`. macOS and Linux are supported;
Windows users should run the pipeline through a Linux environment (WSL).

An existing environment can be used with
`python3 scripts/setup_webapp.py --python /path/to/environment/bin/python`.
Run `scripts/start_webapp.py` with that interpreter, or select it in Local setup.
The existing `make env` Conda environment remains supported.

## Connect private files

Open **Local setup**, provide the paths below, then **Save & check paths**.
Existing conventional repository paths are detected automatically. A file shows
**Ready** only when it exists; missing items show **Missing**. Paths containing spaces
are supported. The private technology Python file is parsed as syntax by the existing
registry script, never imported or executed.

| Setting | Required local file |
|---|---|
| GDS layout | Raw layout with a single top-level cell |
| AIM tech.py | Licensed PDK technology file containing `LayerMapAIM` |
| Custom raw layers | `raw_custom_layers.yaml` |
| Doping rules | `doping_rules.yaml` |
| Static render stack | `render_layers.static.yaml` |
| Python | Interpreter with the installed geometry dependencies |
| Blender | Blender executable with GDSII Importer enabled |

Obtain licensed PDK files through your authorized source. The three YAML inputs
use the [existing configuration formats](advanced-usage.md#configuration-syntax).
Configuration is stored in ignored `.local/webapp/config.json`. You can also copy
`webapp/config.example.json` there and edit the paths. The app checks file existence
and geometry dependencies; the build script checks the Blender importer when a
build starts, and reports any failure in the job log.

## Import and select raw GDS

Use **Choose file** for the operating system file chooser, or paste an absolute or
repository-relative path into **Raw GDS path** and click **Import path**. Paths outside
the clone, spaces and uppercase `.GDS` extensions are supported. The file chooser
sends the selected bytes only to this local loopback server (up to 1 GiB); use a path
for larger files. The app validates the GDSII header before copying, verifies the
complete SHA-256 after copying and checks that the source did not change mid-copy.
The geometry parser performs full parsing when the visual is generated.

Both repository-local and external inputs enter the **same** archive/checking flow:

- A sanitized name ending in `_raw.gds` is stored under
  `.local/webapp/layouts/<name>--<first-12-raw-SHA256>/raw/`.
- The original is left untouched. A local snapshot lets the app continue working if
  an external drive or original path is later unavailable.
- Importing the same name and bytes reuses the archive. Different content creates a
  distinct raw revision, even when filenames match. Selecting the same archived raw
  again also reuses its layout identity.
- **Saved layouts** switches between archives. If you edit the external original,
  import it again to create/select its new content revision; the app does not silently
  replace its archived original. Do not edit files inside the archive in place.
- `layout.json` retains original names/paths, import time, byte size and checksum.
  Paths remain in this ignored local library.

Existing GDS paths from the earlier app are adopted by this same flow when you check
or load their visual. Existing CLI files and old job folders are preserved.

## Check or regenerate visual GDS

**Check visual GDS** compares raw and configuration content, the preprocessing options
(XY fillets, undercut, passivation and fracture limit), the five preprocessing scripts,
the command-building module, and versions of the configured Python environment's
geometry packages. A valid matching visual revision shows **Ready** and is reused.
Changing rendering samples, camera, color scheme or preview simplification does not
regenerate visual GDS. Preview simplification and Z scale affect the interactive mesh.

Output checksums also matter: missing, partial or modified visual/stack output is not
reused. Generation publishes a numbered `v0001`, `v0002`, … revision only after all
commands complete and inputs are checked again. A failed or cancelled revision is
retained as incomplete and never treated as Ready.

**Force regenerate visual GDS** always creates a new visual revision, including when
the normal checks pass. Use it after a preprocessing/environment change that is not
covered by the manifest. The checkbox is cleared after the new preview loads. The
preview and Blender build both consume the exact same generated visual GDS, including
its selected XY rounding. Only browser mesh tessellation is simplified separately.

## Compose before building

New sessions and **Defaults** start at **3200 × 2000 pixels** with **1024 Cycles
samples**. Adaptive sampling and denoising remain enabled; with adaptive sampling,
1024 is the maximum sample count and converged pixels can stop earlier. Change
resolution under **Render** and samples under **Performance** for faster drafts.
Loading an existing preset restores its saved values instead of replacing them with
these defaults.

1. Click **Load visual preview**. A valid visual is reused, otherwise preprocessing
   generates a new one. The browser triangulates that visual GDS without starting
   Blender. The progress message reports which visual revision was generated/reused.
2. Drag to orbit, right-drag to pan, and scroll to move the camera. Use **Fit visible**,
   **Top view**, or **Oblique** for a starting point. Position, rotation, focal length
   and sensor width remain editable.
3. The camera frame uses the selected output width/height, square pixels, horizontal
   sensor fit, zero lens shift and perspective projection. Coordinates remain in µm
   with Z up. Blender XYZ Euler angles map to Three.js ZYX intrinsic rotations.
4. Toggle layers and grouped Appearance, Geometry, Render and Performance options.
   Geometry changes require a refreshed preview before building; camera and final
   render options do not. Missing/changed preprocessing inputs are rejected at build
   time rather than silently rebuilding different geometry behind the chosen camera.
5. Enable Blender DOF, enter or pick a focus position, and choose the f-stop.
   **TODO: browser DOF blur.** Blender applies the saved focus/aperture settings.
6. Click **Build scene** or **Build scene & render**. Before starting Blender, the app
   automatically names and saves an immutable preset from the current camera/DOF,
   layer visibility, render settings, production scene options, and any loaded
   lighting/color-management settings. There is no filename field or separate save.
   A name such as `auto_realistic_80mm_3200x2000_<settings-hash>` summarizes the color
   scheme, focal length and image size; the deterministic digest distinguishes all
   saved settings. Preview mesh tolerance/budget, local input paths, the action button,
   and force-regeneration flags do not participate in naming.
7. Identical settings linked to the same visual reuse the existing preset. Changed
   settings get another automatic name; using the same settings with a new visual
   creates another immutable `p0002.yaml` version under that name. Shared CLI/earlier
   app presets remain loadable and are never overwritten. The preset selector and
   Layout library expose the automatically saved versions immediately, even if a
   later Blender step fails.
8. **Build scene** creates a base scene and a camera-ready scene. **Build scene &
   render** additionally renders PNG. Both consume the exact visual revision you
   previewed; neither preprocesses again. Each run's `preset.yaml` is a byte-identical
   copy of its saved library preset. `request.json` and the job record identify that
   exact preset path and hash separately from the preset originally loaded for editing.

The preview approximates shapes and composition. It omits optical materials,
shader bevels and 3D cladding cutter booleans; mesh simplification may reduce XY detail. Transparent cladding
can be hidden to inspect internal layers. Geometric simplification is controlled
separately from final output. A triangle-budget error stops the preview with an
explanation; the app never silently discards polygons to fit a budget.

## Layout library and output

Open **Layout library** to find the selected layout's raw file, every valid visual
revision, saved preset versions and matching run history. Each run identifies its
visual revision, camera name, time, status and exact local directory, with links to
its preset snapshot, build outputs, PNGs and request/command records. History is read
back from disk after restart. A run interrupted by a server restart remains visible.
Earlier flat job folders appear as legacy history when their raw checksum matches;
these historical files are neither moved nor rewritten.

```text
.local/webapp/
  config.json
  server.log
  layouts/<name>--<raw-sha12>/
    layout.json
    raw/<name>_raw.gds
    visual/v0001/
      <name>_visual.gds
      stack.yaml, registry.yaml, layers.yaml, doping.yaml
      manifest.json      # raw/config/code/environment fingerprint + output hashes
      commands.json
    presets/<automatic-settings-name>/p0001.yaml
    runs/run_0001_<automatic-settings-name>_build_render/
      request.json       # raw/visual identities, options, source and saved preset refs
      preset.yaml        # exact submitted camera/options for this run
      commands.json
      job.log
      build/scene.blend
      build/ready.blend
      renders/*.png
      result.json
    runs/run_0002_preview_preview/
      preview.json
      request.json, commands.json, job.log, result.json
```

Raw identities, visual versions, preset versions and run numbers have distinct jobs:
changing raw content makes another layout directory; changing preprocessing creates
another visual revision; building automatically saves or reuses the corresponding
preset; each build/render creates another numbered run. Old results are never overwritten.
`webapp.lineage` inside a preset and each run's `request.json` record the corresponding
raw SHA-256, visual version/path/SHA-256 and preprocessing fingerprint. The app does
not require a separate database to recover these relationships.

Only one job runs at a time. **Cancel job** terminates its subprocess group. Completed
visual generations remain reusable even if later triangulation or rendering fails.
The server binds only to loopback, checks request origins and tokens, executes argument
arrays without a shell, and exposes only UI assets and explicitly linked artifacts.
Private technology/configuration source files are not served. The library is ignored
by Git; copy a desired complete layout folder deliberately if you need to archive or
transfer its private data elsewhere.

## FacultyOS entry

The optional `.facultyos/workspace-entries.json` declares a **GDS Studio / Start app**
button under the owning Project. It invokes the same local launcher with
`--background` only after a click. FacultyOS startup and resource discovery do not
start GDS Studio. Removing FacultyOS has no effect on this app.

## Troubleshooting

- **Missing Python modules or viewer:** rerun setup. For Conda, select its Python in
  Local setup. Restart the studio if its own Python cannot import YAML.
- **Blender importer unavailable:** install and enable GDSII Importer in the selected
  Blender version; inspect the build log for the exact error.
- **Preview too large:** increase preview simplification, raise the triangle budget,
  or use a smaller raw GDS crop. Final render geometry is unaffected by simplification.
- **Visual needs regeneration:** check the reported changed inputs, then reload the visual preview. Use Force regenerate when desired.
- **Wrong framing:** reload the preset or Fit visible; confirm Z scale, image aspect and
  sensor width. DOF blur and refraction are intentionally not predicted by the preview.
- **Port occupied:** specify another port, or open the already running instance of
  this clone. The launcher refuses to reuse an unrelated app's port.

## Raw GDS backup with FacultyOS (optional)

Raw layouts are private originals: keep them excluded from Git and use the configured
FacultyOS OneDrive external-file store for cross-device transport. The standalone
app runs without FacultyOS or OneDrive. A local app import is not by itself a cloud
backup.

`.facultyos/external-files.json` declares original-source locations for FacultyOS
discovery: the existing AIM raw directory, the original SKY130 example, and imported
`_raw.gds` files in the layout library. Discovery matches `.gds` case-insensitively.
Full-sync audits, OneDrive sync, and bootstrap report newly found originals that
have no external-file registration; they do not silently call those files synced.
Register these candidates through the guarded `project-assets` workflow under the
existing raw-layout storage authorization, then repeat OneDrive transport. Identical
raw bytes share one SHA-256 store object even when multiple archive paths are registered.

Visual GDS, build files, rendered images and reproducible caches are not included by
these rules. Keep original raw bytes immutable; a changed source is a new version,
not permission to overwrite a previously registered copy. A source-policy edit does
not itself authorize uploading arbitrary files.
