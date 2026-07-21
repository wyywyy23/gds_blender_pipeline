#!/usr/bin/env python3
"""Generate a watertight, explicitly triangulated AIM cladding mesh sidecar.

The final mesh is constructed from two 2D solid domains instead of a Blender
Boolean: lower cladding is CLADDING minus TUAM, and upper passivation is lower
cladding minus PAAM. Horizontal surfaces are triangulated in bounded tiles;
tile borders become shared coplanar edges, never internal vertical faces.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterable

from kfactory import kdb
import numpy as np

from aim_generate_unfractured_cladding_cutter import (
    oriented_loop,
    points_xy,
    polygon_area2,
    signed_area2,
    triangle_coordinates,
)


FORMAT_VERSION = 1
DEFAULT_CLADDING_LAYER = (2010, 0)
DEFAULT_UNDERCUT_LAYER = (2050, 0)
DEFAULT_PASSIVATION_LAYER = (2051, 0)
DEFAULT_Z_PLANES_UM = (-2.001, 5.479, 5.980)
MAXIMUM_SLIVER_AREA2_DBU2 = 10_000


def parse_layer(value: str) -> tuple[int, int]:
    parts = value.replace("/", ",").split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("expected LAYER,DATATYPE")
    return int(parts[0]), int(parts[1])


def merged_region(layout: Any, top: Any, layer: tuple[int, int]) -> Any:
    region = kdb.Region(top.begin_shapes_rec(layout.layer(*layer)))
    region.merge()
    if region.is_empty():
        raise ValueError(f"Required GDS layer is empty: {layer}")
    return region


class MeshBuilder:
    def __init__(self) -> None:
        self.vertex_index: dict[tuple[int, int, int], int] = {}
        self.xy_dbu: list[tuple[int, int]] = []
        self.vertex_planes: list[int] = []
        self.face_chunks: list[np.ndarray] = []
        self.horizontal_face_count = 0

    def add_vertex(self, coordinate: tuple[int, int], plane: int) -> int:
        key = coordinate[0], coordinate[1], plane
        index = self.vertex_index.get(key)
        if index is None:
            index = len(self.xy_dbu)
            self.vertex_index[key] = index
            self.xy_dbu.append(coordinate)
            self.vertex_planes.append(plane)
        return index

    def add_planar_triangles(
        self, triangle_xy: np.ndarray, *, plane: int, reverse: bool
    ) -> None:
        flat = triangle_xy.reshape((-1, 2))
        unique, inverse = np.unique(flat, axis=0, return_inverse=True)
        local_to_global = np.empty(len(unique), dtype=np.int32)
        for local_index, coordinate in enumerate(unique):
            point = int(coordinate[0]), int(coordinate[1])
            local_to_global[local_index] = self.add_vertex(point, plane)
        faces = local_to_global[inverse].reshape((-1, 3))
        if reverse:
            faces = faces[:, (0, 2, 1)]
        self.face_chunks.append(faces.astype(np.int32, copy=False))
        self.horizontal_face_count += len(faces)

    def add_wall_loop(
        self,
        loop: list[tuple[int, int]],
        *,
        lower_plane: int,
        upper_plane: int,
    ) -> None:
        lower = np.asarray(
            [self.add_vertex(point, lower_plane) for point in loop],
            dtype=np.int32,
        )
        upper = np.asarray(
            [self.add_vertex(point, upper_plane) for point in loop],
            dtype=np.int32,
        )
        next_lower = np.roll(lower, -1)
        next_upper = np.roll(upper, -1)
        faces = np.empty((len(loop) * 2, 3), dtype=np.int32)
        faces[0::2] = np.column_stack((lower, next_lower, next_upper))
        faces[1::2] = np.column_stack((lower, next_upper, upper))
        self.face_chunks.append(faces)

    def add_wall_edges(
        self,
        edges: list[tuple[tuple[int, int], tuple[int, int]]],
        *,
        lower_plane: int,
        upper_plane: int,
    ) -> None:
        lower = np.asarray(
            [self.add_vertex(first, lower_plane) for first, _ in edges],
            dtype=np.int32,
        )
        next_lower = np.asarray(
            [self.add_vertex(second, lower_plane) for _, second in edges],
            dtype=np.int32,
        )
        upper = np.asarray(
            [self.add_vertex(first, upper_plane) for first, _ in edges],
            dtype=np.int32,
        )
        next_upper = np.asarray(
            [self.add_vertex(second, upper_plane) for _, second in edges],
            dtype=np.int32,
        )
        faces = np.empty((len(edges) * 2, 3), dtype=np.int32)
        faces[0::2] = np.column_stack((lower, next_lower, next_upper))
        faces[1::2] = np.column_stack((lower, next_upper, upper))
        self.face_chunks.append(faces)

    def arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        xy_dbu = np.asarray(self.xy_dbu, dtype=np.int64)
        if np.any(xy_dbu < np.iinfo(np.int32).min) or np.any(
            xy_dbu > np.iinfo(np.int32).max
        ):
            raise ValueError("Cladding coordinates exceed int32 database units")
        return (
            xy_dbu.astype(np.int32),
            np.asarray(self.vertex_planes, dtype=np.uint8),
            np.concatenate(self.face_chunks, axis=0),
        )


def polygon_loops(polygon: Any) -> Iterable[list[tuple[int, int]]]:
    yield oriented_loop(points_xy(polygon.each_point_hull()), counterclockwise=True)
    for index in range(polygon.holes()):
        yield oriented_loop(
            points_xy(polygon.each_point_hole(index)), counterclockwise=False
        )


def add_tiled_plane(
    builder: MeshBuilder,
    *,
    region: Any,
    plane: int,
    reverse: bool,
    tile_dbu: int,
    grid_bbox: Any,
    label: str,
) -> tuple[int, int, int, list[tuple[tuple[int, int], tuple[int, int]]]]:
    import shapely
    from shapely.geometry import Polygon

    expected_area2 = int(region.area()) * 2
    triangulated_area2 = 0
    tile_count = 0
    polygon_count = 0
    clipped_polygons: list[tuple[list[list[tuple[int, int]]], int]] = []
    boundary_edges: dict[
        tuple[tuple[int, int], tuple[int, int]],
        tuple[tuple[int, int], tuple[int, int]],
    ] = {}
    x_values = range(grid_bbox.left, grid_bbox.right, tile_dbu)
    y_values = range(grid_bbox.bottom, grid_bbox.top, tile_dbu)
    grid_x = set(range(grid_bbox.left, grid_bbox.right + 1, tile_dbu))
    grid_y = set(range(grid_bbox.bottom, grid_bbox.top + 1, tile_dbu))
    grid_x.add(grid_bbox.right)
    grid_y.add(grid_bbox.top)
    for y0 in y_values:
        y1 = min(y0 + tile_dbu, grid_bbox.top)
        for x0 in x_values:
            x1 = min(x0 + tile_dbu, grid_bbox.right)
            clipped = region & kdb.Region(kdb.Box(x0, y0, x1, y1))
            if clipped.is_empty():
                continue
            clipped.merge()
            tile_count += 1
            for polygon in clipped.each_merged():
                polygon_count += 1
                clipped_polygons.append(
                    (list(polygon_loops(polygon)), polygon_area2(polygon))
                )
        if tile_count and tile_count % 128 == 0:
            print(
                f"{label}: processed {tile_count} occupied tiles, "
                f"{polygon_count} polygons",
                flush=True,
            )

    vertical_points: dict[int, set[int]] = {}
    horizontal_points: dict[int, set[int]] = {}
    for loops, _ in clipped_polygons:
        for loop in loops:
            for x, y in loop:
                if x in grid_x:
                    vertical_points.setdefault(x, set()).add(y)
                if y in grid_y:
                    horizontal_points.setdefault(y, set()).add(x)

    def densify_loop(loop: list[tuple[int, int]]) -> list[tuple[int, int]]:
        dense: list[tuple[int, int]] = []
        for first_point, second_point in zip(loop, loop[1:] + loop[:1]):
            dense.append(first_point)
            x0, y0 = first_point
            x1, y1 = second_point
            if x0 == x1 and x0 in grid_x:
                values = sorted(
                    value
                    for value in vertical_points.get(x0, ())
                    if min(y0, y1) < value < max(y0, y1)
                )
                if y1 < y0:
                    values.reverse()
                dense.extend((x0, value) for value in values)
            elif y0 == y1 and y0 in grid_y:
                values = sorted(
                    value
                    for value in horizontal_points.get(y0, ())
                    if min(x0, x1) < value < max(x0, x1)
                )
                if x1 < x0:
                    values.reverse()
                dense.extend((value, y0) for value in values)
        return dense

    def polygon_parts(geometry: Any) -> list[Any]:
        if geometry.geom_type == "Polygon":
            return [geometry]
        parts = []
        for part in getattr(geometry, "geoms", ()):
            parts.extend(polygon_parts(part))
        return parts

    def shapely_part_loops(part: Any) -> list[list[tuple[int, int]]]:
        raw_loops = [part.exterior.coords]
        raw_loops.extend(interior.coords for interior in part.interiors)
        loops = []
        for index, coordinates in enumerate(raw_loops):
            array = np.asarray(coordinates, dtype=np.float64)
            rounded = np.rint(array).astype(np.int64)
            if float(np.max(np.abs(array - rounded))) > 1.0e-6:
                raise ValueError(f"{label} repair introduced non-DBU coordinates")
            points = [(int(x), int(y)) for x, y in rounded]
            if points[0] == points[-1]:
                points.pop()
            loops.append(
                oriented_loop(points, counterclockwise=index == 0)
            )
        return loops

    for loops, expected_polygon_area2 in clipped_polygons:
        loops = [densify_loop(loop) for loop in loops]
        geometry = Polygon(loops[0], loops[1:])
        if geometry.is_empty:
            raise ValueError(f"{label} densified tile polygon is empty")
        parts = [geometry]
        if not geometry.is_valid:
            parts = polygon_parts(shapely.make_valid(geometry))
            if not parts:
                raise ValueError(f"{label} tile repair produced no polygons")
        polygon_triangle_area2 = 0
        for part in parts:
            part_loops = shapely_part_loops(part)
            for loop in part_loops:
                for first_point, second_point in zip(
                    loop, loop[1:] + loop[:1]
                ):
                    key = tuple(sorted((first_point, second_point)))
                    previous = boundary_edges.get(key)
                    if previous is None:
                        boundary_edges[key] = (first_point, second_point)
                    elif previous == (second_point, first_point):
                        del boundary_edges[key]
                    else:
                        raise ValueError(
                            f"{label} has duplicate same-direction tile edge"
                        )
            triangle_xy = triangle_coordinates(part)
            first = triangle_xy[:, 0]
            second = triangle_xy[:, 1]
            third = triangle_xy[:, 2]
            area2 = int(
                (
                    (second[:, 0] - first[:, 0])
                    * (third[:, 1] - first[:, 1])
                    - (second[:, 1] - first[:, 1])
                    * (third[:, 0] - first[:, 0])
                ).sum()
            )
            polygon_triangle_area2 += area2
            builder.add_planar_triangles(
                triangle_xy,
                plane=plane,
                reverse=reverse,
            )
        polygon_area_error = polygon_triangle_area2 - expected_polygon_area2
        if polygon_area_error:
            raise ValueError(
                f"{label} tile polygon area mismatch: "
                f"triangles={polygon_triangle_area2}, "
                f"expected={expected_polygon_area2}"
            )
        triangulated_area2 += polygon_triangle_area2
    area_error = triangulated_area2 - expected_area2
    relative_error = abs(area_error) / max(1, expected_area2)
    if relative_error > 1.0e-7:
        raise ValueError(
            f"{label} tiled area mismatch: "
            f"triangles={triangulated_area2}, expected={expected_area2}, "
            f"relative_error={relative_error:g}"
        )
    print(
        f"{label}: validated {tile_count} occupied tiles, "
        f"{polygon_count} polygons, area2={triangulated_area2}, "
        f"area_error={area_error}, physical_edges={len(boundary_edges)}",
        flush=True,
    )
    return tile_count, expected_area2, triangulated_area2, list(boundary_edges.values())


def add_tiled_boundary_walls(
    builder: MeshBuilder,
    *,
    edges: list[tuple[tuple[int, int], tuple[int, int]]],
    lower_plane: int,
    upper_plane: int,
    label: str,
) -> int:
    builder.add_wall_edges(
        edges,
        lower_plane=lower_plane,
        upper_plane=upper_plane,
    )
    print(
        f"{label}: added {len(edges)} tiled physical boundary wall segments",
        flush=True,
    )
    return len(edges)


def validate_closed_triangle_mesh(
    *,
    faces: np.ndarray,
    vertex_count: int,
    xy_dbu: np.ndarray,
    vertex_planes: np.ndarray,
) -> None:
    edge_count = len(faces) * 3
    encoded = np.empty(edge_count, dtype=np.int64)
    for offset, (first, second) in enumerate(((0, 1), (1, 2), (2, 0))):
        start = offset * len(faces)
        stop = start + len(faces)
        u = faces[:, first].astype(np.int64, copy=False)
        v = faces[:, second].astype(np.int64, copy=False)
        low = np.minimum(u, v)
        high = np.maximum(u, v)
        encoded[start:stop] = (
            (low * vertex_count + high) * 2 + (u < v).astype(np.int64)
        )
    encoded.sort()
    canonical = encoded // 2
    unique_edges, starts, counts = np.unique(
        canonical, return_index=True, return_counts=True
    )
    count_bad = counts != 2
    paired_starts = starts[~count_bad]
    orientation_bad = (
        encoded[paired_starts] ^ encoded[paired_starts + 1]
    ) != 1
    bad_count = int(np.count_nonzero(count_bad))
    bad_orientation = int(np.count_nonzero(orientation_bad))
    if bad_count or bad_orientation:
        sample = []
        for edge_key, count in zip(
            unique_edges[count_bad][:12], counts[count_bad][:12]
        ):
            first_index = int(edge_key // vertex_count)
            second_index = int(edge_key % vertex_count)
            sample.append(
                {
                    "count": int(count),
                    "first": (
                        xy_dbu[first_index].tolist()
                        + [int(vertex_planes[first_index])]
                    ),
                    "second": (
                        xy_dbu[second_index].tolist()
                        + [int(vertex_planes[second_index])]
                    ),
                }
            )
        raise ValueError(
            "Triangle mesh is not consistently closed/manifold: "
            f"{bad_count} edges with incidence != 2, "
            f"{bad_orientation} same-direction pairs; sample={sample}"
        )
    print(
        f"Validated closed oriented triangle topology: "
        f"{vertex_count} vertices, {len(faces)} faces, {len(starts)} edges",
        flush=True,
    )


def remove_tiny_orphan_horizontal_faces(
    *,
    faces: np.ndarray,
    xy_dbu: np.ndarray,
    vertex_planes: np.ndarray,
    maximum_area2_dbu2: int = MAXIMUM_SLIVER_AREA2_DBU2,
) -> tuple[np.ndarray, int]:
    vertex_count = len(xy_dbu)
    canonical_edges = np.empty((len(faces), 3), dtype=np.int64)
    for column, (first, second) in enumerate(((0, 1), (1, 2), (2, 0))):
        u = faces[:, first].astype(np.int64, copy=False)
        v = faces[:, second].astype(np.int64, copy=False)
        canonical_edges[:, column] = (
            np.minimum(u, v) * vertex_count + np.maximum(u, v)
        )
    _, inverse, counts = np.unique(
        canonical_edges.reshape(-1), return_inverse=True, return_counts=True
    )
    incidence = counts[inverse].reshape((-1, 3))
    orphan = np.all(incidence == 1, axis=1)
    planes = vertex_planes[faces]
    horizontal = np.all(planes == planes[:, :1], axis=1)
    points = xy_dbu[faces].astype(np.int64, copy=False)
    area2 = np.abs(
        (points[:, 1, 0] - points[:, 0, 0])
        * (points[:, 2, 1] - points[:, 0, 1])
        - (points[:, 1, 1] - points[:, 0, 1])
        * (points[:, 2, 0] - points[:, 0, 0])
    )
    removable = orphan & horizontal & (area2 <= maximum_area2_dbu2)
    removed = int(np.count_nonzero(removable))
    if removed:
        faces = faces[~removable]
        print(
            f"Removed {removed} isolated sub-resolution horizontal sliver "
            f"triangles (area2 <= {maximum_area2_dbu2} DBU^2)",
            flush=True,
        )
    return faces, removed


def cap_tiny_triangular_holes(
    *,
    faces: np.ndarray,
    xy_dbu: np.ndarray,
    vertex_planes: np.ndarray,
    vertex_lookup: dict[tuple[int, int, int], int],
    maximum_area2_dbu2: int = MAXIMUM_SLIVER_AREA2_DBU2,
) -> tuple[np.ndarray, int]:
    from shapely.geometry import Polygon

    vertex_count = len(xy_dbu)
    directed = np.stack(
        (faces[:, (0, 1)], faces[:, (1, 2)], faces[:, (2, 0)]),
        axis=1,
    ).reshape((-1, 2))
    canonical = (
        np.minimum(directed[:, 0], directed[:, 1]).astype(np.int64)
        * vertex_count
        + np.maximum(directed[:, 0], directed[:, 1])
    )
    _, inverse, counts = np.unique(
        canonical, return_inverse=True, return_counts=True
    )
    boundary = directed[counts[inverse] == 1]
    if len(boundary) == 0:
        return faces, 0

    outgoing: dict[int, int] = {}
    for first, second in boundary:
        first = int(first)
        second = int(second)
        if first in outgoing:
            raise ValueError("Open mesh boundary branches at a vertex")
        outgoing[first] = second

    cap_chunks = []
    capped_loop_count = 0
    invalid_capped_loop_count = 0
    while outgoing:
        start = next(iter(outgoing))
        loop = [start]
        current = start
        while True:
            following = outgoing.pop(current, None)
            if following is None:
                raise ValueError("Open mesh boundary does not form closed loops")
            if following == start:
                break
            loop.append(following)
            current = following
            if len(loop) > len(boundary):
                raise ValueError("Open mesh boundary traversal did not terminate")
        planes = vertex_planes[loop]
        if not np.all(planes == planes[0]):
            raise ValueError("Refusing to cap a cross-plane open boundary")
        points = xy_dbu[loop].astype(np.int64, copy=False)
        point_tuples = [(int(x), int(y)) for x, y in points]
        loop_area2 = signed_area2(point_tuples)
        area2 = abs(loop_area2)
        if area2 > maximum_area2_dbu2:
            raise ValueError(
                f"Refusing to cap open {len(loop)}-edge boundary with "
                f"area2={area2} DBU^2"
            )
        geometry = Polygon(point_tuples)
        if geometry.is_empty or not geometry.is_valid:
            candidates = []
            expected_sign = -1 if int(planes[0]) == 0 else 1
            for root in range(len(loop)):
                ordered = loop[root:] + loop[:root]
                candidate = np.asarray(
                    [
                        [ordered[0], ordered[index + 1], ordered[index]]
                        for index in range(1, len(ordered) - 1)
                    ],
                    dtype=np.int32,
                )
                candidate_points = xy_dbu[candidate].astype(
                    np.int64, copy=False
                )
                candidate_area2 = (
                    (candidate_points[:, 1, 0] - candidate_points[:, 0, 0])
                    * (candidate_points[:, 2, 1] - candidate_points[:, 0, 1])
                    - (candidate_points[:, 1, 1] - candidate_points[:, 0, 1])
                    * (candidate_points[:, 2, 0] - candidate_points[:, 0, 0])
                )
                absolute_area2 = np.abs(candidate_area2)
                if np.any(absolute_area2 == 0):
                    continue
                total_absolute_area2 = int(absolute_area2.sum())
                if total_absolute_area2 > maximum_area2_dbu2 * 4:
                    continue
                wrong_normal_count = int(
                    np.count_nonzero(candidate_area2 * expected_sign < 0)
                )
                candidates.append(
                    (
                        wrong_normal_count,
                        total_absolute_area2,
                        int(absolute_area2.max()),
                        candidate,
                    )
                )
            if not candidates:
                raise ValueError(
                    f"Refusing to cap invalid {len(loop)}-edge sliver "
                    f"boundary at {point_tuples}"
                )
            candidates.sort(key=lambda candidate: candidate[:3])
            cap_array = candidates[0][3]
            invalid_capped_loop_count += 1
        else:
            triangle_xy = triangle_coordinates(geometry)
            plane = int(planes[0])
            cap_array = np.asarray(
                [
                    [
                        vertex_lookup[(int(x), int(y), plane)]
                        for x, y in triangle
                    ]
                    for triangle in triangle_xy
                ],
                dtype=np.int32,
            )
            if loop_area2 > 0:
                cap_array = cap_array[:, (0, 2, 1)]
        cap_chunks.append(cap_array)
        capped_loop_count += 1

    cap_array = np.concatenate(cap_chunks, axis=0)
    print(
        f"Capped {capped_loop_count} sub-resolution Boolean sliver loops "
        f"with {len(cap_array)} triangles "
        f"({invalid_capped_loop_count} weakly-simple; "
        f"area2 <= {maximum_area2_dbu2} DBU^2)",
        flush=True,
    )
    return np.concatenate((faces, cap_array), axis=0), len(cap_array)


def generate(args: argparse.Namespace) -> None:
    layout = kdb.Layout()
    layout.read(str(args.gds))
    top_cells = layout.top_cells()
    if len(top_cells) != 1:
        raise ValueError(f"Expected one top cell, found {len(top_cells)}")
    top = top_cells[0]
    dbu_um = float(layout.dbu)
    tile_dbu = max(1, int(round(args.tile_size_um / dbu_um)))

    cladding = merged_region(layout, top, args.cladding_layer)
    undercut = merged_region(layout, top, args.undercut_layer)
    passivation = merged_region(layout, top, args.passivation_layer)
    lower = cladding - undercut
    lower.merge()
    upper = lower - passivation
    upper.merge()
    floor = lower & passivation
    floor.merge()
    if lower.is_empty() or upper.is_empty():
        raise ValueError("Cladding subtraction produced an empty solid domain")

    print(f"Input GDS: {args.gds}", flush=True)
    print(f"Top cell: {top.name}; DBU: {dbu_um:g} um", flush=True)
    print(
        f"Tile size: {tile_dbu} DBU ({tile_dbu * dbu_um:g} um)",
        flush=True,
    )
    print(
        f"Domain areas DBU^2: lower={lower.area()}, "
        f"upper={upper.area()}, floor={floor.area()}",
        flush=True,
    )

    builder = MeshBuilder()
    grid_bbox = cladding.bbox()
    tile_stats = []
    lower_plane_stats = add_tiled_plane(
            builder,
            region=lower,
            plane=0,
            reverse=True,
            tile_dbu=tile_dbu,
            grid_bbox=grid_bbox,
            label="lower bottom",
        )
    tile_stats.append(lower_plane_stats)
    if not floor.is_empty():
        tile_stats.append(
            add_tiled_plane(
                builder,
                region=floor,
                plane=1,
                reverse=False,
                tile_dbu=tile_dbu,
                grid_bbox=grid_bbox,
                label="PAAM floor",
            )
        )
    upper_plane_stats = add_tiled_plane(
            builder,
            region=upper,
            plane=2,
            reverse=False,
            tile_dbu=tile_dbu,
            grid_bbox=grid_bbox,
            label="upper top",
        )
    tile_stats.append(upper_plane_stats)
    wall_edge_counts = (
        add_tiled_boundary_walls(
            builder,
            edges=lower_plane_stats[3],
            lower_plane=0,
            upper_plane=1,
            label="lower walls",
        ),
        add_tiled_boundary_walls(
            builder,
            edges=upper_plane_stats[3],
            lower_plane=1,
            upper_plane=2,
            label="upper walls",
        ),
    )

    xy_dbu, vertex_planes, faces = builder.arrays()
    if np.any(faces < 0) or np.any(faces >= len(xy_dbu)):
        raise ValueError("Generated triangle indices are out of range")
    faces, removed_sliver_face_count = remove_tiny_orphan_horizontal_faces(
        faces=faces,
        xy_dbu=xy_dbu,
        vertex_planes=vertex_planes,
    )
    faces, capped_sliver_face_count = cap_tiny_triangular_holes(
        faces=faces,
        xy_dbu=xy_dbu,
        vertex_planes=vertex_planes,
        vertex_lookup=builder.vertex_index,
    )
    validate_closed_triangle_mesh(
        faces=faces,
        vertex_count=len(xy_dbu),
        xy_dbu=xy_dbu,
        vertex_planes=vertex_planes,
    )

    face_planes = vertex_planes[faces]
    horizontal_mask = np.all(face_planes == face_planes[:, :1], axis=1)
    horizontal_faces = faces[horizontal_mask]
    horizontal_face_count = len(horizontal_faces)
    horizontal_xy = xy_dbu[horizontal_faces]
    spans = horizontal_xy.max(axis=1) - horizontal_xy.min(axis=1)
    maximum_horizontal_span_dbu = float(np.hypot(spans[:, 0], spans[:, 1]).max())
    maximum_allowed = tile_dbu * 2**0.5 + 2.0
    if maximum_horizontal_span_dbu > maximum_allowed:
        raise ValueError(
            "Horizontal triangle exceeds tile diagonal: "
            f"{maximum_horizontal_span_dbu} > {maximum_allowed} DBU"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.output,
        format_version=np.asarray([FORMAT_VERSION], dtype=np.int32),
        xy_dbu=xy_dbu,
        vertex_planes=vertex_planes,
        triangles=faces,
        dbu_um=np.asarray([dbu_um], dtype=np.float64),
        z_planes_um=np.asarray(args.z_planes_um, dtype=np.float64),
        tile_size_dbu=np.asarray([tile_dbu], dtype=np.int32),
        horizontal_face_count=np.asarray(
            [horizontal_face_count], dtype=np.int64
        ),
        maximum_horizontal_span_dbu=np.asarray(
            [maximum_horizontal_span_dbu], dtype=np.float64
        ),
        domain_area2_dbu2=np.asarray(
            [stat[1] for stat in tile_stats], dtype=np.int64
        ),
        triangulated_area2_dbu2=np.asarray(
            [stat[2] for stat in tile_stats], dtype=np.int64
        ),
        wall_edge_counts=np.asarray(wall_edge_counts, dtype=np.int32),
        validated_closed_oriented_topology=np.asarray([1], dtype=np.uint8),
        removed_sliver_face_count=np.asarray(
            [removed_sliver_face_count], dtype=np.int32
        ),
        capped_sliver_face_count=np.asarray(
            [capped_sliver_face_count], dtype=np.int32
        ),
    )
    print(
        f"Explicit tiled cladding sidecar written: {args.output} "
        f"({args.output.stat().st_size} bytes)",
        flush=True,
    )
    print(
        f"Final mesh: vertices={len(xy_dbu)}, triangles={len(faces)}, "
        f"horizontal_triangles={horizontal_face_count}, "
        f"max_horizontal_span={maximum_horizontal_span_dbu * dbu_um:g} um",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cladding-layer", type=parse_layer, default=DEFAULT_CLADDING_LAYER)
    parser.add_argument("--undercut-layer", type=parse_layer, default=DEFAULT_UNDERCUT_LAYER)
    parser.add_argument(
        "--passivation-layer", type=parse_layer, default=DEFAULT_PASSIVATION_LAYER
    )
    parser.add_argument("--tile-size-um", type=float, default=200.0)
    parser.add_argument(
        "--z-planes-um",
        type=float,
        nargs=3,
        default=DEFAULT_Z_PLANES_UM,
        metavar=("BOTTOM", "PAAM_FLOOR", "TOP"),
    )
    args = parser.parse_args()
    if args.tile_size_um <= 0:
        parser.error("--tile-size-um must be positive")
    if not args.z_planes_um[0] < args.z_planes_um[1] < args.z_planes_um[2]:
        parser.error("--z-planes-um must be strictly increasing")
    args.gds = args.gds.resolve()
    args.output = args.output.resolve()
    generate(args)


if __name__ == "__main__":
    main()
