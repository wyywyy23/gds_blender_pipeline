#!/usr/bin/env python3
"""
Build a Blender scene from an AIM visual GDS.

Run from Blender, for example:

  blender --background --python scripts/aim_build_blender_scene.py -- \
    --gds examples/aim/visual/tx_array_checkered.visual.gds

This script intentionally owns the Blender-side modeling/post-processing flow:
BlenderGDS does the GDS extrusion, then this script fixes AIM-specific scene
details, applies AIM-specific booleans/materials, and saves the .blend artifact.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from itertools import product
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterator

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GDS = (
    REPO_ROOT
    / "examples/aim/visual/tx_array_checkered.visual.gds"
)
DEFAULT_STACK_CONFIG = REPO_ROOT / "configs/blender/aim.yaml"
DEFAULT_COLOR_CONFIG = REPO_ROOT / "configs/blender/colors/aim/realistic.yaml"
CLADDING_LAYER = "CLADDING_RENDER"
CLADDING_UNDERCUT_CUTTER_LAYER = "CLADDING_UNDERCUT_CUTTER_RENDER"
CLADDING_PASSIVATION_CUTTER_LAYER = "CLADDING_PASSIVATION_CUTTER_RENDER"
CLADDING_CUTTER_LAYERS = (
    CLADDING_UNDERCUT_CUTTER_LAYER,
    CLADDING_PASSIVATION_CUTTER_LAYER,
)
CLADDING_MODES = ("boolean", "solid", "omit")
CLADDING_BOOLEAN_SOLVERS = ("manifold", "exact")


def positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def camera_margin_float(value: str) -> float:
    number = positive_float(value)
    if number < 1.0:
        raise argparse.ArgumentTypeError("must be at least 1.0")
    return number


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
        type=positive_float,
        default=1e-6,
        help="GDS database unit scale passed to BlenderGDS",
    )
    parser.add_argument(
        "--z-scale",
        type=positive_float,
        default=1.0,
        help=(
            "Vertical exaggeration passed to BlenderGDS; scales layer elevations "
            "and thicknesses without changing XY dimensions"
        ),
    )
    parser.add_argument(
        "--camera-fit-margin",
        type=camera_margin_float,
        default=1.10,
        help=(
            "Camera framing multiplier applied around the imported chip "
            "bounding box (default: 1.10)"
        ),
    )
    parser.add_argument(
        "--no-fit-camera",
        action="store_true",
        help="Keep BlenderGDS's fixed camera placement instead of fitting the chip",
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
        "--cladding-mode",
        choices=CLADDING_MODES,
        default="boolean",
        help=(
            "Cladding handling: boolean imports cladding and its TUAM/PAAM "
            "cutters and adds the opening Booleans; solid imports only uncut "
            "cladding; omit imports neither cladding nor cutters (default: boolean)"
        ),
    )
    parser.add_argument(
        "--no-cladding-boolean",
        action="store_true",
        help="Deprecated alias for --cladding-mode solid",
    )
    parser.add_argument(
        "--show-cladding-cutter",
        action="store_true",
        help="Keep the TUAM and PAAM cladding cutters visible after the booleans",
    )
    parser.add_argument(
        "--cladding-boolean-solver",
        choices=CLADDING_BOOLEAN_SOLVERS,
        default="manifold",
        help=(
            "Boolean solver for graph-separated cladding cutter batches. "
            "manifold is the low-memory default; exact is a diagnostic "
            "fallback"
        ),
    )
    boolean_storage = parser.add_mutually_exclusive_group()
    boolean_storage.add_argument(
        "--apply-cladding-boolean",
        dest="apply_cladding_boolean",
        action="store_true",
        default=None,
        help=(
            "Bake cladding Boolean batches into the mesh (default in boolean "
            "mode)"
        ),
    )
    boolean_storage.add_argument(
        "--keep-live-cladding-boolean",
        dest="apply_cladding_boolean",
        action="store_false",
        help=(
            "Keep cladding Boolean modifiers live for debugging; this can use "
            "substantially more memory during dependency-graph evaluation"
        ),
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
    if args.no_cladding_boolean:
        if args.cladding_mode == "omit":
            parser.error(
                "--no-cladding-boolean cannot be combined with "
                "--cladding-mode omit"
            )
        args.cladding_mode = "solid"
    if args.cladding_mode != "boolean" and args.show_cladding_cutter:
        parser.error("--show-cladding-cutter requires --cladding-mode boolean")
    if args.cladding_mode != "boolean" and args.apply_cladding_boolean is not None:
        parser.error("--apply-cladding-boolean requires --cladding-mode boolean")
    if args.apply_cladding_boolean is None:
        args.apply_cladding_boolean = args.cladding_mode == "boolean"

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


def excluded_cladding_layers(mode: str) -> set[str]:
    if mode == "boolean":
        return set()
    if mode == "solid":
        return set(CLADDING_CUTTER_LAYERS)
    if mode == "omit":
        return {CLADDING_LAYER, *CLADDING_CUTTER_LAYERS}
    raise ValueError(f"Unknown cladding mode: {mode}")


def prepare_import_stack_config(
    source: Path, *, cladding_mode: str, temp_dir: Path
) -> Path:
    """Write a temporary stack without cladding layers excluded before import."""
    excluded = excluded_cladding_layers(cladding_mode)
    if not excluded:
        return source

    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{source} must contain a BlenderGDS layer mapping")

    filtered = {name: spec for name, spec in data.items() if name not in excluded}
    destination = temp_dir / source.name
    destination.write_text(
        "# Temporary BlenderGDS stack generated by aim_build_blender_scene.py\n"
        f"# Cladding mode: {cladding_mode}\n\n"
        + yaml.safe_dump(filtered, sort_keys=False),
        encoding="utf-8",
    )
    removed = sorted(excluded & set(data))
    print(
        f"Cladding mode {cladding_mode}: excluded before import: "
        + ", ".join(removed)
    )
    return destination


def require_blendergds_operator(bpy: Any) -> None:
    def operator_is_registered() -> bool:
        # bpy.ops dynamically manufactures operator proxies, so hasattr() can
        # return True even when no implementation is registered.
        try:
            bpy.ops.import_scene.gdsii.get_rna_type()
        except (AttributeError, KeyError, RuntimeError):
            return False
        return True

    if operator_is_registered():
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
        if operator_is_registered():
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


def imported_layer_world_bounds(
    bpy: Any, layer_names: list[str]
) -> tuple[tuple[float, float, float], tuple[float, float, float], int]:
    """Return world-space bounds for visible imported render-layer meshes."""
    from mathutils import Vector

    layer_object_names = {
        name
        for layer_name in layer_names
        for name in (layer_name, f"L{layer_name}")
    }
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points: list[Any] = []
    object_count = 0

    for obj in bpy.context.scene.objects:
        if getattr(obj, "type", None) != "MESH" or obj.hide_render:
            continue
        if strip_blender_numeric_suffix(obj.name) not in layer_object_names:
            continue

        evaluated = obj.evaluated_get(depsgraph)
        if not getattr(evaluated.data, "vertices", None):
            continue

        points.extend(
            evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box
        )
        object_count += 1

    if not points:
        raise RuntimeError("No visible imported render-layer meshes found for camera fit")

    minimum = tuple(min(point[axis] for point in points) for axis in range(3))
    maximum = tuple(max(point[axis] for point in points) for axis in range(3))
    return minimum, maximum, object_count


def camera_frame_tangents(camera_data: Any, scene: Any) -> tuple[float, float]:
    """Return horizontal and vertical half-frame size per unit camera distance."""
    frame = camera_data.view_frame(scene=scene)
    horizontal = max(abs(corner.x / corner.z) for corner in frame if corner.z)
    vertical = max(abs(corner.y / corner.z) for corner in frame if corner.z)
    if horizontal <= 0 or vertical <= 0:
        raise RuntimeError("Camera has an invalid perspective frame")
    return horizontal, vertical


def verify_camera_contains_bounds(
    bpy: Any,
    camera: Any,
    minimum: tuple[float, float, float],
    maximum: tuple[float, float, float],
) -> tuple[float, float, float, float]:
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector

    scene = bpy.context.scene
    projected = [
        world_to_camera_view(scene, camera, Vector(corner))
        for corner in product(
            (minimum[0], maximum[0]),
            (minimum[1], maximum[1]),
            (minimum[2], maximum[2]),
        )
    ]
    coverage = (
        min(point.x for point in projected),
        max(point.x for point in projected),
        min(point.y for point in projected),
        max(point.y for point in projected),
    )
    tolerance = 1e-5
    if (
        coverage[0] < -tolerance
        or coverage[1] > 1.0 + tolerance
        or coverage[2] < -tolerance
        or coverage[3] > 1.0 + tolerance
    ):
        raise RuntimeError(
            "Camera fit did not contain the chip bounds: "
            f"x=[{coverage[0]:.4f}, {coverage[1]:.4f}], "
            f"y=[{coverage[2]:.4f}, {coverage[3]:.4f}]"
        )
    return coverage


def fit_camera_to_imported_layers(
    bpy: Any, *, layer_names: list[str], margin: float
) -> bool:
    """Fit BlenderGDS's top-down camera without changing chip or Sun transforms."""
    scene = bpy.context.scene
    camera = scene.camera
    if camera is None:
        print("Skipping camera fit: scene has no active camera")
        return False
    if camera.type != "CAMERA":
        raise RuntimeError(f"Active camera object has unexpected type: {camera.type}")

    minimum, maximum, object_count = imported_layer_world_bounds(bpy, layer_names)
    width = maximum[0] - minimum[0]
    height = maximum[1] - minimum[1]
    depth = maximum[2] - minimum[2]
    if width <= 0 or height <= 0:
        raise RuntimeError(
            f"Invalid imported chip bounds: minimum={minimum}, maximum={maximum}"
        )

    center_x = (minimum[0] + maximum[0]) / 2.0
    center_y = (minimum[1] + maximum[1]) / 2.0
    camera.data.shift_x = 0.0
    camera.data.shift_y = 0.0
    camera.rotation_mode = "XYZ"
    camera.rotation_euler = (0.0, 0.0, 0.0)

    if camera.data.type == "PERSP":
        half_frame_x, half_frame_y = camera_frame_tangents(camera.data, scene)
        distance = margin * max(
            width / (2.0 * half_frame_x),
            height / (2.0 * half_frame_y),
        )
    elif camera.data.type == "ORTHO":
        frame = camera.data.view_frame(scene=scene)
        frame_width = max(corner.x for corner in frame) - min(
            corner.x for corner in frame
        )
        frame_height = max(corner.y for corner in frame) - min(
            corner.y for corner in frame
        )
        scale_factor = margin * max(width / frame_width, height / frame_height)
        camera.data.ortho_scale *= scale_factor
        distance = max(width, height, depth, 1.0)
    else:
        raise RuntimeError(
            f"Automatic camera fit does not support {camera.data.type} cameras"
        )

    camera.location = (center_x, center_y, maximum[2] + distance)
    scene_extent = max(width, height, depth, 1.0)
    camera.data.clip_start = max(distance * 1e-4, 1e-4)
    camera.data.clip_end = distance + depth + scene_extent * 0.25
    bpy.context.view_layer.update()

    coverage = verify_camera_contains_bounds(bpy, camera, minimum, maximum)
    print(
        "Camera fit: "
        f"{object_count} visible layers, bounds={width:.3f} x {height:.3f} x "
        f"{depth:.3f}, margin={margin:.3f}"
    )
    print(
        "Camera placement: "
        f"location=({camera.location.x:.3f}, {camera.location.y:.3f}, "
        f"{camera.location.z:.3f}), clip=[{camera.data.clip_start:.6f}, "
        f"{camera.data.clip_end:.3f}]"
    )
    print(
        "Camera frame coverage: "
        f"x=[{coverage[0]:.4f}, {coverage[1]:.4f}], "
        f"y=[{coverage[2]:.4f}, {coverage[3]:.4f}]"
    )
    return True


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


def mesh_component_labels(mesh: Any) -> tuple[list[int], int]:
    adjacency: list[list[int]] = [[] for _ in mesh.vertices]
    for edge in mesh.edges:
        first, second = edge.vertices
        adjacency[first].append(second)
        adjacency[second].append(first)

    labels = [-1] * len(mesh.vertices)
    component_count = 0
    for seed in range(len(mesh.vertices)):
        if labels[seed] != -1:
            continue
        labels[seed] = component_count
        stack = [seed]
        while stack:
            current = stack.pop()
            for neighbor in adjacency[current]:
                if labels[neighbor] == -1:
                    labels[neighbor] = component_count
                    stack.append(neighbor)
        component_count += 1

    return labels, component_count


def color_touching_mesh_components(
    cutter: Any,
    labels: list[int],
    component_count: int,
    *,
    tolerance: float = 1.0e-4,
) -> list[int]:
    coordinate_components: dict[tuple[int, int], set[int]] = defaultdict(set)
    for vertex in cutter.data.vertices:
        world_co = cutter.matrix_world @ vertex.co
        coordinate = (
            round(world_co.x / tolerance),
            round(world_co.y / tolerance),
        )
        coordinate_components[coordinate].add(labels[vertex.index])

    neighbors = [set() for _ in range(component_count)]
    for components_at_coordinate in coordinate_components.values():
        components = tuple(components_at_coordinate)
        for index, first in enumerate(components):
            for second in components[index + 1 :]:
                neighbors[first].add(second)
                neighbors[second].add(first)

    colors = [-1] * component_count
    order = sorted(
        range(component_count),
        key=lambda component: len(neighbors[component]),
        reverse=True,
    )
    for component in order:
        unavailable = {
            colors[neighbor]
            for neighbor in neighbors[component]
            if colors[neighbor] >= 0
        }
        color = 0
        while color in unavailable:
            color += 1
        colors[component] = color

    return colors


def iter_cladding_boolean_batches(
    bpy: Any,
    *,
    cutter: Any,
    batch_prefix: str,
) -> Iterator[Any]:
    labels, component_count = mesh_component_labels(cutter.data)
    if component_count == 0:
        raise RuntimeError(f"Cladding cutter has no mesh components: {cutter.name}")

    colors = color_touching_mesh_components(cutter, labels, component_count)
    batch_count = max(colors) + 1
    sizes = [colors.count(color) for color in range(batch_count)]
    print(
        f"Split {cutter.name} into non-touching boolean batches: "
        f"{component_count} components -> {batch_count} batches {sizes}"
    )

    for color in range(batch_count):
        old_to_new: dict[int, int] = {}
        coordinates = []
        faces = []
        for polygon in cutter.data.polygons:
            polygon_vertices = tuple(polygon.vertices)
            component = labels[polygon_vertices[0]]
            if colors[component] != color:
                continue

            face = []
            for old_index in polygon_vertices:
                new_index = old_to_new.get(old_index)
                if new_index is None:
                    new_index = len(coordinates)
                    old_to_new[old_index] = new_index
                    coordinates.append(cutter.data.vertices[old_index].co.copy())
                face.append(new_index)
            faces.append(face)

        name = f"{batch_prefix}{color + 1:02d}"
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(coordinates, [], faces)
        mesh.update()
        batch = bpy.data.objects.new(name, mesh)
        cutter.users_collection[0].objects.link(batch)
        batch.matrix_world = cutter.matrix_world.copy()
        batch.hide_viewport = True
        batch.hide_render = True
        yield batch


def validate_bounded_cutter_crosses_target_top(*, cutter: Any, target: Any) -> None:
    target_zmin, target_zmax = object_world_z_bounds(target)
    cutter_zmin, cutter_zmax = object_world_z_bounds(cutter)
    if not target_zmin < cutter_zmin < target_zmax < cutter_zmax:
        raise RuntimeError(
            f"Bounded cladding cutter {cutter.name} must start inside "
            f"{target.name} and end above it: cutter=[{cutter_zmin:.6f}, "
            f"{cutter_zmax:.6f}], target=[{target_zmin:.6f}, {target_zmax:.6f}]"
        )
    print(
        "Validated bounded cladding cutter z-range: "
        f"{cutter.name} [{cutter_zmin:.3f}, {cutter_zmax:.3f}] crosses "
        f"{target.name} top at {target_zmax:.3f}"
    )


def remove_boolean_batches(bpy: Any, batches: list[Any]) -> None:
    for batch in batches:
        mesh = batch.data
        bpy.data.objects.remove(batch, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def add_cladding_cutter_boolean(
    bpy: Any,
    *,
    target: Any,
    cutter: Any,
    modifier_prefix: str,
    batch_prefix: str,
    solver: str,
    apply_modifier: bool,
) -> int:
    recalculate_mesh_normals(cutter)

    # Polygon fracturing keeps GDS records/imports manageable, but it also makes
    # each cutter a compound mesh of closed prisms. Some prisms touch at shared
    # edges or points, so a single Boolean operand is unreliable. Graph-color
    # touching components into separate, non-touching batches. MANIFOLD is both
    # reliable on those batches and far less memory-intensive than EXACT.
    blender_solver = solver.upper()
    batch_count = 0
    batches = iter_cladding_boolean_batches(
        bpy,
        cutter=cutter,
        batch_prefix=batch_prefix,
    )
    for index, batch in enumerate(batches, start=1):
        batch_count = index
        modifier = target.modifiers.new(f"{modifier_prefix}_{index:02d}", "BOOLEAN")
        modifier.operation = "DIFFERENCE"
        modifier.object = batch
        modifier.solver = blender_solver
        modifier.show_viewport = True
        modifier.show_render = True

        if apply_modifier:
            bpy.ops.object.modifier_apply(modifier=modifier.name)
            remove_boolean_batches(bpy, [batch])

    if apply_modifier:
        print(
            f"Applied {batch_count} {blender_solver} cladding boolean modifiers: "
            f"{cutter.name} -> {target.name}"
        )
    else:
        print(
            f"Added {batch_count} live {blender_solver} cladding boolean modifiers: "
            f"{cutter.name} -> {target.name}"
        )

    return batch_count


def apply_cladding_boolean(
    bpy: Any,
    *,
    show_cutter: bool,
    solver: str,
    apply_modifier: bool,
) -> bool:
    target = find_imported_layer_object(bpy, CLADDING_LAYER)

    if target is None:
        print("Skipping cladding boolean: LCLADDING_RENDER not found")
        return False

    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    recalculate_mesh_normals(target)

    cutter_specs = (
        {
            "layer": CLADDING_UNDERCUT_CUTTER_LAYER,
            "modifier_prefix": "Cladding_Undercut",
            "batch_prefix": "CladdingUndercutBatch",
            "extend_through_target": True,
        },
        {
            "layer": CLADDING_PASSIVATION_CUTTER_LAYER,
            "modifier_prefix": "Cladding_Passivation",
            "batch_prefix": "CladdingPassivationBatch",
            "extend_through_target": False,
        },
    )
    applied_cutters = 0
    applied_modifiers = 0

    for spec in cutter_specs:
        cutter = find_imported_layer_object(bpy, spec["layer"])
        if cutter is None:
            print(f"Skipping cladding cutter not present in scene: L{spec['layer']}")
            continue

        if spec["extend_through_target"]:
            extend_cutter_z_through_target(bpy, cutter=cutter, target=target)
        else:
            validate_bounded_cutter_crosses_target_top(cutter=cutter, target=target)

        applied_modifiers += add_cladding_cutter_boolean(
            bpy,
            target=target,
            cutter=cutter,
            modifier_prefix=spec["modifier_prefix"],
            batch_prefix=spec["batch_prefix"],
            solver=solver,
            apply_modifier=apply_modifier,
        )
        applied_cutters += 1

        if show_cutter:
            print(f"Kept cladding cutter visible: {cutter.name}")
        else:
            cutter.hide_viewport = True
            cutter.hide_render = True
            print(f"Hid cladding cutter object: {cutter.name}")

    print(
        f"Cladding Boolean summary: {applied_cutters} cutters, "
        f"{applied_modifiers} modifiers"
    )
    return applied_cutters > 0


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

    scene = bpy.context.scene
    scene.gdsii_use_custom_config = True
    scene.gdsii_custom_color_path = str(args.color_config)

    print(f"Building AIM Blender scene from: {args.gds}")
    print(f"Layer stack config: {args.stack_config}")
    print(f"Color config:       {args.color_config}")
    print(f"GDS unit scale:     {args.unit_scale:g}")
    print(f"Z exaggeration:     {args.z_scale:g}x")
    print(f"Cladding mode:      {args.cladding_mode}")
    if args.cladding_mode == "boolean":
        print(f"Cladding solver:    {args.cladding_boolean_solver.upper()}")
        print(
            "Cladding storage:   "
            + ("baked" if args.apply_cladding_boolean else "live modifiers")
        )
    if args.no_cladding_boolean:
        print("Deprecated --no-cladding-boolean mapped to --cladding-mode solid")

    with tempfile.TemporaryDirectory(prefix="aim_blendergds_stack_") as temp_name:
        import_stack_config = prepare_import_stack_config(
            args.stack_config,
            cladding_mode=args.cladding_mode,
            temp_dir=Path(temp_name),
        )
        scene.gdsii_custom_config_path = str(import_stack_config)
        result = bpy.ops.import_scene.gdsii(
            filepath=str(args.gds),
            setup_scene=not args.no_setup_scene,
            create_collection=not args.no_create_collection,
            merge_layers=not args.no_merge_layers,
            unit_scale=args.unit_scale,
            z_scale=args.z_scale,
        )
    scene.gdsii_custom_config_path = str(args.stack_config)
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

    if args.cladding_mode == "boolean":
        applied = apply_cladding_boolean(
            bpy,
            show_cutter=args.show_cladding_cutter,
            solver=args.cladding_boolean_solver,
            apply_modifier=args.apply_cladding_boolean,
        )
        print(f"Applied cladding boolean: {applied}")
    elif args.cladding_mode == "solid":
        print("Kept solid cladding without an undercut opening")
    else:
        print("Omitted cladding and cladding undercut cutter")

    if not args.no_apply_colors:
        updated = apply_color_schema(bpy, args.color_config)
        print(f"Updated {updated} materials from AIM color schema")

    if not args.no_fit_camera:
        fitted = fit_camera_to_imported_layers(
            bpy,
            layer_names=load_blendergds_layer_names(args.stack_config),
            margin=args.camera_fit_margin,
        )
        print(f"Fitted camera to imported chip: {fitted}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output))
    print(f"Saved Blender scene: {args.output}")


def main() -> None:
    args = parse_args(blender_argv(sys.argv))
    build_scene(args)


if __name__ == "__main__":
    main()
