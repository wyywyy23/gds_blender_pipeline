#!/usr/bin/env python3
"""Apply one render-preset run and save a portable, render-ready .blend file."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aim_render_scene import (
    apply_camera,
    apply_render_settings,
    apply_run_visibility,
    blender_argv,
    load_preset,
    resolve_run_layer_objects,
    select_runs,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Apply one camera/render/visibility preset run and save a .blend "
            "that can be rendered without this repository's Python scripts."
        )
    )
    parser.add_argument("--preset", required=True, type=Path, help="Render preset YAML")
    parser.add_argument(
        "--run",
        required=True,
        help="Single preset run whose layer visibility will be baked into the file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Output .blend path. By default, append the preset and run names to "
            "the open file's name."
        ),
    )
    parser.add_argument(
        "--render-output",
        default=None,
        help=(
            "Render filepath stored in the .blend. Defaults to a //renders path "
            "relative to the prepared file."
        ),
    )
    parser.add_argument(
        "--no-pack-resources",
        action="store_true",
        help="Do not pack external images, fonts, and other packable resources",
    )
    parser.add_argument(
        "--overwrite-source",
        action="store_true",
        help="Allow --output to replace the currently open .blend file",
    )
    args = parser.parse_args(argv)
    args.preset = args.preset.resolve()
    if args.output is not None:
        args.output = args.output.resolve()
        if args.output.suffix.lower() != ".blend":
            parser.error("--output must end in .blend")
    if args.render_output is not None and not args.render_output.strip():
        parser.error("--render-output must be non-empty")
    return args


def default_output_blend(
    source: Path, *, preset_name: str, run_name: str
) -> Path:
    return source.with_name(f"{source.stem}.{preset_name}.{run_name}.blend")


def default_render_output(output_blend: Path) -> str:
    return f"//renders/{output_blend.stem}"


def pack_resources(bpy: Any) -> None:
    result = bpy.ops.file.pack_all()
    if "FINISHED" not in result:
        raise RuntimeError(f"Packing external resources did not finish: {result}")
    print("Packed external resources into the prepared .blend")


def prepare_render_blend(args: argparse.Namespace) -> None:
    try:
        import bpy
    except ModuleNotFoundError as exc:
        raise RuntimeError("This script must be run from Blender") from exc

    if not bpy.data.filepath:
        raise RuntimeError("Open a saved .blend file before running this script")

    source = Path(bpy.data.filepath).resolve()
    preset = load_preset(args.preset)
    run = select_runs(preset["runs"], [args.run])[0]
    output = args.output or default_output_blend(
        source, preset_name=preset["name"], run_name=run["name"]
    )
    output = output.resolve()
    if output == source and not args.overwrite_source:
        raise ValueError(
            "Refusing to overwrite the source .blend; choose another --output or "
            "pass --overwrite-source"
        )

    scene = bpy.context.scene
    resolved_layers = resolve_run_layer_objects(scene, run)
    apply_camera(scene, preset["camera"])
    apply_render_settings(scene, preset["render"])
    hidden_objects = apply_run_visibility(run, resolved_layers)

    scene.render.filepath = args.render_output or default_render_output(output)
    scene["aim_render_preset"] = preset["name"]
    scene["aim_render_run"] = run["name"]
    scene["aim_source_blend"] = source.name
    print(
        f"Prepared run {run['name']}: hidden={hidden_objects or 'none'}, "
        f"render_output={scene.render.filepath}"
    )

    if not args.no_pack_resources:
        pack_resources(bpy)

    output.parent.mkdir(parents=True, exist_ok=True)
    result = bpy.ops.wm.save_as_mainfile(filepath=str(output), relative_remap=True)
    if "FINISHED" not in result:
        raise RuntimeError(f"Saving prepared .blend did not finish: {result}")
    print(f"Saved render-ready Blender file: {output}")


def main() -> None:
    args = parse_args(blender_argv(sys.argv))
    prepare_render_blend(args)


if __name__ == "__main__":
    main()
