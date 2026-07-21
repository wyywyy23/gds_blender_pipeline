#!/usr/bin/env python3
"""Generate a validated sidecar of complete TUAM cladding-cutter components.

The visual GDS fractures large rounded TUAM polygons for GDS compatibility.
This generator merges that layer back into logical openings and triangulates
each opening independently. Blender then extrudes the complete openings into
one compound cutter and performs one undercut Boolean, instead of applying
touching GDS fragments across several sequential Boolean passes.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterable

from kfactory import kdb
import numpy as np
import shapely
from shapely.geometry import Polygon


FORMAT_VERSION = 1
DEFAULT_UNDERCUT_LAYER = (2050, 0)


def parse_layer(value: str) -> tuple[int, int]:
    parts = value.replace("/", ",").split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("expected LAYER,DATATYPE")
    return int(parts[0]), int(parts[1])


def points_xy(points: Iterable[Any]) -> list[tuple[int, int]]:
    coordinates = [(int(point.x), int(point.y)) for point in points]
    if len(coordinates) > 1 and coordinates[0] == coordinates[-1]:
        coordinates.pop()
    return coordinates


def signed_area2(coordinates: list[tuple[int, int]]) -> int:
    return sum(
        x0 * y1 - x1 * y0
        for (x0, y0), (x1, y1) in zip(
            coordinates, coordinates[1:] + coordinates[:1]
        )
    )


def oriented_loop(
    coordinates: list[tuple[int, int]], *, counterclockwise: bool
) -> list[tuple[int, int]]:
    if len(coordinates) < 3:
        raise ValueError("Cutter boundary loop has fewer than three points")
    area2 = signed_area2(coordinates)
    if area2 == 0:
        raise ValueError("Cutter boundary loop has zero signed area")
    if (area2 > 0) != counterclockwise:
        coordinates.reverse()
    return coordinates


def polygon_area2(polygon: Any) -> int:
    hull_area2 = abs(signed_area2(points_xy(polygon.each_point_hull())))
    hole_area2 = sum(
        abs(signed_area2(points_xy(polygon.each_point_hole(index))))
        for index in range(polygon.holes())
    )
    return hull_area2 - hole_area2


def polygon_to_shapely(polygon: Any) -> Polygon:
    hull = points_xy(polygon.each_point_hull())
    holes = [
        points_xy(polygon.each_point_hole(index))
        for index in range(polygon.holes())
    ]
    geometry = Polygon(hull, holes)
    if geometry.is_empty or geometry.area <= 0:
        raise ValueError("KLayout cutter polygon converted to an empty polygon")
    if not geometry.is_valid:
        raise ValueError("KLayout cutter polygon converted to an invalid polygon")
    return geometry


def triangle_coordinates(geometry: Polygon) -> np.ndarray:
    triangulated = shapely.constrained_delaunay_triangles(geometry)
    parts = shapely.get_parts(triangulated)
    coordinates = shapely.get_coordinates(parts)
    if len(parts) == 0 or coordinates.shape != (len(parts) * 4, 2):
        raise ValueError(
            "Constrained cutter triangulation returned invalid parts: "
            f"parts={len(parts)}, coordinates={coordinates.shape}"
        )

    coordinates = coordinates.reshape((-1, 4, 2))
    if not np.allclose(coordinates[:, 0], coordinates[:, 3], atol=0.0, rtol=0.0):
        raise ValueError("Cutter triangle rings are not closed")
    rounded = np.rint(coordinates[:, :3]).astype(np.int64)
    error = float(np.max(np.abs(coordinates[:, :3] - rounded)))
    if error > 1.0e-6:
        raise ValueError(
            "Cutter triangulation introduced non-database-unit coordinates: "
            f"maximum error={error}"
        )

    first = rounded[:, 0]
    second = rounded[:, 1]
    third = rounded[:, 2]
    area2 = (
        (second[:, 0] - first[:, 0]) * (third[:, 1] - first[:, 1])
        - (second[:, 1] - first[:, 1]) * (third[:, 0] - first[:, 0])
    )
    if np.any(area2 == 0):
        raise ValueError("Cutter triangulation produced a zero-area triangle")
    clockwise = area2 < 0
    if np.any(clockwise):
        rounded[clockwise, 1], rounded[clockwise, 2] = (
            rounded[clockwise, 2].copy(),
            rounded[clockwise, 1].copy(),
        )
    return rounded


def add_triangle_chunk(
    triangle_xy: np.ndarray,
    *,
    vertex_index: dict[tuple[int, int], int],
    vertices: list[tuple[int, int]],
) -> np.ndarray:
    flat = triangle_xy.reshape((-1, 2))
    unique, inverse = np.unique(flat, axis=0, return_inverse=True)
    local_to_global = np.empty(len(unique), dtype=np.int64)
    for local_index, coordinate in enumerate(unique):
        key = int(coordinate[0]), int(coordinate[1])
        global_index = vertex_index.get(key)
        if global_index is None:
            global_index = len(vertices)
            vertex_index[key] = global_index
            vertices.append(key)
        local_to_global[local_index] = global_index
    return local_to_global[inverse].reshape((-1, 3))


def validate_planar_topology(
    *,
    triangles: np.ndarray,
    boundary_edges: np.ndarray,
    vertex_count: int,
) -> None:
    triangle_edges = np.concatenate(
        (
            triangles[:, (0, 1)],
            triangles[:, (1, 2)],
            triangles[:, (2, 0)],
        ),
        axis=0,
    ).astype(np.int64, copy=False)
    triangle_edges.sort(axis=1)
    triangle_keys = triangle_edges[:, 0] * vertex_count + triangle_edges[:, 1]
    unique_keys, counts = np.unique(triangle_keys, return_counts=True)
    if np.any(counts > 2):
        raise ValueError(
            "Cutter triangulation has non-manifold planar edges: "
            f"{int(np.count_nonzero(counts > 2))}"
        )

    expected = np.sort(boundary_edges.astype(np.int64, copy=False), axis=1)
    expected_keys = np.unique(expected[:, 0] * vertex_count + expected[:, 1])
    actual_keys = unique_keys[counts == 1]
    if not np.array_equal(actual_keys, expected_keys):
        missing = np.setdiff1d(expected_keys, actual_keys, assume_unique=True)
        extra = np.setdiff1d(actual_keys, expected_keys, assume_unique=True)
        raise ValueError(
            "Cutter triangulation boundary mismatch: "
            f"missing={len(missing)}, extra={len(extra)}"
        )


def generate_unfractured_cutter(
    *, input_gds: Path, output: Path, undercut_layer: tuple[int, int]
) -> None:
    layout = kdb.Layout()
    layout.read(str(input_gds))
    top_cells = layout.top_cells()
    if len(top_cells) != 1:
        names = ", ".join(cell.name for cell in top_cells)
        raise ValueError(f"Expected one top cell, found {len(top_cells)}: {names}")
    top = top_cells[0]
    dbu_um = float(layout.dbu)

    layer_index = layout.layer(*undercut_layer)
    region = kdb.Region(top.begin_shapes_rec(layer_index))
    imported_polygon_count = int(region.count())
    region.merge()
    if region.is_empty():
        raise ValueError(f"Undercut layer {undercut_layer} is empty")

    vertex_index: dict[tuple[int, int], int] = {}
    vertices: list[tuple[int, int]] = []
    triangle_chunks: list[np.ndarray] = []
    boundary_chunks: list[np.ndarray] = []
    component_offsets = [0]
    expected_area2 = 0
    triangle_area2 = 0
    component_count = 0
    boundary_loop_count = 0

    print(f"Input GDS: {input_gds}", flush=True)
    print(f"Top cell: {top.name}", flush=True)
    print(f"Database unit: {dbu_um:g} um", flush=True)
    print(f"Undercut layer: {undercut_layer}", flush=True)
    print(f"Imported fractured polygons: {imported_polygon_count}", flush=True)

    for polygon in region.each_merged():
        component_count += 1
        geometry = polygon_to_shapely(polygon)
        triangle_xy = triangle_coordinates(geometry)
        first = triangle_xy[:, 0]
        second = triangle_xy[:, 1]
        third = triangle_xy[:, 2]
        component_triangle_area2 = int(
            (
                (second[:, 0] - first[:, 0])
                * (third[:, 1] - first[:, 1])
                - (second[:, 1] - first[:, 1])
                * (third[:, 0] - first[:, 0])
            ).sum()
        )
        component_expected_area2 = polygon_area2(polygon)
        if component_triangle_area2 != component_expected_area2:
            raise ValueError(
                f"Cutter component {component_count} area mismatch: "
                f"triangles={component_triangle_area2}, "
                f"expected={component_expected_area2}"
            )
        expected_area2 += component_expected_area2
        triangle_area2 += component_triangle_area2

        triangles = add_triangle_chunk(
            triangle_xy,
            vertex_index=vertex_index,
            vertices=vertices,
        ).astype(np.int32)
        triangle_chunks.append(triangles)

        loops = [
            oriented_loop(
                points_xy(polygon.each_point_hull()), counterclockwise=True
            )
        ]
        loops.extend(
            oriented_loop(
                points_xy(polygon.each_point_hole(index)),
                counterclockwise=False,
            )
            for index in range(polygon.holes())
        )
        for loop in loops:
            boundary_loop_count += 1
            indices = [vertex_index[coordinate] for coordinate in loop]
            starts = np.asarray(indices, dtype=np.int32)
            boundary_chunks.append(np.column_stack((starts, np.roll(starts, -1))))
        component_offsets.append(component_offsets[-1] + len(triangles))

        if component_count % 512 == 0:
            print(
                f"Reconstructed {component_count} logical openings: "
                f"vertices={len(vertices)}, "
                f"triangles={sum(len(chunk) for chunk in triangle_chunks)}",
                flush=True,
            )

    if triangle_area2 != expected_area2:
        raise ValueError(
            f"Total cutter area mismatch: {triangle_area2} != {expected_area2}"
        )
    triangles = np.concatenate(triangle_chunks, axis=0)
    boundary_edges = np.concatenate(boundary_chunks, axis=0)
    validate_planar_topology(
        triangles=triangles,
        boundary_edges=boundary_edges,
        vertex_count=len(vertices),
    )

    xy_dbu = np.asarray(vertices, dtype=np.int64)
    if np.any(xy_dbu < np.iinfo(np.int32).min) or np.any(
        xy_dbu > np.iinfo(np.int32).max
    ):
        raise ValueError("Cutter coordinates exceed int32 database units")
    xy_dbu = xy_dbu.astype(np.int32)

    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output,
        format_version=np.asarray([FORMAT_VERSION], dtype=np.int32),
        xy_dbu=xy_dbu,
        triangles=triangles,
        boundary_edges=boundary_edges,
        component_triangle_offsets=np.asarray(component_offsets, dtype=np.int32),
        dbu_um=np.asarray([dbu_um], dtype=np.float64),
        area2_dbu2=np.asarray([expected_area2], dtype=np.int64),
        undercut_layer=np.asarray(undercut_layer, dtype=np.int32),
        imported_fractured_polygon_count=np.asarray(
            [imported_polygon_count], dtype=np.int32
        ),
        logical_component_count=np.asarray([component_count], dtype=np.int32),
        boundary_loop_count=np.asarray([boundary_loop_count], dtype=np.int32),
        validated_closed_planar_topology=np.asarray([1], dtype=np.uint8),
    )
    print(
        f"Unfractured cutter sidecar written: {output} "
        f"({output.stat().st_size} bytes)",
        flush=True,
    )
    print(
        "Validated logical cutter topology: "
        f"fractured_polygons={imported_polygon_count}, "
        f"logical_components={component_count}, vertices={len(xy_dbu)}, "
        f"triangles={len(triangles)}, boundary_edges={len(boundary_edges)}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gds", type=Path, required=True, help="AIM visual GDS")
    parser.add_argument("--output", type=Path, required=True, help="Output .npz sidecar")
    parser.add_argument(
        "--undercut-layer",
        type=parse_layer,
        default=DEFAULT_UNDERCUT_LAYER,
        help="Undercut GDS layer/datatype (default: 2050,0)",
    )
    args = parser.parse_args()
    generate_unfractured_cutter(
        input_gds=args.gds.resolve(),
        output=args.output.resolve(),
        undercut_layer=args.undercut_layer,
    )


if __name__ == "__main__":
    main()
