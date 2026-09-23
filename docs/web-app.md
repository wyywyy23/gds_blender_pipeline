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
the actual preprocessing command plan, and versions of the configured Python
environment's geometry packages. A valid matching visual revision shows **Ready** and is reused.
Changing rendering samples, camera, color scheme or preview simplification does not
regenerate visual GDS. Changes to render defaults, Blender scene construction, preset
naming or other unrelated app code also do not invalidate it; the complete shared
`pipeline.py` module is not used as a preprocessing fingerprint. Preview simplification and Z scale affect the interactive mesh.

Earlier manifests that hashed the whole pipeline remain reusable when raw/config
content, preprocessing scripts/options and environment match, and their recorded
commands exactly match the current preprocessing plan after accounting for local
paths. Missing or incompatible command records require regeneration. This check
never rewrites old manifests or breaks their preset/run references.

Output checksums also matter: missing, partial or modified visual/stack output is not
reused. Generation publishes a numbered `v0001`, `v0002`, … revision only after all
commands complete and inputs are checked again. A failed or cancelled revision is
retained as incomplete and never treated as Ready.

**Force regenerate visual GDS** always creates a new visual revision, including when
the normal checks pass. Use it after a preprocessing/environment change that is not
covered by the manifest. The checkbox is cleared after the new preview loads. The
preview and Blender build both consume the exact same generated visual GDS, including
its selected XY rounding. Only browser mesh tessellation is simplified separately.

XY rounding is adaptive in both Studio and the command-line pipeline. The metal
radius remains **0.20 µm** and the contact/via radius **0.10 µm** by default; these
values are upper limits. Each connected shape first uses the selected radius.
If rounding would erase or split that shape, change its holes, or join neighbours,
only affected shapes retry with smaller radii. Shapes too small for safe rounding
at GDS grid resolution retain their original contours. Zero still disables rounding.
The preprocessing log reports the effective radius range and affected shape count.

The visual extent also follows dicing trenches automatically. When merged **DIAM**
forms closed trenches enclosing all raw layout geometry, the substrate footprint
ends at their **outer contours**, with no extra bbox margin. Cladding and etchable
substrate still have DIAM removed, and generated layers stay inside those outer
contours. Nonrectangular contours and multiple enclosed chips are retained. If DIAM
is absent, open, or only surrounds part of the layout, the full layout bbox is
expanded by the configured margin (10 µm per edge by default). CLI `--bbox-margin`
overrides only this fallback. Studio and CLI use the same rule; existing visuals
are regenerated into a new revision when the preprocessing code changes.

## Controls by workflow stage

The upper-left **Visual GDS** section contains fill cheese, TUAM undercut, PAAM
opening, both adaptive XY radii and the advanced GDS fracture limit. Changing any
of these requires **Refresh visual GDS & preview** before building.

The lower-left **Build scene** section contains the color scheme, metal cap bevel,
cladding geometry, vertical scale and object merging. Change these and build again
using the checked visual. Vertical scale updates the browser model immediately.

The right side holds the preset, camera, surface optics, output dimensions, render
quality and layer visibility. Builds save these together in the render preset.
Each Defaults button resets only its own section. Preview detail lives next to the
viewport; **Refresh preview** applies it without blocking a full-detail scene build.

## Optional fill and cheese

Enable **Visual GDS → Geometry → Fill cheese**, then refresh the visual preview. It defaults to
off. When enabled, the shared preprocessor adds ONE Lab dummy fill and metal cheese
holes to the original raw geometry before silicon/doping conversion and adaptive
XY rounding. The original raw GDS stays unchanged. Both the browser and Blender
consume the resulting visual revision; browser simplification never changes its
full-detail fill or holes. Loading an older preset without this option turns it off.

The editable profile is `configs/finishing/aim_fill_cheese.json`, adapted from the
ONE Lab layout implementation. It retains aligned horizontal dot/capsule fill on
SEAM/FNAM/SNAM, aligned M1/M2 squares, ML squares, independent BFILL/BCHE masks,
original-waveguide keepout, round clearances, via and metal-edge protection, TUAM
clearance, global device keepouts on 802/803/804 for both fill and cheese (sharing the
7 µm waveguide clearance), and
per-layer DIAM minima. Fill and holes must fit whole inside the original
layout bbox and allowed regions; the existing DIAM/expanded-bbox rule still determines
the visual substrate extent. TUAM is preserved and fill is computed from the unfilled
source each time. No undercut repair is performed against new fill.

This is the same photo-informed visualization model as ONE Lab, with its approximate
width trigger and candidate limit; it is not a foundry density/signoff deck. A budget
error is explicit and does not silently lower full-detail geometry. Profile/rule or
engine changes invalidate enabled visuals; toggling the option can reuse an existing
matching revision. Camera and preview-quality changes remain independent.

CLI: add `--fill-cheese` to `scripts/aim_preprocess_gds.py`; optionally provide
`--fill-cheese-config profile.json`. With Make, use `make aim-build GDS=... FILL_CHEESE=1`
or `make aim-preprocess ... AIM_PREPROCESS_FILL_CHEESE=1`. The staged Make workflow
uses a `.fill-cheese` filename suffix to distinguish the output.

The standalone adapter uses the pipeline's existing dependencies, with no onelab
checkout/runtime dependency. Reference: onelab-layout working snapshot over
`28970731221029ff4048616951db01237957f1e5`, read on 2026-09-23:

- `src/onelab/finishing.py`: `61da5be66d4a6e8dd086d600ca6c171075290b2e8faaf02b978a4cafce85dbaf`
- `src/onelab/_geometry.py`: `80c4ce7f9476a91ed428dd85df6fd3dcfc6e6878c7c4e12e0ba11f337bbfd199`
- `config/aim_fill_cheese.json`: `5426a83b6d5ef7db7e924119a59e19b320a69a1ffc72988ce56a831549e97972`
- `config/aim_diam_rules.json`: `ab3f0daa5e8ad88757f74b9af0274cdd3d7d049706a716de876d84c13e52a04b`

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
   **Metal cap Z bevel** handles its dependencies automatically: for non-empty
   M1/M2/ML render layers the app generates complete-layer sidecars from the checked
   visual GDS, even when that GDS is fractured. If those metal layers are absent,
   the build skips metal bevel while retaining the saved preference. No separate
   change to the fracture limit or sidecar setting is needed.
5. Enable Blender DOF, enter or pick a focus position, and choose the f-stop.
   **TODO: browser DOF blur.** Blender applies the saved focus/aperture settings.
6. Click **Build scene** or **Build scene & render**. Before starting Blender, the app
   automatically names and saves an immutable preset from the current camera/DOF,
   layer visibility, render settings, production scene options, and any loaded
   lighting/color-management settings. There is no filename field or separate save.
   A name such as `chip_realistic_80mm_3200x2000_<settings-hash>` starts with the current
   archived GDS name, followed by color scheme, focal length and image size; the
   deterministic digest distinguishes all saved settings. The current GDS determines the prefix even when starting from
   another layout's preset. Full archived layout names are retained; matching run
   names use the same preset name. Older `auto_...` and manual presets remain
   loadable under their original names. Preview mesh tolerance/budget, local input
   paths, the action button and force-regeneration flags do not participate in naming.
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

The preview automatically adjusts its detail to fit the **Preview triangle budget**
(default 1,000,000, adjustable up to 2,000,000). **Preview simplification start**
(default 0.05 µm) is the starting tolerance. When needed, Studio increases it while
preserving connected shapes and holes. If tiny disconnected structures still exceed
the budget, the smallest footprints keep their outlines as flat top surfaces in the
preview. Every component and layer remains represented; an impossibly small budget
still produces an explicit error rather than dropping shapes.

The note beneath the viewer reports the actual tolerance and any flat small shapes.
These adjustments happen automatically for future layouts and are used only to make
the browser preview responsive. Original layout bounds stay fixed for camera framing.
They do not rewrite the visual GDS, lower final render resolution/samples, or change
Blender's full-detail geometry, materials or bevels. Preview-only changes reuse the
same checked visual and require no new preprocessing. The preview omits optical
materials, shader bevels, depth-of-field blur and 3D cladding cutter booleans; inspect
those effects in Blender. Transparent cladding can be hidden to inspect internal layers.

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
    presets/<gds-name>_<settings-name>/p0001.yaml
    runs/run_0001_<gds-name>_<settings-name>_build_render/
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

### Imported preset layer visibility

The app loads the first visibility run from a preset. For example,
`ramzi_oblique_100mm.yaml` starts with `no_cladding`; loading it leaves the CLADDING
layer unchecked. Legacy names such as `cladding` and Blender object names such as
`LCLADDING_RENDER` map to the same `CLADDING_RENDER` checkbox. Checking that layer
shows it in preview and removes it from the newly saved preset's hidden layers;
unchecking hides it in both preview and Blender. Existing preset files and completed
renders are preserved. Build again to apply a changed selection.

### Output image dimensions

**Lock image aspect ratio** is on by default under Render. Changing either width or
height automatically scales the other side to the nearest whole pixel; for example,
3200 × 2000 becomes 4800 × 3000 when width is set to 4800. Portrait images work the
same way. Loading a preset uses its saved width-to-height ratio. Unlock the control
to enter width and height independently, then lock it again to retain the new ratio.
Defaults restores 3200 × 2000 with the lock enabled. Both sides must remain within
the displayed size limits. The camera frame and next automatic preset use the
resulting dimensions; resizing does not regenerate visual GDS.

## Optional adaptive iridescence

Under **Appearance**, enable **Adaptive iridescence (final render)** for broad,
slightly curved cladding color bands fitted to the current camera view. The default
is off. **Iridescence strength** ranges from 0 to 1; its starting value is **0.85**,
and 0 is equivalent to off. Both settings are saved automatically in presets.
Loading a preset without appearance settings disables the effect; an explicitly
saved strength is retained. The web mesh preview shows geometry; inspect the final
Blender render for this surface effect.

The selected treatment fits approximately **1.5 reference interference periods**
to the visible cladding top-plane extent, with a 28-degree thickness-gradient
orientation and mild 25 nm low-frequency thickness variation. Actual RGB color-band
counts vary with view angle and geometry. Camera framing and render dimensions are
applied before fitting the pattern. Changing the camera in a saved Blender scene
requires reapplying its preset to refit the pattern and reposition the white light.
Prepared `.blend` files contain the native shader and lighting and can render
without this repository's Python scripts.

The accepted lighting balance reduces existing Sun energies by 10%, adds a white
area light linked only to cladding at energy multiplier 0.4, and reduces existing
neutral studio reflection to one quarter (Realistic: 0.4 to 0.1). Clear transmission
and the original opening-wall glass remain intact. Disabling, setting strength to
zero, or hiding/omitting cladding restores the original Sun energies, neutral sheen
and cap shader, and removes only effect-owned nodes, light and receiver collection.
Repeated application does not accumulate lights or repeatedly dim the Sun. Loading
a new preset restores the old effect first, then applies the new preset's lighting.

CLI presets use the same configuration (verified in Blender 5.2.2 LTS):

```yaml
appearance:
  cladding_iridescence:
    enabled: true
    strength: 0.85
```

The film is a presentation hypothesis, not measured chip thickness or a full optical
multilayer simulation. Existing archived presets/scenes stay unchanged; newly
prepared scenes with the option enabled receive the selected adaptive treatment.
See [the comparison and acceptance notes](microscope-iridescence.md).
