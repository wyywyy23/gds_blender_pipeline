#!/usr/bin/env python3
"""Generate a validated unfractured planar-mesh sidecar for one render layer.

The input visual GDS may contain 256-vertex fracture cuts. This generator
merges the selected layer back into logical components, triangulates each
component, and records only true exterior boundary edges. Blender can then
extrude one watertight compound mesh and bevel its top/bottom exterior rims
without treating fracture seams or planar triangulation edges as real rims.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from kfactory import kdb
import numpy as np
import yaml

try:
    from aim_generate_unfractured_cladding_cutter import (
        add_triangle_chunk,
        oriented_loop,
        points_xy,
        polygon_area2,
        polygon_to_shapely,
        triangle_coordinates,
        validate_planar_topology,
    )
except ModuleNotFoundError:
    from scripts.aim_generate_unfractured_cladding_cutter import (
        add_triangle_chunk,
        oriented_loop,
        points_xy,
        polygon_area2,
        polygon_to_shapely,
        triangle_coordinates,
        validate_planar_topology,
    )


FORMAT_VERSION = 1


def stack_layer_pair(stack_config: Path, layer_name: str) -> tuple[int, int]:
    data = yaml.safe_load(stack_config.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{stack_config} must contain a layer mapping")
    spec = data.get(layer_name)
    if not isinstance(spec, dict):
        raise ValueError(f"Layer is missing from stack config: {layer_name}")
    try:
        return int(spec["index"]), int(spec["type"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Layer {layer_name} requires integer index and type values"
        ) from exc


def generate_unfractured_layer_mesh(
    *,
    input_gds: Path,
    stack_config: Path,
    layer_name: str,
    output: Path,
) -> None:
    gds_layer = stack_layer_pair(stack_config, layer_name)
    layout = kdb.Layout()
    layout.read(str(input_gds))
    top_cells = layout.top_cells()
    if len(top_cells) != 1:
        names = ", ".join(cell.name for cell in top_cells)
        raise ValueError(f"Expected one top cell, found {len(top_cells)}: {names}")
    top = top_cells[0]
    dbu_um = float(layout.dbu)

    layer_index = layout.layer(*gds_layer)
    region = kdb.Region(top.begin_shapes_rec(layer_index))
    imported_polygon_count = int(region.count())
    region.merge()
    if region.is_empty():
        raise ValueError(f"Render layer {layer_name} {gds_layer} is empty")

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
    print(f"Render layer: {layer_name} {gds_layer}", flush=True)
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
                f"Layer component {component_count} area mismatch: "
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
            boundary_chunks.append(
                np.column_stack((starts, np.roll(starts, -1)))
            )
        component_offsets.append(component_offsets[-1] + len(triangles))

        if component_count % 512 == 0:
            print(
                f"Reconstructed {component_count} logical components: "
                f"vertices={len(vertices)}, "
                f"triangles={sum(len(chunk) for chunk in triangle_chunks)}",
                flush=True,
            )

    if triangle_area2 != expected_area2:
        raise ValueError(
            f"Total layer area mismatch: {triangle_area2} != {expected_area2}"
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
        raise ValueError("Layer coordinates exceed int32 database units")
    xy_dbu = xy_dbu.astype(np.int32)

    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output,
        format_version=np.asarray([FORMAT_VERSION], dtype=np.int32),
        layer_name=np.asarray([layer_name]),
        gds_layer=np.asarray(gds_layer, dtype=np.int32),
        xy_dbu=xy_dbu,
        triangles=triangles,
        boundary_edges=boundary_edges,
        component_triangle_offsets=np.asarray(component_offsets, dtype=np.int32),
        dbu_um=np.asarray([dbu_um], dtype=np.float64),
        area2_dbu2=np.asarray([expected_area2], dtype=np.int64),
        imported_fractured_polygon_count=np.asarray(
            [imported_polygon_count], dtype=np.int32
        ),
        logical_component_count=np.asarray([component_count], dtype=np.int32),
        boundary_loop_count=np.asarray([boundary_loop_count], dtype=np.int32),
        validated_closed_planar_topology=np.asarray([1], dtype=np.uint8),
    )
    print(
        f"Unfractured layer sidecar written: {output} "
        f"({output.stat().st_size} bytes)",
        flush=True,
    )
    print(
        "Validated logical layer topology: "
        f"fractured_polygons={imported_polygon_count}, "
        f"logical_components={component_count}, vertices={len(xy_dbu)}, "
        f"triangles={len(triangles)}, boundary_edges={len(boundary_edges)}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gds", type=Path, required=True, help="AIM visual GDS")
    parser.add_argument(
        "--stack-config",
        type=Path,
        required=True,
        help="BlenderGDS stack config containing the selected layer",
    )
    parser.add_argument(
        "--layer-name",
        required=True,
        help="Render layer name, for example M2AM_RENDER",
    )
    parser.add_argument("--output", type=Path, required=True, help="Output .npz")
    args = parser.parse_args()
    generate_unfractured_layer_mesh(
        input_gds=args.gds.resolve(),
        stack_config=args.stack_config.resolve(),
        layer_name=args.layer_name,
        output=args.output.resolve(),
    )


if __name__ == "__main__":
    main()
