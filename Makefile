ENV_NAME := gds-blender-pipeline

AIM_TECH ?= external_pdks/AIMPhotonics_ACT1/tech.py

AIM_RAW_CUSTOM ?= configs/aim/raw_custom_layers.yaml
AIM_DOPING_RULES ?= configs/aim/doping_rules.yaml

AIM_RENDER_STATIC ?= configs/aim/render_layers.static.yaml
AIM_RENDER_DOPING ?= configs/aim/render_layers.doping.generated.yaml
AIM_RENDER_LAYERS ?= configs/aim/render_layers.yaml

AIM_LAYER_REGISTRY ?= configs/aim/layer_registry.local.yaml

EXAMPLE_DIR ?= examples/aim_custom_tx_cell_undercut
EXAMPLE_RAW_DIR ?= $(EXAMPLE_DIR)/raw
EXAMPLE_VISUAL_DIR ?= $(EXAMPLE_DIR)/visual
EXAMPLE_GDS ?= $(EXAMPLE_RAW_DIR)/tx_array_checkered.gds
EXAMPLE_VISUAL_GDS ?= $(EXAMPLE_VISUAL_DIR)/tx_array_checkered.visual.gds

.PHONY: \
	env env-update env-remove env-info \
	aim-render-layers aim-registry \
	aim-preprocess-example aim-preprocess-all-examples \
	aim-clean-generated

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

aim-preprocess-example: aim-registry
	mkdir -p $(EXAMPLE_VISUAL_DIR)
	conda run -n $(ENV_NAME) python scripts/aim_preprocess_gds.py \
		--input $(EXAMPLE_GDS) \
		--registry $(AIM_LAYER_REGISTRY) \
		--output $(EXAMPLE_VISUAL_GDS)

aim-preprocess-all-examples: aim-registry
	mkdir -p $(EXAMPLE_VISUAL_DIR)
	@for gds in $(EXAMPLE_RAW_DIR)/*.gds; do \
		base=$$(basename $$gds .gds); \
		out="$(EXAMPLE_VISUAL_DIR)/$${base}.visual.gds"; \
		echo "Preprocessing $$gds -> $$out"; \
		conda run -n $(ENV_NAME) python scripts/aim_preprocess_gds.py \
			--input "$$gds" \
			--registry $(AIM_LAYER_REGISTRY) \
			--output "$$out"; \
	done

aim-clean-generated:
	rm -f $(AIM_RENDER_DOPING)
	rm -f $(AIM_RENDER_LAYERS)
	rm -f $(AIM_LAYER_REGISTRY)
	rm -f $(EXAMPLE_VISUAL_DIR)/*.visual.gds