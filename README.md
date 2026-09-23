# GDS Studio · GDS Blender Pipeline


## Private setup dependency bundle

The versioned `gds-studio-aim-inputs` package contains the required private setup inputs.
Its exact file list and setup steps are declared in [.facultyos/setup-bundles/gds-studio-aim-inputs.json](.facultyos/setup-bundles/gds-studio-aim-inputs.json).
Obtain the ZIP and registration receipt from the authorized OneDrive package store,
verify its SHA-256, extract `restore.py`, and run:

```sh
python3 restore.py restore --archive gds-studio-aim-inputs-2026.09.23.1.zip --sha256 <registered-sha256> --destination /path/to/workspace --apply
```

FacultyOS Sync restores the same package automatically on managed machines.
Standalone restoration needs only Python; continue with the normal setup below.
Existing different files are preserved. Only authorized collaborators with the required
vendor access may receive these private inputs. Usage history and outputs are excluded.

A local web app for turning GDS layouts into Blender scenes and reproducible renders.
Import a raw GDS, reuse or generate its visual GDS, and choose your camera angle in
an interactive 3D preview **before** building a scene.
The app runs independently from FacultyOS and uses deterministic scripts throughout.
No LLM, API key, or hosted service is required.

## Start the app

Install Python 3.11+, Node.js with npm, and Blender with the
[GDSII Importer](https://extensions.blender.org/add-ons/import-gdsii/) enabled.
Blender is only needed for build/render, not for the interactive preview.

```sh
git clone https://github.com/wyywyy23/gds_blender_pipeline.git
cd gds_blender_pipeline
python3 scripts/setup_webapp.py
python3 scripts/start_webapp.py
```

Open **Local setup** and connect your licensed AIM `tech.py`, custom raw-layer YAML,
doping-rules YAML and static render-stack YAML. Use **Choose file** or **Raw GDS path →
Import path** for a layout anywhere on this computer. Both external and repository
inputs are copied into a named local raw archive; the original is unchanged. Existing
paths show **Ready**; missing inputs are listed individually. Everything stays local.

See the [setup and user guide](docs/web-app.md) for configuration formats, existing
Conda environments, Blender setup, alternate ports and troubleshooting.

## Compose → build → render

1. Select a saved layout, or import a raw `.gds` from any local path.
2. **Check visual GDS** reports whether preprocessing is needed. Raw content, private
   configuration, preprocessing parameters/code and Python dependency versions are
   checked by content, together with the preprocessing command plan. Render settings
   and unrelated app/code changes reuse the existing visual. Select **Force
   regenerate visual GDS** to create a fresh revision regardless of the check.
3. **Load visual preview** displays that visual GDS without building a Blender scene.
   Orbit, pan, dolly, or enter position, angles and focal length.
4. Select layers and grouped options, then click **Build scene** or **Build scene &
   render**. The app automatically names and saves a preset from your current camera,
   layer visibility, render and scene settings. Its name starts with the current GDS
   name; no filename or separate save is needed.
5. Each build uses the exact visual revision you previewed and the saved preset.
   Identical settings and visual reuse an existing preset; changes are preserved
   separately. Progress, logs, cancellation and downloads remain available. Changed
   preprocessing inputs require a refreshed preview first.
6. **Layout library** shows the raw original, numbered visual revisions, preset
   versions, and linked preview/build/render history, including after a restart.

New scenes and **Defaults** use **3200 × 2000 pixels** and **1024 Cycles samples**,
with adaptive sampling and denoising enabled. Loaded presets keep their saved
resolution and sample count. Higher settings take longer to render; lower them for
quick drafts.

Every layout has one home:

```text
.local/webapp/layouts/chip--<raw-hash>/
  raw/chip_raw.gds
  visual/v0001/chip_visual.gds
  visual/v0001/manifest.json
  presets/chip_realistic_80mm_3200x2000_<settings-hash>/p0001.yaml
  runs/run_0001_chip_realistic_80mm_3200x2000_<settings-hash>_build_render/
    preset.yaml                 # exact camera snapshot for this run
    request.json                # raw, visual and source-preset references
    build/ready.blend
    renders/*.png
```

Same name with different raw content produces a separate layout revision. Forced
preprocessing produces `v0002`, `v0003`, etc.; earlier presets and outputs stay intact.

The preview shows simplified layer extrusions. Blender evaluates the final optical
materials and surface detail. DOF on/off, focus point and aperture are saved and
applied in Blender; browser depth-of-field blur remains a clearly marked TODO.

The current PDK workflow is AIM Photonics. The optional FacultyOS Project start
button launches this same app on demand; it never starts with FacultyOS.

## Example renders

<table>
  <tr>
    <td><img src="docs/assets/readme/tx-array-coaxial-showcase-01.png" alt="Coaxial device render"></td>
    <td><img src="docs/assets/readme/tx-array-coaxial-showcase-02.png" alt="Coaxial device render"></td>
    <td><img src="docs/assets/readme/tx-array-coaxial-showcase-03.png" alt="Coaxial device render"></td>
  </tr>
  <tr>
    <td><img src="docs/assets/readme/tx-array-coaxial-showcase-04.png" alt="Coaxial device render"></td>
    <td><img src="docs/assets/readme/tx-array-coaxial-showcase-05.png" alt="Coaxial device render"></td>
    <td><img src="docs/assets/readme/tx-array-coaxial-showcase-06.png" alt="Coaxial device render"></td>
  </tr>
  <tr>
    <td><img src="docs/assets/readme/tx-array-coaxial-showcase-07.png" alt="Coaxial device render"></td>
    <td><img src="docs/assets/readme/tx-array-coaxial-showcase-08.png" alt="Coaxial device render"></td>
    <td><img src="docs/assets/readme/tx-array-coaxial-showcase-09.png" alt="Coaxial device render"></td>
  </tr>
</table>

## Advanced command-line workflow

The existing Make and Python entry points remain available for automation, batch
renders, geometry diagnostics and HPC use. See [Advanced usage](docs/advanced-usage.md).
App results live under the named layout directory shown in **Layout library**. Earlier
app jobs remain in `.local/webapp/jobs/` and appear as legacy history when their raw
checksum matches the selected layout. CLI outputs retain `.local/runs/<design>/`.
