#!/usr/bin/env python3
"""Audit a baked Blender cladding mesh without modifying the scene."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def blender_argv(argv: list[str]) -> list[str]:
    if "--" in argv:
        return argv[argv.index("--") + 1 :]
    return []


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--object", default="LCLADDING_RENDER")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--expected-z-plane",
        type=float,
        action="append",
        default=[],
        help="Expected world-Z plane; repeat for each plane",
    )
    parser.add_argument("--z-tolerance", type=float, default=2.1e-3)
    parser.add_argument("--normal-tolerance", type=float, default=1.0e-3)
    return parser.parse_args(argv)


def count_thresholds(values: Any, thresholds: tuple[float, ...]) -> dict[str, int]:
    import numpy as np

    return {
        f">{threshold:g}": int(np.count_nonzero(values > threshold))
        for threshold in thresholds
    }


def top_indices(values: Any, mask: Any, limit: int = 20) -> list[int]:
    import numpy as np

    indices = np.flatnonzero(mask)
    if len(indices) <= limit:
        return indices[np.argsort(values[indices])[::-1]].tolist()
    subset = np.argpartition(values[indices], -limit)[-limit:]
    selected = indices[subset]
    return selected[np.argsort(values[selected])[::-1]].tolist()


def json_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return list(value)
    except TypeError:
        return repr(value)


def material_report(obj: Any) -> list[dict[str, Any]]:
    reports = []
    inputs = (
        "Base Color",
        "Alpha",
        "Roughness",
        "Metallic",
        "IOR",
        "Transmission Weight",
    )
    for slot, material in enumerate(obj.data.materials):
        if material is None:
            reports.append({"slot": slot, "name": None})
            continue
        report: dict[str, Any] = {
            "slot": slot,
            "name": material.name,
            "use_nodes": bool(material.use_nodes),
        }
        for attribute in (
            "surface_render_method",
            "use_transparency_overlap",
            "use_screen_refraction",
        ):
            if hasattr(material, attribute):
                report[attribute] = json_value(getattr(material, attribute))
        if material.use_nodes and material.node_tree is not None:
            bsdf = material.node_tree.nodes.get("Principled BSDF")
            if bsdf is not None:
                report["principled"] = {
                    name: json_value(bsdf.inputs[name].default_value)
                    for name in inputs
                    if name in bsdf.inputs
                }
        reports.append(report)
    return reports


def audit(args: argparse.Namespace) -> dict[str, Any]:
    import bpy
    import numpy as np

    obj = bpy.data.objects.get(args.object)
    if obj is None or obj.type != "MESH":
        raise RuntimeError(f"Mesh object not found: {args.object}")
    mesh = obj.data
    mesh.update(calc_edges=True)

    vertex_count = len(mesh.vertices)
    edge_count = len(mesh.edges)
    polygon_count = len(mesh.polygons)
    loop_count = len(mesh.loops)
    if min(vertex_count, edge_count, polygon_count, loop_count) <= 0:
        raise RuntimeError(f"Mesh is empty: {obj.name}")

    coordinates = np.empty(vertex_count * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", coordinates)
    coordinates = coordinates.reshape((-1, 3))
    matrix = np.asarray([list(row) for row in obj.matrix_world], dtype=np.float64)
    homogeneous = np.ones((vertex_count, 4), dtype=np.float64)
    homogeneous[:, :3] = coordinates
    world = (homogeneous @ matrix.T)[:, :3]
    del homogeneous, coordinates

    loop_vertices = np.empty(loop_count, dtype=np.int32)
    loop_edges = np.empty(loop_count, dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_vertices)
    mesh.loops.foreach_get("edge_index", loop_edges)
    edge_use = np.bincount(loop_edges, minlength=edge_count)

    edge_vertices = np.empty(edge_count * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", edge_vertices)
    edge_vertices = edge_vertices.reshape((-1, 2))
    edge_delta = world[edge_vertices[:, 1]] - world[edge_vertices[:, 0]]
    edge_lengths = np.linalg.norm(edge_delta, axis=1)
    del edge_delta

    loop_starts = np.empty(polygon_count, dtype=np.int64)
    loop_totals = np.empty(polygon_count, dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", loop_starts)
    mesh.polygons.foreach_get("loop_total", loop_totals)
    if np.any(loop_totals < 3):
        print("WARNING: polygons with fewer than three loops detected", flush=True)

    loop_world = world[loop_vertices]
    minimum = np.column_stack(
        tuple(np.minimum.reduceat(loop_world[:, axis], loop_starts) for axis in range(3))
    )
    maximum = np.column_stack(
        tuple(np.maximum.reduceat(loop_world[:, axis], loop_starts) for axis in range(3))
    )
    spans = maximum - minimum
    xy_spans = np.hypot(spans[:, 0], spans[:, 1])
    z_spans = spans[:, 2]
    del loop_world

    areas = np.empty(polygon_count, dtype=np.float64)
    local_normals = np.empty(polygon_count * 3, dtype=np.float64)
    mesh.polygons.foreach_get("area", areas)
    mesh.polygons.foreach_get("normal", local_normals)
    local_normals = local_normals.reshape((-1, 3))
    normal_matrix = np.linalg.inv(matrix[:3, :3]).T
    normals = local_normals @ normal_matrix.T
    normal_lengths = np.linalg.norm(normals, axis=1)
    valid_normal = normal_lengths > 0
    normals[valid_normal] /= normal_lengths[valid_normal, None]
    abs_normal_z = np.abs(normals[:, 2])
    del local_normals, normal_lengths

    z_tolerance = args.z_tolerance
    normal_tolerance = args.normal_tolerance
    planar = z_spans <= z_tolerance
    cross_layer = ~planar
    horizontal = abs_normal_z >= 1.0 - normal_tolerance
    vertical = abs_normal_z <= normal_tolerance
    tilted = ~(horizontal | vertical)
    planar_not_horizontal = planar & ~horizontal
    cross_layer_not_vertical = cross_layer & ~vertical
    geometry_violation = planar_not_horizontal | cross_layer_not_vertical

    planes = np.asarray(args.expected_z_plane, dtype=np.float64)
    plane_report: dict[str, Any] = {"expected": planes.tolist()}
    if len(planes):
        plane_error = np.min(np.abs(world[:, 2, None] - planes[None, :]), axis=1)
        plane_report.update(
            {
                "maximum_vertex_error": float(plane_error.max()),
                "off_plane_counts": count_thresholds(
                    plane_error, (1.0e-6, 1.0e-4, z_tolerance, 1.0e-2)
                ),
            }
        )

    def face_record(index: int) -> dict[str, Any]:
        return {
            "polygon": int(index),
            "vertices": int(loop_totals[index]),
            "area_local": float(areas[index]),
            "normal_world": normals[index].tolist(),
            "minimum_world": minimum[index].tolist(),
            "maximum_world": maximum[index].tolist(),
            "xy_span": float(xy_spans[index]),
            "z_span": float(z_spans[index]),
        }

    violation_score = xy_spans * np.maximum(z_spans, z_tolerance) * (
        1.0 + abs_normal_z
    )
    violation_indices = top_indices(violation_score, geometry_violation)
    giant_indices = top_indices(xy_spans, np.ones(polygon_count, dtype=bool))

    long_edge_indices = top_indices(
        edge_lengths, np.ones(edge_count, dtype=bool), limit=20
    )
    long_edges = [
        {
            "edge": int(index),
            "vertices": edge_vertices[index].tolist(),
            "length": float(edge_lengths[index]),
            "first_world": world[edge_vertices[index, 0]].tolist(),
            "second_world": world[edge_vertices[index, 1]].tolist(),
        }
        for index in long_edge_indices
    ]

    print("Calculating Blender loop-triangle tessellation", flush=True)
    mesh.calc_loop_triangles()
    triangle_count = len(mesh.loop_triangles)
    triangle_vertices = np.empty(triangle_count * 3, dtype=np.int32)
    triangle_polygons = np.empty(triangle_count, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", triangle_vertices)
    mesh.loop_triangles.foreach_get("polygon_index", triangle_polygons)
    triangle_vertices = triangle_vertices.reshape((-1, 3))
    triangle_spans = np.empty(triangle_count, dtype=np.float32)
    triangle_areas = np.empty(triangle_count, dtype=np.float32)
    triangle_alignment = np.empty(triangle_count, dtype=np.float32)
    tessellated_area = np.zeros(polygon_count, dtype=np.float64)
    maximum_triangle_span = np.zeros(polygon_count, dtype=np.float32)
    chunk_size = 500_000
    for start in range(0, triangle_count, chunk_size):
        stop = min(start + chunk_size, triangle_count)
        vertices = triangle_vertices[start:stop]
        first = world[vertices[:, 0]]
        second = world[vertices[:, 1]]
        third = world[vertices[:, 2]]
        cross = np.cross(second - first, third - first)
        area = np.linalg.norm(cross, axis=1) * 0.5
        span = np.maximum.reduce(
            (
                np.linalg.norm(second - first, axis=1),
                np.linalg.norm(third - second, axis=1),
                np.linalg.norm(first - third, axis=1),
            )
        )
        polygon_indices = triangle_polygons[start:stop]
        unit_cross = np.zeros_like(cross)
        nonzero = area > 0
        unit_cross[nonzero] = cross[nonzero] / (2.0 * area[nonzero, None])
        alignment = np.einsum(
            "ij,ij->i", unit_cross, normals[polygon_indices]
        )
        triangle_areas[start:stop] = area
        triangle_spans[start:stop] = span
        triangle_alignment[start:stop] = alignment
        np.add.at(tessellated_area, polygon_indices, area)
        np.maximum.at(maximum_triangle_span, polygon_indices, span)
        print(
            f"Tessellation audit: {stop}/{triangle_count} triangles",
            flush=True,
        )

    valid_area = areas > 1.0e-12
    relative_area_error = np.zeros(polygon_count, dtype=np.float64)
    relative_area_error[valid_area] = np.abs(
        tessellated_area[valid_area] - areas[valid_area]
    ) / areas[valid_area]
    tessellation_parent_indices = top_indices(
        maximum_triangle_span,
        np.ones(polygon_count, dtype=bool),
    )
    area_mismatch_parent_indices = top_indices(
        relative_area_error,
        valid_area,
    )
    longest_triangle_indices = top_indices(
        triangle_spans,
        np.ones(triangle_count, dtype=bool),
    )

    def tessellation_parent_record(index: int) -> dict[str, Any]:
        record = face_record(index)
        record.update(
            {
                "tessellated_area": float(tessellated_area[index]),
                "relative_area_error": float(relative_area_error[index]),
                "maximum_triangle_span": float(maximum_triangle_span[index]),
            }
        )
        return record

    longest_triangles = []
    for index in longest_triangle_indices:
        vertices = triangle_vertices[index]
        longest_triangles.append(
            {
                "triangle": int(index),
                "polygon": int(triangle_polygons[index]),
                "vertices": vertices.tolist(),
                "coordinates_world": world[vertices].tolist(),
                "span": float(triangle_spans[index]),
                "area": float(triangle_areas[index]),
                "normal_alignment_with_polygon": float(
                    triangle_alignment[index]
                ),
            }
        )

    custom_properties = {
        key: json_value(obj[key]) for key in obj.keys() if key != "_RNA_UI"
    }
    report = {
        "blend": bpy.data.filepath,
        "object": obj.name,
        "mesh": mesh.name,
        "custom_properties": custom_properties,
        "matrix_world": matrix.tolist(),
        "counts": {
            "vertices": vertex_count,
            "edges": edge_count,
            "polygons": polygon_count,
            "loops": loop_count,
        },
        "world_bounds": {
            "minimum": world.min(axis=0).tolist(),
            "maximum": world.max(axis=0).tolist(),
        },
        "topology": {
            "edge_use_0": int(np.count_nonzero(edge_use == 0)),
            "edge_use_1_boundary": int(np.count_nonzero(edge_use == 1)),
            "edge_use_2_manifold": int(np.count_nonzero(edge_use == 2)),
            "edge_use_over_2": int(np.count_nonzero(edge_use > 2)),
            "degenerate_polygons": int(np.count_nonzero(loop_totals < 3)),
            "zero_area_polygons": int(np.count_nonzero(areas <= 1.0e-12)),
        },
        "face_geometry": {
            "planar": int(np.count_nonzero(planar)),
            "cross_layer": int(np.count_nonzero(cross_layer)),
            "horizontal_normal": int(np.count_nonzero(horizontal)),
            "vertical_normal": int(np.count_nonzero(vertical)),
            "tilted_normal": int(np.count_nonzero(tilted)),
            "planar_not_horizontal": int(np.count_nonzero(planar_not_horizontal)),
            "cross_layer_not_vertical": int(
                np.count_nonzero(cross_layer_not_vertical)
            ),
            "xy_span_counts": count_thresholds(
                xy_spans, (1.0, 5.0, 10.0, 50.0, 100.0, 500.0)
            ),
            "cross_layer_xy_span_counts": count_thresholds(
                xy_spans[cross_layer], (1.0, 5.0, 10.0, 50.0, 100.0, 500.0)
            ),
            "tilted_abs_normal_z_counts": count_thresholds(
                abs_normal_z, (1.0e-3, 1.0e-2, 0.1, 0.5)
            ),
            "maximum_xy_span": float(xy_spans.max()),
            "maximum_z_span": float(z_spans.max()),
            "maximum_area_local": float(areas.max()),
        },
        "edge_geometry": {
            "length_counts": count_thresholds(
                edge_lengths, (1.0, 5.0, 10.0, 50.0, 100.0, 500.0)
            ),
            "maximum_length": float(edge_lengths.max()),
        },
        "tessellation": {
            "triangles": triangle_count,
            "zero_area_triangles": int(
                np.count_nonzero(triangle_areas <= 1.0e-12)
            ),
            "triangles_opposite_parent_normal": int(
                np.count_nonzero(triangle_alignment < -1.0e-5)
            ),
            "triangle_span_counts": count_thresholds(
                triangle_spans, (1.0, 5.0, 10.0, 50.0, 100.0, 500.0)
            ),
            "maximum_triangle_span": float(triangle_spans.max()),
            "parent_relative_area_error_counts": count_thresholds(
                relative_area_error, (1.0e-6, 1.0e-4, 1.0e-2, 0.1)
            ),
        },
        "z_planes": plane_report,
        "materials": material_report(obj),
        "largest_faces": [face_record(index) for index in giant_indices],
        "largest_geometry_violations": [
            face_record(index) for index in violation_indices
        ],
        "longest_edges": long_edges,
        "largest_tessellated_parents": [
            tessellation_parent_record(index)
            for index in tessellation_parent_indices
        ],
        "largest_tessellation_area_mismatches": [
            tessellation_parent_record(index)
            for index in area_mismatch_parent_indices
        ],
        "longest_triangles": longest_triangles,
    }
    return report


def main() -> None:
    args = parse_args(blender_argv(sys.argv))
    report = audit(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "counts": report["counts"],
        "topology": report["topology"],
        "face_geometry": report["face_geometry"],
        "tessellation": report["tessellation"],
        "z_planes": report["z_planes"],
    }, indent=2), flush=True)
    print(f"Audit report written: {args.output}", flush=True)


if __name__ == "__main__":
    main()
