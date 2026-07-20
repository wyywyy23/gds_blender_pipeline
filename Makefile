ENV_NAME := gds-blender-pipeline
BLENDER ?= blender

AIM_TECH ?= external_pdks/AIMPhotonics_ACT1/tech.py

AIM_RAW_CUSTOM ?= configs/aim/raw_custom_layers.yaml
AIM_DOPING_RULES ?= configs/aim/doping_rules.yaml

AIM_RENDER_STATIC ?= configs/aim/render_layers.static.yaml
AIM_RENDER_DOPING ?= configs/aim/render_layers.doping.generated.yaml
AIM_RENDER_LAYERS ?= configs/aim/render_layers.yaml

AIM_LAYER_REGISTRY ?= configs/aim/layer_registry.local.yaml
AIM_BLENDERGDS_CONFIG ?= configs/blender/aim.yaml
AIM_BLENDER_COLOR_DIR ?= configs/blender/colors/aim
AIM_BLENDER_COLORS ?= $(sort $(wildcard $(AIM_BLENDER_COLOR_DIR)/*.yaml))
AIM_BLENDER_DELETE_LAYERS ?=
AIM_BLENDER_Z_SCALE ?= 1.0
AIM_BLENDER_CAMERA_FIT_MARGIN ?= 1.10
AIM_BLENDER_CLADDING_MODE ?= boolean
AIM_BLENDER_CLADDING_BOOLEAN_SOLVER ?= manifold
AIM_BLENDER_CLADDING_UNDERCUT_METHOD ?= fractured_batches
AIM_BLENDER_CLADDING_UNFRACTURED_CUTTER ?=
AIM_BLENDER_CLADDING_EXPLICIT_MESH ?=
AIM_BLENDER_CLADDING_EXPLICIT_CHUNK_SIZE_UM ?= 0
AIM_BLENDER_APPLY_CLADDING_BOOLEAN ?= 1
AIM_PREPROCESS_MAX_POLYGON_VERTICES ?= 256
AIM_BLENDER_MERGE_LAYERS ?= 0

AIM_RENDER_BLEND ?= examples/aim/blender/trx_top.realistic.blend
AIM_RENDER_PRESET ?= configs/blender/render_presets/trx_top_oblique_100mm.yaml
AIM_RENDER_RUNS ?=
AIM_RENDER_OUTPUT_DIR ?=
AIM_RENDER_READY_RUN ?= all_layers
AIM_RENDER_READY_BLEND ?=
AIM_RENDER_READY_OUTPUT ?=
AIM_RENDER_READY_PACK ?= 1

EXAMPLE_DIR ?= examples/aim
EXAMPLE_RAW_DIR ?= $(EXAMPLE_DIR)/raw
EXAMPLE_VISUAL_DIR ?= $(EXAMPLE_DIR)/visual
EXAMPLE_BLENDER_DIR ?= $(EXAMPLE_DIR)/blender
EXAMPLE_RENDER_READY_DIR ?= $(EXAMPLE_DIR)/render_ready
EXAMPLE_GDS ?= $(EXAMPLE_RAW_DIR)/tx_array_checkered.gds
EXAMPLE_VISUAL_GDS ?= $(EXAMPLE_VISUAL_DIR)/tx_array_checkered.visual.gds
EXAMPLE_BLEND ?= $(EXAMPLE_BLENDER_DIR)/tx_array_checkered.blend

# Minimal raw-GDS-to-render workflow. Users only provide GDS and PRESET.
GDS ?=
PRESET ?=
AIM_RUN_ROOT ?= .local/runs
AIM_RUN_NAME = $(basename $(notdir $(GDS)))
AIM_RUN_DIR = $(AIM_RUN_ROOT)/$(AIM_RUN_NAME)
AIM_RUN_VISUAL_GDS = $(AIM_RUN_DIR)/visual/$(AIM_RUN_NAME).visual.gds
AIM_RUN_BASE_BLEND = $(AIM_RUN_DIR)/blender/$(AIM_RUN_NAME).blend
AIM_RUN_REALISTIC_BLEND = $(AIM_RUN_DIR)/blender/$(AIM_RUN_NAME).realistic.blend
AIM_RUN_RENDER_DIR = $(AIM_RUN_DIR)/renders

.PHONY: \
	env env-update env-remove env-info \
	aim-render-layers aim-registry aim-blendergds-config \
	aim-preprocess-example aim-preprocess-all-examples \
	aim-blender-scene-example aim-render-preset aim-prepare-render-blend \
	aim-run aim-clean-generated

aim-run:
	@test -n "$(strip $(GDS))" || { echo "Usage: make aim-run GDS=path/to/input.gds PRESET=path/to/preset.yaml"; exit 2; }
	@test -n "$(strip $(PRESET))" || { echo "Usage: make aim-run GDS=path/to/input.gds PRESET=path/to/preset.yaml"; exit 2; }
	@test -f "$(GDS)" || { echo "GDS file not found: $(GDS)"; exit 2; }
	@test -f "$(PRESET)" || { echo "Preset file not found: $(PRESET)"; exit 2; }
	@echo "Running AIM GDS -> realistic Boolean scene -> preset renders"
	+$(MAKE) aim-blender-scene-example \
		EXAMPLE_DIR="$(AIM_RUN_DIR)" \
		EXAMPLE_GDS="$(GDS)" \
		EXAMPLE_VISUAL_GDS="$(AIM_RUN_VISUAL_GDS)" \
		EXAMPLE_BLEND="$(AIM_RUN_BASE_BLEND)" \
		AIM_BLENDER_COLORS="configs/blender/colors/aim/realistic.yaml" \
		AIM_PREPROCESS_MAX_POLYGON_VERTICES=256 \
		AIM_BLENDER_Z_SCALE=1.0 \
		AIM_BLENDER_CAMERA_FIT_MARGIN=1.10 \
		AIM_BLENDER_CLADDING_MODE=boolean \
		AIM_BLENDER_CLADDING_BOOLEAN_SOLVER=manifold \
		AIM_BLENDER_CLADDING_UNDERCUT_METHOD=fractured_batches \
		AIM_BLENDER_CLADDING_UNFRACTURED_CUTTER= \
		AIM_BLENDER_CLADDING_EXPLICIT_MESH= \
		AIM_BLENDER_CLADDING_EXPLICIT_CHUNK_SIZE_UM=0 \
		AIM_BLENDER_APPLY_CLADDING_BOOLEAN=1 \
		AIM_BLENDER_DELETE_LAYERS= \
		AIM_BLENDER_MERGE_LAYERS=0
	+$(MAKE) aim-render-preset \
		AIM_RENDER_BLEND="$(AIM_RUN_REALISTIC_BLEND)" \
		AIM_RENDER_PRESET="$(PRESET)" \
		AIM_RENDER_RUNS= \
		AIM_RENDER_OUTPUT_DIR="$(AIM_RUN_RENDER_DIR)"
	@echo "Scene:   $(AIM_RUN_REALISTIC_BLEND)"
	@echo "Renders: $(AIM_RUN_RENDER_DIR)"

env:
	conda env create -f environment.yml || conda env update -f environment.yml --prune

env-update:
	conda env update -f environment.yml --prune

env-remove:
	conda env remove -n $(ENV_NAME)

env-info:
	conda run -n $(ENV_NAME) python -c "import sys; print(sys.executable); print(sys.version)"
	conda run -n $(ENV_NAME) python -c "import yaml; print('pyyaml ok')"
	conda run -n $(ENV_NAME) python -c "import gdstk; print('gdstk', gdstk.__version__)"
	conda run -n $(ENV_NAME) python -c "import gdsfactory as gf; print('gdsfactory', gf.__version__)"

aim-render-layers:
	conda run -n $(ENV_NAME) python scripts/aim_generate_doping_render_layers.py \
		--doping-rules $(AIM_DOPING_RULES) \
		--output $(AIM_RENDER_DOPING)
	conda run -n $(ENV_NAME) python scripts/aim_merge_render_layers.py \
		--doping $(AIM_RENDER_DOPING) \
		--static $(AIM_RENDER_STATIC) \
		--output $(AIM_RENDER_LAYERS)

aim-registry: aim-render-layers
	conda run -n $(ENV_NAME) python scripts/aim_build_layer_registry.py \
		--tech $(AIM_TECH) \
		--raw-custom $(AIM_RAW_CUSTOM) \
		--render-layers $(AIM_RENDER_LAYERS) \
		--doping-rules $(AIM_DOPING_RULES) \
		--output $(AIM_LAYER_REGISTRY)

aim-blendergds-config: aim-render-layers
	conda run -n $(ENV_NAME) python scripts/aim_generate_blendergds_config.py \
		--render-layers $(AIM_RENDER_LAYERS) \
		--output $(AIM_BLENDERGDS_CONFIG)

aim-preprocess-example: aim-registry aim-blendergds-config
	mkdir -p $(EXAMPLE_VISUAL_DIR)
	conda run -n $(ENV_NAME) python scripts/aim_preprocess_gds.py \
		--input $(EXAMPLE_GDS) \
		--registry $(AIM_LAYER_REGISTRY) \
		--output $(EXAMPLE_VISUAL_GDS) \
		--max-polygon-vertices $(AIM_PREPROCESS_MAX_POLYGON_VERTICES)

aim-preprocess-all-examples: aim-registry aim-blendergds-config
	mkdir -p $(EXAMPLE_VISUAL_DIR)
	@for gds in $(EXAMPLE_RAW_DIR)/*.gds; do \
		base=$$(basename $$gds .gds); \
		out="$(EXAMPLE_VISUAL_DIR)/$${base}.visual.gds"; \
		echo "Preprocessing $$gds -> $$out"; \
		conda run -n $(ENV_NAME) python scripts/aim_preprocess_gds.py \
			--input "$$gds" \
			--registry $(AIM_LAYER_REGISTRY) \
			--output "$$out" \
			--max-polygon-vertices $(AIM_PREPROCESS_MAX_POLYGON_VERTICES); \
	done

aim-blender-scene-example: aim-preprocess-example
	mkdir -p $(EXAMPLE_BLENDER_DIR)
	@if [ -z "$(AIM_BLENDER_COLORS)" ]; then \
		echo "No AIM color schemes found in $(AIM_BLENDER_COLOR_DIR)"; \
		exit 1; \
	fi
	@blend_dir=$$(dirname "$(EXAMPLE_BLEND)"); \
	blend_stem=$$(basename "$(EXAMPLE_BLEND)" .blend); \
	for color_config in $(AIM_BLENDER_COLORS); do \
		scheme=$$(basename "$$color_config" .yaml); \
		output="$${blend_dir}/$${blend_stem}.$${scheme}.blend"; \
		echo "Building Blender scene with $$scheme colors -> $$output"; \
		$(BLENDER) --background --python scripts/aim_build_blender_scene.py -- \
			--gds $(EXAMPLE_VISUAL_GDS) \
			--stack-config $(AIM_BLENDERGDS_CONFIG) \
			--color-config "$$color_config" \
			--output "$$output" \
			--z-scale $(AIM_BLENDER_Z_SCALE) \
			--camera-fit-margin $(AIM_BLENDER_CAMERA_FIT_MARGIN) \
			--cladding-mode $(AIM_BLENDER_CLADDING_MODE) \
			--cladding-boolean-solver $(AIM_BLENDER_CLADDING_BOOLEAN_SOLVER) \
			--cladding-undercut-method $(AIM_BLENDER_CLADDING_UNDERCUT_METHOD) \
			$(if $(strip $(AIM_BLENDER_CLADDING_UNFRACTURED_CUTTER)),--cladding-unfractured-cutter "$(AIM_BLENDER_CLADDING_UNFRACTURED_CUTTER)") \
			$(if $(strip $(AIM_BLENDER_CLADDING_EXPLICIT_MESH)),--cladding-explicit-mesh "$(AIM_BLENDER_CLADDING_EXPLICIT_MESH)") \
			$(if $(filter-out 0,$(AIM_BLENDER_CLADDING_EXPLICIT_CHUNK_SIZE_UM)),--cladding-explicit-chunk-size-um $(AIM_BLENDER_CLADDING_EXPLICIT_CHUNK_SIZE_UM)) \
			$(if $(filter 0 false no,$(AIM_BLENDER_APPLY_CLADDING_BOOLEAN)),--keep-live-cladding-boolean) \
			$(if $(filter 0 false no,$(AIM_BLENDER_MERGE_LAYERS)),--no-merge-layers) \
			$(if $(strip $(AIM_BLENDER_DELETE_LAYERS)),--delete-layers "$(AIM_BLENDER_DELETE_LAYERS)"); \
	done

aim-render-preset:
	$(BLENDER) --background $(AIM_RENDER_BLEND) \
		--python scripts/aim_render_scene.py -- \
		--preset $(AIM_RENDER_PRESET) \
		$(foreach run,$(AIM_RENDER_RUNS),--run $(run)) \
		$(if $(strip $(AIM_RENDER_OUTPUT_DIR)),--output-dir "$(AIM_RENDER_OUTPUT_DIR)")

aim-prepare-render-blend:
	$(BLENDER) --background $(AIM_RENDER_BLEND) \
		--python scripts/aim_prepare_render_blend.py -- \
		--preset $(AIM_RENDER_PRESET) \
		--run $(AIM_RENDER_READY_RUN) \
		$(if $(strip $(AIM_RENDER_READY_BLEND)),--output "$(AIM_RENDER_READY_BLEND)") \
		$(if $(strip $(AIM_RENDER_READY_OUTPUT)),--render-output "$(AIM_RENDER_READY_OUTPUT)") \
		$(if $(filter 0 false no,$(AIM_RENDER_READY_PACK)),--no-pack-resources)

aim-clean-generated:
	rm -f $(AIM_RENDER_DOPING)
	rm -f $(AIM_RENDER_LAYERS)
	rm -f $(AIM_LAYER_REGISTRY)
	rm -f $(AIM_BLENDERGDS_CONFIG)
	rm -f $(EXAMPLE_VISUAL_DIR)/*.visual.gds
	rm -f $(EXAMPLE_BLENDER_DIR)/*.blend
	rm -f $(EXAMPLE_BLENDER_DIR)/*.blend1
	rm -f $(EXAMPLE_RENDER_READY_DIR)/*.blend
	rm -f $(EXAMPLE_RENDER_READY_DIR)/*.blend1
