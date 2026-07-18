#!/usr/bin/env python3
"""Apply a YAML render preset to an open Blender scene and run named renders."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import re
import sys
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
FORMAT_EXTENSIONS = {
    "BMP": ".bmp",
    "JPEG": ".jpg",
    "OPEN_EXR": ".exr",
    "PNG": ".png",
    "TARGA": ".tga",
    "TIFF": ".tif",
}


def blender_argv(argv: list[str]) -> list[str]:
    if "--" in argv:
        return argv[argv.index("--") + 1 :]
    return []


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply a camera/render preset and render named visibility runs."
    )
    parser.add_argument("--preset", required=True, type=Path, help="Render preset YAML")
    parser.add_argument(
        "--run",
        dest="runs",
        action="append",
        default=[],
        help="Render only this named run; repeat to select multiple runs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override the preset output directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print each run without rendering files",
    )
    args = parser.parse_args(argv)
    args.preset = args.preset.resolve()
    if args.output_dir is not None:
        args.output_dir = args.output_dir.resolve()
    return args


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def require_token(value: Any, label: str) -> str:
    if not isinstance(value, str) or not TOKEN_RE.fullmatch(value):
        raise ValueError(
            f"{label} must use only letters, numbers, dots, underscores, or hyphens"
        )
    return value


def require_positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{label} must be a positive number")
    return float(value)


def require_vector3(value: Any, label: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{label} must be a three-element list")
    out: list[float] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"{label}[{index}] must be numeric")
        out.append(float(item))
    return out[0], out[1], out[2]


def normalize_layer_name(value: str) -> str:
    token = value.strip().upper().replace("-", "_")
    if token.startswith("L") and token[1:].endswith("_RENDER"):
        token = token[1:]
    if not token.endswith("_RENDER"):
        token += "_RENDER"
    return token


def load_preset(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data = require_mapping(data, str(path))
    if data.get("version") != 1:
        raise ValueError(f"{path}: version must be 1")

    name = require_token(data.get("name", path.stem), "name")
    camera_data = require_mapping(data.get("camera"), "camera")
    camera = {
        "object": camera_data.get("object", "Camera"),
        "type": camera_data.get("type", "PERSP").upper(),
        "location": require_vector3(camera_data.get("location"), "camera.location"),
        "rotation_degrees": require_vector3(
            camera_data.get("rotation_degrees"), "camera.rotation_degrees"
        ),
        "lens_mm": require_positive_number(
            camera_data.get("lens_mm"), "camera.lens_mm"
        ),
    }
    if not isinstance(camera["object"], str) or not camera["object"]:
        raise ValueError("camera.object must be a non-empty string")
    if camera["type"] != "PERSP":
        raise ValueError("Only PERSP camera presets are currently supported")

    lighting_data = data.get("lighting")
    lighting = None
    if lighting_data is not None:
        lighting_data = require_mapping(lighting_data, "lighting")
        sun_data = require_mapping(lighting_data.get("sun"), "lighting.sun")
        sun_object = sun_data.get("object", "Sun")
        if not isinstance(sun_object, str) or not sun_object:
            raise ValueError("lighting.sun.object must be a non-empty string")
        lighting = {
            "sun": {
                "object": sun_object,
                "strength": require_positive_number(
                    sun_data.get("strength"), "lighting.sun.strength"
                ),
            }
        }

    color_management_data = data.get("color_management")
    color_management = None
    if color_management_data is not None:
        color_management_data = require_mapping(
            color_management_data, "color_management"
        )
        view_transform = color_management_data.get("view_transform")
        look = color_management_data.get("look")
        if not isinstance(view_transform, str) or not view_transform:
            raise ValueError(
                "color_management.view_transform must be a non-empty string"
            )
        if not isinstance(look, str) or not look:
            raise ValueError("color_management.look must be a non-empty string")
        color_management = {
            "view_transform": view_transform,
            "look": look,
        }

    render_data = require_mapping(data.get("render"), "render")
    samples = render_data.get("samples")
    if isinstance(samples, bool) or not isinstance(samples, int) or samples <= 0:
        raise ValueError("render.samples must be a positive integer")
    denoise = render_data.get("denoise")
    if not isinstance(denoise, bool):
        raise ValueError("render.denoise must be true or false")
    adaptive_sampling = render_data.get("adaptive_sampling")
    if adaptive_sampling is not None and not isinstance(adaptive_sampling, bool):
        raise ValueError("render.adaptive_sampling must be true or false")
    file_format = str(render_data.get("file_format", "PNG")).upper()
    if file_format not in FORMAT_EXTENSIONS:
        raise ValueError(
            f"render.file_format must be one of: {', '.join(FORMAT_EXTENSIONS)}"
        )
    render = {
        "engine": str(render_data.get("engine", "CYCLES")).upper(),
        "samples": samples,
        "denoise": denoise,
        "adaptive_sampling": adaptive_sampling,
        "file_format": file_format,
    }
    if render["engine"] != "CYCLES":
        raise ValueError("Only the CYCLES render engine is currently supported")

    output_data = require_mapping(data.get("output", {}), "output")
    directory = output_data.get("directory")
    if not isinstance(directory, str) or not directory:
        raise ValueError("output.directory must be a non-empty path string")

    runs_data = data.get("runs")
    if not isinstance(runs_data, list) or not runs_data:
        raise ValueError("runs must be a non-empty list")
    runs: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, run_any in enumerate(runs_data):
        run_data = require_mapping(run_any, f"runs[{index}]")
        run_name = require_token(run_data.get("name"), f"runs[{index}].name")
        if run_name in seen_names:
            raise ValueError(f"Duplicate run name: {run_name}")
        seen_names.add(run_name)
        layers_any = run_data.get("hide_layers", [])
        if not isinstance(layers_any, list) or not all(
            isinstance(layer, str) and layer.strip() for layer in layers_any
        ):
            raise ValueError(f"runs[{index}].hide_layers must be a list of names")
        layers = list(dict.fromkeys(normalize_layer_name(layer) for layer in layers_any))
        runs.append({"name": run_name, "hide_layers": layers})

    return {
        "name": name,
        "camera": camera,
        "lighting": lighting,
        "color_management": color_management,
        "render": render,
        "output_directory": directory,
        "runs": runs,
    }


def strip_blender_numeric_suffix(name: str) -> str:
    if len(name) > 4 and name[-4] == "." and name[-3:].isdigit():
        return name[:-4]
    return name


def resolve_layer_objects(scene: Any, layer_name: str) -> list[Any]:
    candidates = {layer_name, f"L{layer_name}"}
    return [
        obj
        for obj in scene.objects
        if strip_blender_numeric_suffix(obj.name) in candidates
    ]


def resolve_run_layer_objects(scene: Any, run: dict[str, Any]) -> dict[str, list[Any]]:
    """Resolve and validate every render layer referenced by one preset run."""
    resolved: dict[str, list[Any]] = {}
    for layer_name in run["hide_layers"]:
        objects = resolve_layer_objects(scene, layer_name)
        if not objects:
            raise ValueError(f"Preset layer {layer_name} is not present in the scene")
        resolved[layer_name] = objects
    return resolved


def apply_run_visibility(
    run: dict[str, Any], resolved_layers: dict[str, list[Any]]
) -> list[str]:
    """Apply one run's render visibility and return the hidden object names."""
    hidden_objects: list[str] = []
    for layer_name in run["hide_layers"]:
        for obj in resolved_layers[layer_name]:
            obj.hide_render = True
            hidden_objects.append(obj.name)
    return hidden_objects


def select_runs(
    runs: list[dict[str, Any]], requested_names: list[str]
) -> list[dict[str, Any]]:
    if not requested_names:
        return runs
    run_by_name = {run["name"]: run for run in runs}
    unknown = [name for name in requested_names if name not in run_by_name]
    if unknown:
        raise ValueError("Unknown render runs: " + ", ".join(unknown))
    return [run_by_name[name] for name in dict.fromkeys(requested_names)]


def resolve_output_directory(
    preset: dict[str, Any], override: Path | None, *, example_name: str
) -> Path:
    if override is not None:
        return override
    configured_text = preset["output_directory"].replace(
        "{example_name}", example_name
    )
    configured = Path(configured_text)
    return configured if configured.is_absolute() else REPO_ROOT / configured


def apply_camera(scene: Any, camera_config: dict[str, Any]) -> Any:
    import bpy

    camera = bpy.data.objects.get(camera_config["object"])
    if camera is None or camera.type != "CAMERA":
        raise ValueError(
            f"Camera object not found: {camera_config['object']}"
        )
    scene.camera = camera
    camera.location = camera_config["location"]
    camera.rotation_mode = "XYZ"
    camera.rotation_euler = tuple(
        math.radians(value) for value in camera_config["rotation_degrees"]
    )
    camera.data.type = camera_config["type"]
    camera.data.lens = camera_config["lens_mm"]
    print(
        "Camera preset: "
        f"object={camera.name}, location={tuple(camera.location)}, "
        f"rotation_degrees={camera_config['rotation_degrees']}, "
        f"lens={camera.data.lens:g} mm"
    )
    return camera


def apply_lighting(scene: Any, lighting_config: dict[str, Any] | None) -> None:
    if lighting_config is None:
        return

    sun_config = lighting_config["sun"]
    sun = scene.objects.get(sun_config["object"])
    if sun is None or sun.type != "LIGHT" or sun.data.type != "SUN":
        raise ValueError(f"Sun light object not found: {sun_config['object']}")
    sun.data.energy = sun_config["strength"]
    print(f"Lighting preset: sun={sun.name}, strength={sun.data.energy:g}")


def apply_color_management(
    scene: Any, color_management_config: dict[str, Any] | None
) -> None:
    if color_management_config is None:
        return

    view_transform = color_management_config["view_transform"]
    look = color_management_config["look"]
    try:
        scene.view_settings.view_transform = view_transform
        scene.view_settings.look = look
    except TypeError as exc:
        raise ValueError(
            "Unsupported color-management setting: "
            f"view_transform={view_transform}, look={look}"
        ) from exc
    print(
        "Color management preset: "
        f"view_transform={scene.view_settings.view_transform}, "
        f"look={scene.view_settings.look}"
    )


def apply_render_settings(scene: Any, render_config: dict[str, Any]) -> None:
    scene.render.engine = render_config["engine"]
    scene.cycles.samples = render_config["samples"]
    scene.cycles.use_denoising = render_config["denoise"]
    adaptive_sampling = render_config["adaptive_sampling"]
    if adaptive_sampling is not None:
        scene.cycles.use_adaptive_sampling = adaptive_sampling

    for view_layer in scene.view_layers:
        view_layer.samples = 0
        view_layer.cycles.use_denoising = render_config["denoise"]

    compositor_tree = getattr(scene, "node_tree", None)
    if compositor_tree is None:
        compositor_tree = getattr(scene, "compositing_node_group", None)
    if compositor_tree is not None:
        for node in compositor_tree.nodes:
            if node.type == "DENOISE":
                node.mute = not render_config["denoise"]

    scene.render.image_settings.file_format = render_config["file_format"]
    scene.render.use_file_extension = True
    print(
        "Render preset: "
        f"engine={scene.render.engine}, samples={scene.cycles.samples}, "
        f"denoise={scene.cycles.use_denoising}, "
        f"adaptive_sampling={scene.cycles.use_adaptive_sampling}, "
        f"format={scene.render.image_settings.file_format}"
    )


def render_preset(args: argparse.Namespace) -> None:
    try:
        import bpy
    except ModuleNotFoundError as exc:
        raise RuntimeError("This script must be run from Blender") from exc

    if not bpy.data.filepath:
        raise RuntimeError("Open a saved .blend file before running this script")

    preset = load_preset(args.preset)
    runs = select_runs(preset["runs"], args.runs)
    scene = bpy.context.scene
    apply_camera(scene, preset["camera"])
    apply_lighting(scene, preset["lighting"])
    apply_color_management(scene, preset["color_management"])
    apply_render_settings(scene, preset["render"])

    resolved_layers: dict[str, list[Any]] = {}
    for run in runs:
        for layer_name, objects in resolve_run_layer_objects(scene, run).items():
            resolved_layers.setdefault(layer_name, objects)

    blend_stem = Path(bpy.data.filepath).stem
    example_name = blend_stem.split(".", 1)[0]
    output_dir = resolve_output_directory(
        preset,
        args.output_dir,
        example_name=example_name,
    )
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
    extension = FORMAT_EXTENSIONS[preset["render"]["file_format"]]
    baseline_visibility = {obj: obj.hide_render for obj in scene.objects}

    try:
        for run in runs:
            for obj, hidden in baseline_visibility.items():
                obj.hide_render = hidden
            hidden_objects = apply_run_visibility(run, resolved_layers)

            filename = (
                f"{blend_stem}.{preset['name']}.{run['name']}{extension}"
            )
            output_path = output_dir / filename
            scene.render.filepath = str(output_path)
            print(
                f"Render run {run['name']}: hidden={hidden_objects or 'none'}, "
                f"output={output_path}"
            )
            if not args.dry_run:
                result = bpy.ops.render.render(write_still=True)
                if "FINISHED" not in result:
                    raise RuntimeError(
                        f"Render run {run['name']} did not finish: {result}"
                    )
    finally:
        for obj, hidden in baseline_visibility.items():
            obj.hide_render = hidden

    print(
        f"{'Validated' if args.dry_run else 'Completed'} "
        f"{len(runs)} render run(s) from {args.preset}"
    )


def main() -> None:
    args = parse_args(blender_argv(sys.argv))
    render_preset(args)


if __name__ == "__main__":
    main()
