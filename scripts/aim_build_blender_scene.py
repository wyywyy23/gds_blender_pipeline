#!/usr/bin/env python3
"""
Build a Blender scene from an AIM visual GDS.

Run from Blender, for example:

  blender --background --python scripts/aim_build_blender_scene.py -- \
    --gds examples/aim_custom_tx_cell_undercut/visual/tx_array_checkered.visual.gds

This script intentionally owns the Blender-side modeling/post-processing flow:
BlenderGDS does the GDS extrusion, then this script fixes AIM-specific scene
details and saves the .blend artifact.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GDS = (
    REPO_ROOT
    / "examples/aim_custom_tx_cell_undercut/visual/tx_array_checkered.visual.gds"
)
DEFAULT_STACK_CONFIG = REPO_ROOT / "configs/blender/aim.yaml"
DEFAULT_COLOR_CONFIG = REPO_ROOT / "configs/blender/colors/aim/realistic.yaml"


def blender_argv(argv: list[str]) -> list[str]:
    if "--" in argv:
        return argv[argv.index("--") + 1 :]
    return []


def default_output_blend(gds_path: Path) -> Path:
    stem = gds_path.stem
    if stem.endswith(".visual"):
        stem = stem[: -len(".visual")]

    if gds_path.parent.name == "visual":
        out_dir = gds_path.parent.parent / "blender"
    else:
        out_dir = gds_path.parent / "blender"

    return out_dir / f"{stem}.blend"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and post-process an AIM Blender scene from a visual GDS."
    )
    parser.add_argument(
        "--gds",
        type=Path,
        default=DEFAULT_GDS,
        help="Preprocessed AIM visual GDS to load",
    )
    parser.add_argument(
        "--stack-config",
        type=Path,
        default=DEFAULT_STACK_CONFIG,
        help="BlenderGDS layer stack YAML, usually configs/blender/aim.yaml",
    )
    parser.add_argument(
        "--color-config",
        type=Path,
        default=DEFAULT_COLOR_CONFIG,
        help="BlenderGDS color schema YAML",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output .blend path. Defaults to examples/<name>/blender/<cell>.blend",
    )
    parser.add_argument(
        "--unit-scale",
        type=float,
        default=1e-6,
        help="GDS database unit scale passed to BlenderGDS",
    )
    parser.add_argument(
        "--z-scale",
        type=float,
        default=1.0,
        help="Vertical scale passed to BlenderGDS",
    )
    parser.add_argument(
        "--no-setup-scene",
        action="store_true",
        help="Disable BlenderGDS camera/sun/world setup",
    )
    parser.add_argument(
        "--keep-chip-base",
        action="store_true",
        help="Keep BlenderGDS's hardcoded ChipBase plane",
    )
    parser.add_argument(
        "--no-cladding-boolean",
        action="store_true",
        help="Skip subtracting the cladding undercut cutter from the cladding",
    )
    parser.add_argument(
        "--keep-cladding-cutter",
        action="store_true",
        help="Keep the cladding cutter object after boolean subtraction",
    )
    parser.add_argument(
        "--no-merge-layers",
        action="store_true",
        help="Disable BlenderGDS per-layer merge step",
    )
    parser.add_argument(
        "--no-create-collection",
        action="store_true",
        help="Import objects into the active collection instead of a new one",
    )
    parser.add_argument(
        "--no-clear-scene",
        action="store_true",
        help="Keep existing Blender scene contents before building",
    )
    parser.add_argument(
        "--no-apply-colors",
        action="store_true",
        help="Skip post-import material update from the color schema",
    )

    args = parser.parse_args(argv)
    args.gds = args.gds.resolve()
    args.stack_config = args.stack_config.resolve()
    args.color_config = args.color_config.resolve()
    args.output = (
        default_output_blend(args.gds).resolve()
        if args.output is None
        else args.output.resolve()
    )
    return args


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{label} does not exist: {path}")


def require_blendergds_operator(bpy: Any) -> None:
    if hasattr(bpy.ops.import_scene, "gdsii"):
        return

    for module_name in (
        "import_gdsii",
        "bl_ext.user_default.import_gdsii",
        "bl_ext.blender_org.import_gdsii",
    ):
        try:
            bpy.ops.preferences.addon_enable(module=module_name)
        except Exception:
            continue
        if hasattr(bpy.ops.import_scene, "gdsii"):
            return

    raise RuntimeError(
        "BlenderGDS operator bpy.ops.import_scene.gdsii is not available. "
        "Install and enable the BlenderGDS add-on before running this script."
    )


def clear_scene(bpy: Any) -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def remove_chip_base(bpy: Any) -> None:
    obj = bpy.data.objects.get("ChipBase")
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)
        print("Removed BlenderGDS ChipBase")


def find_imported_layer_object(bpy: Any, layer_name: str) -> Any | None:
    return bpy.data.objects.get(f"L{layer_name}") or bpy.data.objects.get(layer_name)


def set_active_object(bpy: Any, obj: Any) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def object_world_z_bounds(obj: Any) -> tuple[float, float]:
    from mathutils import Vector

    z_values = [(obj.matrix_world @ Vector(corner)).z for corner in obj.bound_box]
    return min(z_values), max(z_values)


def extend_cutter_z_through_target(
    bpy: Any,
    *,
    cutter: Any,
    target: Any,
    margin: float = 1.0,
) -> None:
    bpy.context.view_layer.update()
    target_zmin, target_zmax = object_world_z_bounds(target)
    cutter_zmin, cutter_zmax = object_world_z_bounds(cutter)
    cutter_zmid = (cutter_zmin + cutter_zmax) / 2.0

    desired_zmin = target_zmin - margin
    desired_zmax = target_zmax + margin
    world_to_local = cutter.matrix_world.inverted()

    for vertex in cutter.data.vertices:
        world_co = cutter.matrix_world @ vertex.co
        world_co.z = desired_zmin if world_co.z <= cutter_zmid else desired_zmax
        vertex.co = world_to_local @ world_co

    cutter.data.update()
    bpy.context.view_layer.update()
    print(
        "Extended cutter z-range for boolean: "
        f"{cutter.name} [{cutter_zmin:.3f}, {cutter_zmax:.3f}] -> "
        f"[{desired_zmin:.3f}, {desired_zmax:.3f}]"
    )


def boolean_difference(
    bpy: Any,
    *,
    target_name: str,
    cutter_name: str,
    keep_cutter: bool,
) -> bool:
    target = find_imported_layer_object(bpy, target_name)
    cutter = find_imported_layer_object(bpy, cutter_name)

    if target is None:
        print(f"Skipping boolean: target object missing for {target_name}")
        return False
    if cutter is None:
        print(f"Skipping boolean: cutter object missing for {cutter_name}")
        return False

    extend_cutter_z_through_target(bpy, cutter=cutter, target=target)
    set_active_object(bpy, target)
    modifier = target.modifiers.new(
        name=f"Subtract_{cutter_name}",
        type="BOOLEAN",
    )
    modifier.operation = "DIFFERENCE"
    modifier.object = cutter
    if hasattr(modifier, "operand_type"):
        modifier.operand_type = "OBJECT"
    if hasattr(modifier, "solver"):
        modifier.solver = "EXACT"
    if hasattr(modifier, "use_hole_tolerant"):
        modifier.use_hole_tolerant = True

    bpy.ops.object.modifier_apply(modifier=modifier.name)
    target.data.validate(clean_customdata=False)
    target.data.update()
    print(f"Boolean subtracted {cutter.name} from {target.name}")

    if keep_cutter:
        cutter.hide_viewport = True
        cutter.hide_render = True
        print(f"Hid cladding cutter object: {cutter.name}")
    else:
        cutter_name_before_remove = cutter.name
        bpy.data.objects.remove(cutter, do_unlink=True)
        print(f"Removed cladding cutter object: {cutter_name_before_remove}")

    return True


def apply_aim_booleans(bpy: Any, *, keep_cladding_cutter: bool) -> int:
    count = 0
    if boolean_difference(
        bpy,
        target_name="CLADDING_RENDER",
        cutter_name="CLADDING_UNDERCUT_CUTTER_RENDER",
        keep_cutter=keep_cladding_cutter,
    ):
        count += 1
    return count


def load_color_schema(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    layers = data.get("layers")
    if not isinstance(layers, dict):
        raise ValueError(f"{path} must contain a layers mapping")
    return data


def set_bsdf_input(bsdf: Any, material: Any, key: str, value: Any) -> None:
    if key in bsdf.inputs:
        bsdf.inputs[key].default_value = value
        if key == "Base Color":
            material.diffuse_color = tuple(value)
            if len(value) > 3 and "Alpha" in bsdf.inputs:
                bsdf.inputs["Alpha"].default_value = value[3]
        return

    if key == "Specular Type":
        bsdf.distribution = value
    elif key == "Subsurface Type":
        bsdf.subsurface_method = value
    else:
        print(f"Unknown Principled BSDF input: {key}")


def apply_color_schema(bpy: Any, color_config: Path) -> int:
    color_schema = load_color_schema(color_config)
    updated = 0

    for layer_name, material_cfg in color_schema["layers"].items():
        if not isinstance(material_cfg, dict):
            raise ValueError(f"{color_config}: layers.{layer_name} must be a mapping")

        material = bpy.data.materials.get(f"Mat_{layer_name}")
        if material is None:
            continue

        material.use_nodes = True
        bsdf = material.node_tree.nodes.get("Principled BSDF")
        if bsdf is None:
            print(f"Material {material.name} has no Principled BSDF node")
            continue

        for key, value in material_cfg.items():
            set_bsdf_input(bsdf, material, key, value)

        alpha = material_cfg.get("Alpha")
        base_color = material_cfg.get("Base Color")
        if alpha is None and isinstance(base_color, list) and len(base_color) > 3:
            alpha = base_color[3]
        if isinstance(alpha, (int, float)) and alpha < 1.0:
            material.blend_method = "BLEND"
            material.use_screen_refraction = True

        updated += 1

    return updated


def build_scene(args: argparse.Namespace) -> None:
    try:
        import bpy
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "This script must be run by Blender, for example with "
            "`blender --background --python scripts/aim_build_blender_scene.py -- ...`."
        ) from exc

    require_file(args.gds, "Input GDS")
    require_file(args.stack_config, "BlenderGDS stack config")
    require_file(args.color_config, "BlenderGDS color config")
    require_blendergds_operator(bpy)

    if not args.no_clear_scene:
        clear_scene(bpy)

    bpy.context.scene.gdsii_use_custom_config = True
    bpy.context.scene.gdsii_custom_config_path = str(args.stack_config)
    bpy.context.scene.gdsii_custom_color_path = str(args.color_config)

    print(f"Building AIM Blender scene from: {args.gds}")
    print(f"Layer stack config: {args.stack_config}")
    print(f"Color config:       {args.color_config}")

    result = bpy.ops.import_scene.gdsii(
        filepath=str(args.gds),
        setup_scene=not args.no_setup_scene,
        create_collection=not args.no_create_collection,
        merge_layers=not args.no_merge_layers,
        unit_scale=args.unit_scale,
        z_scale=args.z_scale,
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"BlenderGDS import did not finish: {result}")

    if not args.keep_chip_base:
        remove_chip_base(bpy)

    if not args.no_cladding_boolean:
        boolean_count = apply_aim_booleans(
            bpy,
            keep_cladding_cutter=args.keep_cladding_cutter,
        )
        print(f"Applied {boolean_count} AIM boolean operations")

    if not args.no_apply_colors:
        updated = apply_color_schema(bpy, args.color_config)
        print(f"Updated {updated} materials from AIM color schema")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output))
    print(f"Saved Blender scene: {args.output}")


def main() -> None:
    args = parse_args(blender_argv(sys.argv))
    build_scene(args)


if __name__ == "__main__":
    main()
