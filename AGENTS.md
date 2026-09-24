# GDS Studio implementation rules

- Resolve dependent build settings deterministically from the checked visual GDS.
  A user-facing option must include the preparation and arguments it requires;
  do not make users coordinate hidden CLI flags or repeat a setting elsewhere.
- For metal cap Z bevel, generate and pass complete-layer sidecars for every
  non-empty eligible metal render layer. When none exist, skip the inapplicable
  treatment while preserving the user's saved preference. Keep the real fracture
  limit; never label fractured geometry as unfractured to bypass validation.
- Test generated build commands against the Blender script's actual argument
  parser, including absent/partial layers, disabled effects and zero widths.
  Checking that an individual sidecar flag is absent or present is insufficient.
- Preserve the exact previewed visual and immutable raw/preset/run lineage.
- Automatic preset and run names must start with the current archived GDS name.
  Preserve that layout identity when loading another preset as a starting point,
  retain supported layout names in full, and keep existing preset/history paths
  immutable when changing naming rules.
- Visual cache identity must cover actual preprocessing inputs, scripts, command
  arguments and environment; do not hash unrelated render, camera, preset or UI
  implementation as preprocessing code. Preserve reuse across cache-format upgrades
  only when legacy inputs, output hashes and recorded preprocessing commands prove
  compatibility. Never rewrite old manifests or bypass genuine geometry changes.
- Normalize imported preset layer aliases with the same rules as the Blender
  preset loader before using them as checkbox/preview state. The current layer
  controls must determine newly saved visibility; old aliases must not survive a
  user enabling a layer. Verify loading before/after preview and toggling through
  the actual browser handlers and Blender preset loader.
- Link output dimensions by default using the current or loaded preset aspect
  ratio, including portrait images. Keep integer pixel bounds, provide an explicit
  unlock, and keep preview framing and saved/rendered dimensions in agreement.
- Validate cladding appearance with matched cladding-on/off renders and a layout
  containing real undercut openings. Suppressing ghost images is insufficient:
  covered regions must remain distinguishable from holes, with glass character
  and readable devices beneath. Check Ramzi for duplicated outlines and TX
  checkered for openings, with the original glass openings as the appearance
  reference. Keep default cladding neutral. For the optional thin-film effect,
  cyan and stronger colored reflections are acceptable while device lines, hole
  boundaries and refractive walls remain readable. Evaluate color separately
  from structural clarity; reject obscuring haze and lost opening definition.
  Optional iridescence should preserve baseline brightness and contrast: compare
  at fixed exposure and separate material changes from added illumination. Label
  normalized reflection color as a presentation approximation, not absolute
  physical thin-film reflectance. Global brightness/contrast statistics do not
  establish visual acceptance: inspect hue variety, local device/metal/opening
  contrast and actual user feedback before recommending a look.
  Check actual shader paths before attributing glass character
  to IOR: the value must govern the relevant reflection/refraction branch.
  Shared cladding settings must work across GDS layouts; validation examples
  must not introduce filename-, device- or coordinate-specific material branches.

- Place Studio controls by the operation they require: visual GDS preprocessing
  controls in the upper-left source section, scene construction controls in the
  lower-left Build scene section, and camera/render/visibility controls on the
  right with the preset. Keep preview-only detail settings beside the viewport.
  Only visual changes may require visual regeneration before building. Scene and
  preset changes must leave Build available for the checked visual; update simple
  preview transforms immediately where possible. Defaults apply only to their
  own section. New options must declare and verify their stage against the actual
  preprocessing/build/preset path, not only their visual label.

- Preserve the visual preprocessing process order: compute fill/cheese from the
  original layout and its clearance masks, then subtract DIAM from FNAM/SNAM.
  Keep finishing before trench cutting to match the user-selected process
  sequence; performance optimizations must preserve this order.

- Validate depth of field with matched renders containing real depth variation,
  not only enabled flags or focus-coordinate handoff. Check optical scale and
  pixel blur at the intended output size. Keep illustrative aperture adaptation
  explicit in the preset, preserve framing/focus and render quality, and verify
  scale invariance plus saved-scene and rendering behavior.

- For this project, a user request to push includes both the reviewed Git changes
  and updates to the configured OneDrive external-file store. Discover and preserve
  new required originals and camera/render presets, including validation presets,
  then verify both Git publication and OneDrive copies before reporting completion.
  Keep generated scenes, renders and reproducible caches outside original-file
  registration; retain their source inputs and settings.
