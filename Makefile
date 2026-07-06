ENV_NAME := gds-blender-pipeline

.PHONY: env env-update env-remove env-info

env:
	conda env create -f environment.yml || conda env update -f environment.yml --prune

env-update:
	conda env update -f environment.yml --prune

env-remove:
	conda env remove -n $(ENV_NAME)

env-info:
	conda run -n $(ENV_NAME) python -c "import sys; print(sys.executable); print(sys.version)"
	conda run -n $(ENV_NAME) python -c "import gdsfactory as gf; print('gdsfactory', gf.__version__)"
	conda run -n $(ENV_NAME) python -c "import kfactory as kf; print('kfactory', kf.__version__)"