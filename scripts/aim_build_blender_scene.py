#!/usr/bin/env python3
"""
Build a Blender scene from an AIM visual GDS.

Run from Blender, for example:

  blender --background --python scripts/aim_build_blender_scene.py -- \
    --gds examples/aim_custom_tx_cell_undercut/visual/tx_array_checkered.visual.gds

This script intentionally owns the Blender-side modeling/post-processing flow:
BlenderGDS does the GDS extrusion, then this script fixes AIM-specific scene
details, applies AIM-specific booleans/materials, and saves the .blend artifact.
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


def split_layer_args(values: list[str] | None) -> list[str]:
    layers: list[str] = []
    seen: set[str] = set()

    for value in values or []:
        for layer in value.replace(",", " ").split():
            key = layer.casefold()
            if key in seen:
                continue
            seen.add(key)
            layers.append(layer)

    return layers


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
        "--show-cladding-cutter",
        action="store_true",
        help="Keep the cladding cutter visible after the boolean operation",
    )
    parser.add_argument(
        "--apply-cladding-boolean",
        action="store_true",
        help="Apply the cladding boolean destructively instead of keeping a live modifier",
    )
    parser.add_argument(
        "--keep-pn-conflicts",
        action="store_true",
        help="Keep PN conflict debug objects in the Blender scene",
    )
    parser.add_argument(
        "--delete-layer",
        "--delete-layers",
        dest="delete_layers",
        action="append",
        default=[],
        metavar="NAME[,NAME...]",
        help=(
            "Remove additional imported render layers after import. "
            "Accepts repeated flags or comma/space-separated names, and "
            "short AIM aliases such as cbam for CBAM_RENDER."
        ),
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
    args.delete_layers = split_layer_args(args.delete_layers)
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


def load_blendergds_layer_names(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a BlenderGDS layer mapping")
    return [name for name in data if isinstance(name, str)]


def delete_layer_name_candidates(name: str) -> list[str]:
    token = name.strip().upper().replace("-", "_")
    if token.startswith("MAT_"):
        token = token[len("MAT_") :]

    bases = [token]
    if token.startswith("L") and len(token) > 1:
        bases.append(token[1:])

    candidates: list[str] = []
    seen: set[str] = set()
    for base in bases:
        for candidate in (base, f"{base}_RENDER"):
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)

    return candidates


def resolve_delete_layer_names(
    requested_layers: list[str], stack_config: Path
) -> tuple[list[str], list[str]]:
    if not requested_layers:
        return [], []

    layer_names = load_blendergds_layer_names(stack_config)
    layer_by_normalized = {name.upper(): name for name in layer_names}

    resolved: list[str] = []
    unresolved: list[str] = []
    seen: set[str] = set()

    for requested in requested_layers:
        match = None
        for candidate in delete_layer_name_candidates(requested):
            match = layer_by_normalized.get(candidate)
            if match is not None:
                break

        if match is None:
            unresolved.append(requested)
            continue

        if match not in seen:
            seen.add(match)
            resolved.append(match)

    return resolved, unresolved


def strip_blender_numeric_suffix(name: str) -> str:
    if len(name) > 4 and name[-4] == "." and name[-3:].isdigit():
        return name[:-4]
    return name


def layer_datablock_name_map(layer_names: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for layer_name in layer_names:
        out[layer_name] = layer_name
        out[f"L{layer_name}"] = layer_name
        out[f"Mat_{layer_name}"] = layer_name
    return out


def remove_orphan_layer_datablocks(
    datablocks: Any, layer_by_datablock_name: dict[str, str]
) -> int:
    removed = 0
    for datablock in list(datablocks):
        if datablock.users != 0:
            continue
        base_name = strip_blender_numeric_suffix(datablock.name)
        if base_name not in layer_by_datablock_name:
            continue
        datablocks.remove(datablock)
        removed += 1
    return removed


def remove_orphan_mesh_refs(meshes: Any, mesh_refs: list[Any]) -> int:
    removed = 0
    seen: set[str] = set()

    for mesh in mesh_refs:
        if mesh is None:
            continue

        name = mesh.name
        if name in seen:
            continue
        seen.add(name)

        if mesh.users != 0:
            continue
        meshes.remove(mesh)
        removed += 1

    return removed


def remove_render_layer_objects(bpy: Any, layer_names: list[str]) -> int:
    if not layer_names:
        return 0

    layer_by_datablock_name = layer_datablock_name_map(layer_names)
    removed_by_layer = {layer_name: 0 for layer_name in layer_names}
    removed_object_meshes: list[Any] = []

    for obj in list(bpy.data.objects):
        base_name = strip_blender_numeric_suffix(obj.name)
        layer_name = layer_by_datablock_name.get(base_name)
        if layer_name is None:
            continue
        if getattr(obj, "type", None) == "MESH":
            removed_object_meshes.append(getattr(obj, "data", None))
        bpy.data.objects.remove(obj, do_unlink=True)
        removed_by_layer[layer_name] += 1

    removed_meshes = remove_orphan_mesh_refs(bpy.data.meshes, removed_object_meshes)
    removed_meshes += remove_orphan_layer_datablocks(
        bpy.data.meshes, layer_by_datablock_name
    )
    removed_materials = remove_orphan_layer_datablocks(
        bpy.data.materials, layer_by_datablock_name
    )

    for layer_name in layer_names:
        removed = removed_by_layer[layer_name]
        if removed:
            print(f"Removed {removed} objects from render layer {layer_name}")
        else:
            print(f"No imported objects found for render layer {layer_name}")

    if removed_meshes or removed_materials:
        print(
            "Removed orphan layer data-blocks: "
            f"{removed_meshes} meshes, {removed_materials} materials"
        )

    return sum(removed_by_layer.values())


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
        "Extended cladding cutter z-range: "
        f"{cutter.name} [{cutter_zmin:.3f}, {cutter_zmax:.3f}] -> "
        f"[{desired_zmin:.3f}, {desired_zmax:.3f}]"
    )


def recalculate_mesh_normals(obj: Any) -> None:
    import bmesh

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    print(f"Recalculated mesh normals: {obj.name}")


def apply_cladding_boolean(
    bpy: Any,
    *,
    show_cutter: bool,
    apply_modifier: bool,
) -> bool:
    target = find_imported_layer_object(bpy, "CLADDING_RENDER")
    cutter = find_imported_layer_object(bpy, "CLADDING_UNDERCUT_CUTTER_RENDER")

    if target is None:
        print("Skipping cladding boolean: LCLADDING_RENDER not found")
        return False
    if cutter is None:
        print("Skipping cladding boolean: LCLADDING_UNDERCUT_CUTTER_RENDER not found")
        return False

    extend_cutter_z_through_target(bpy, cutter=cutter, target=target)
    recalculate_mesh_normals(target)
    recalculate_mesh_normals(cutter)

    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target

    bpy.ops.object.modifier_add(type="BOOLEAN")
    modifier = target.modifiers[len(target.modifiers) - 1]
    modifier.name = "Cladding_Undercut"
    modifier.operation = "DIFFERENCE"
    modifier.object = cutter

    if apply_modifier:
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        print(f"Applied cladding boolean modifier: {cutter.name} -> {target.name}")
    else:
        modifier.show_viewport = True
        modifier.show_render = True
        print(f"Added live cladding boolean modifier: {cutter.name} -> {target.name}")

    if show_cutter:
        print(f"Kept cladding cutter visible: {cutter.name}")
    else:
        cutter.hide_viewport = True
        cutter.hide_render = True
        print(f"Hid cladding cutter object: {cutter.name}")

    return True


def remove_pn_conflict_debug_objects(bpy: Any) -> int:
    removed = 0
    for obj in list(bpy.data.objects):
        if "PN_CONFLICT" not in obj.name:
            continue
        bpy.data.objects.remove(obj, do_unlink=True)
        removed += 1

    for mesh in list(bpy.data.meshes):
        if "PN_CONFLICT" in mesh.name and mesh.users == 0:
            bpy.data.meshes.remove(mesh)

    for material in list(bpy.data.materials):
        if "PN_CONFLICT" in material.name and material.users == 0:
            bpy.data.materials.remove(material)

    if removed:
        print(f"Removed {removed} PN conflict debug objects")
    return removed


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

    if not args.keep_pn_conflicts:
        remove_pn_conflict_debug_objects(bpy)

    delete_layers, unknown_delete_layers = resolve_delete_layer_names(
        args.delete_layers, args.stack_config
    )
    for layer in unknown_delete_layers:
        print(f"Requested delete layer not found in stack config: {layer}")
    remove_render_layer_objects(bpy, delete_layers)

    if not args.no_cladding_boolean:
        applied = apply_cladding_boolean(
            bpy,
            show_cutter=args.show_cladding_cutter,
            apply_modifier=args.apply_cladding_boolean,
        )
        print(f"Applied cladding boolean: {applied}")

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
