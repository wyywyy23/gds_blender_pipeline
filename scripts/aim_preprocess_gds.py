#!/usr/bin/env python3
"""
scripts/aim_preprocess_gds.py

AIM raw GDS -> visual/render GDS preprocessor.

Current functionality:
  - Read raw GDS using gdsfactory.
  - Flatten it.
  - Compute an extended bounding box.
  - Generate substrate render regions:
      SUBSTRATE_BASE_RENDER
      SUBSTRATE_ETCHABLE_RENDER minus TUAM_EXPANDED / DIAM etch regions
  - Generate cladding render regions:
      CLADDING_RENDER
      CLADDING_UNDERCUT_CUTTER_RENDER
  - Generate silicon render regions from doping rules:
      intrinsic silicon
      doped silicon
      PN conflict debug
  - Copy remaining static expression layers:
      nitride, contact, vias, metals, PDK black box
  - Write output visual/render GDS.
"""

from __future__ import annotations

import argparse
import ast
import importlib
from pathlib import Path
from typing import Any

import gdsfactory as gf
from gdsfactory.gpdk import get_generic_pdk
from gdsfactory.typings import CornerMode
from kfactory import kdb
import yaml


OFFSET_WORK_LAYER = (9000, 0)


def load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def ensure_active_pdk() -> None:
    try:
        gf.get_active_pdk()
    except ValueError:
        get_generic_pdk().activate()


def normalize_layer_pair(value: Any) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"Expected layer pair [layer, datatype], got {value!r}")
    return int(value[0]), int(value[1])


def get_render_layers(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    Supports current registry shape:

      output_layers:
        render:
          SUBSTRATE_BASE_RENDER:
            layer: [2000, 0]
            ...

    Also accepts a simpler shape:

      render_layers:
        SUBSTRATE_BASE_RENDER:
          layer: [2000, 0]
    """
    if "output_layers" in registry:
        output_layers = registry.get("output_layers", {})
        render_layers = output_layers.get("render", {})
    else:
        render_layers = registry.get("render_layers", {})

    if not isinstance(render_layers, dict):
        raise ValueError("Registry render layers must be a mapping")

    return render_layers


def get_layer(render_layers: dict[str, dict[str, Any]], name: str) -> tuple[int, int]:
    if name not in render_layers:
        raise KeyError(f"Missing render layer in registry: {name}")

    entry = render_layers[name]
    if "layer" not in entry:
        raise KeyError(f"Render layer {name} has no 'layer' field")

    return normalize_layer_pair(entry["layer"])


def get_input_layers(registry: dict[str, Any]) -> dict[str, tuple[int, int]]:
    input_layers = registry.get("input_layers", {})
    if not isinstance(input_layers, dict):
        raise ValueError("Registry input_layers must be a mapping")

    out: dict[str, tuple[int, int]] = {}
    for group_name, group in input_layers.items():
        if not isinstance(group, dict):
            raise ValueError(f"Registry input_layers.{group_name} must be a mapping")
        for name, entry in group.items():
            if not isinstance(entry, dict) or "layer" not in entry:
                raise ValueError(f"Registry input_layers.{group_name}.{name} has no layer")
            out[str(name)] = normalize_layer_pair(entry["layer"])

    return out


def get_static_derived_regions(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    processing = registry.get("processing", {})
    if not isinstance(processing, dict):
        raise ValueError("Registry processing must be a mapping")

    derived_regions = processing.get("static_derived_regions", {})
    if derived_regions is None:
        derived_regions = {}
    if not isinstance(derived_regions, dict):
        raise ValueError("Registry processing.static_derived_regions must be a mapping")

    return derived_regions


def get_doping_rules(registry: dict[str, Any]) -> dict[str, Any]:
    processing = registry.get("processing", {})
    if not isinstance(processing, dict):
        raise ValueError("Registry processing must be a mapping")

    doping_rules = processing.get("silicon_doping_resolution")
    if doping_rules is None:
        doping_rules = processing.get("doping_rules")
    if not isinstance(doping_rules, dict):
        raise ValueError("Registry processing.silicon_doping_resolution must be a mapping")

    return doping_rules


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def get_bbox_margin(
    render_layers: dict[str, dict[str, Any]], default: float = 10.0
) -> float:
    """
    Use the largest bbox_margin among generated_from_bbox render layers.
    """
    margins: list[float] = []

    for cfg in render_layers.values():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("generated_from_bbox"):
            margins.append(float(cfg.get("bbox_margin", default)))

    return max(margins) if margins else default


def import_flat_gds(path: str | Path) -> gf.Component:
    """
    Import and flatten raw GDS using gdsfactory.

    Top-cell selection can be added as a CLI option if multi-top inputs become
    part of the preprocessing flow.
    """
    c = gf.import_gds(str(path))
    c_flat = c.flatten() or c
    c_flat.name = f"{c.name}_FLAT"
    return c_flat


def component_bbox(c: gf.Component) -> tuple[float, float, float, float]:
    """
    Return xmin, ymin, xmax, ymax.

    Use gdsfactory Component bbox convenience properties because the exact bbox
    API has changed across versions.
    """
    try:
        xmin = float(c.xmin)
        ymin = float(c.ymin)
        xmax = float(c.xmax)
        ymax = float(c.ymax)
    except Exception as exc:
        raise RuntimeError(
            "Could not read component bbox from gdsfactory Component"
        ) from exc

    if xmax <= xmin or ymax <= ymin:
        raise ValueError(f"Invalid component bbox: {(xmin, ymin, xmax, ymax)}")

    return xmin, ymin, xmax, ymax


def extended_bbox_size_and_center(
    bbox: tuple[float, float, float, float],
    margin: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    xmin, ymin, xmax, ymax = bbox

    xmin -= margin
    ymin -= margin
    xmax += margin
    ymax += margin

    size = (xmax - xmin, ymax - ymin)
    center = ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)

    return size, center


def microns_to_dbu(value: float, dbu: float) -> int:
    return int(round(value / dbu))


def bbox_region(
    *,
    size: tuple[float, float],
    center: tuple[float, float],
    dbu: float,
) -> kdb.Region:
    width, height = size
    cx, cy = center

    xmin = microns_to_dbu(cx - width / 2.0, dbu)
    ymin = microns_to_dbu(cy - height / 2.0, dbu)
    xmax = microns_to_dbu(cx + width / 2.0, dbu)
    ymax = microns_to_dbu(cy + height / 2.0, dbu)

    return kdb.Region(kdb.Box(xmin, ymin, xmax, ymax))


def add_region(
    c_out: gf.Component,
    *,
    region: kdb.Region,
    layer: tuple[int, int],
) -> None:
    if region.is_empty():
        return

    layer_index = c_out.kcl.layout.layer(*layer)
    c_out.kdb_cell.shapes(layer_index).insert(region)


def component_from_region(
    region: kdb.Region,
    *,
    layer: tuple[int, int] = OFFSET_WORK_LAYER,
    dbu: float,
) -> gf.Component:
    c = gf.Component()
    if c.kcl.dbu != dbu:
        raise RuntimeError(f"Temporary dbu {c.kcl.dbu} does not match input dbu {dbu}")
    add_region(c, region=region, layer=layer)
    return c


def count_region_polygons(region: kdb.Region) -> int:
    return sum(1 for _ in region.each())


def region_for_layer(c: gf.Component, layer: tuple[int, int]) -> kdb.Region:
    return c.get_region(layer, merge=True).merged()


def build_input_regions(
    c: gf.Component,
    input_layers: dict[str, tuple[int, int]],
) -> dict[str, kdb.Region]:
    return {name: region_for_layer(c, layer) for name, layer in input_layers.items()}


def get_legacy_gdsfactory_offset() -> Any:
    try:
        geometry = importlib.import_module("gdsfactory.geometry")
    except ModuleNotFoundError:
        return None
    return getattr(geometry, "offset", None)


def offset_region_with_gdsfactory(
    region: kdb.Region,
    *,
    distance: float,
    join: str,
    tolerance: int,
    dbu: float,
) -> kdb.Region:
    c = component_from_region(region, dbu=dbu)

    legacy_offset = get_legacy_gdsfactory_offset()
    if legacy_offset is not None:
        c_offset = legacy_offset(
            c,
            distance=distance,
            join=join,
            tolerance=tolerance,
            layer=OFFSET_WORK_LAYER,
        )
        return region_for_layer(c_offset, OFFSET_WORK_LAYER)

    corner_mode_by_join = {
        "miter": CornerMode.square_limit,
        "bevel": CornerMode.octagon_limit,
        "round": CornerMode.square_limit,
    }
    c.offset(
        layer=OFFSET_WORK_LAYER,
        distance=distance,
        flatten=True,
        corner_mode=corner_mode_by_join[join],
    )
    out = region_for_layer(c, OFFSET_WORK_LAYER)

    if join == "round" and not out.is_empty():
        radius = abs(microns_to_dbu(distance, dbu))
        if radius > 0:
            out = out.rounded_corners(radius, radius, tolerance).merged()

    return out


def apply_region_offset(
    region: kdb.Region,
    *,
    operation: dict[str, Any],
    dbu: float,
    region_name: str,
) -> kdb.Region:
    op_type = operation.get("type")
    if op_type != "offset":
        raise ValueError(
            f"derived_regions.{region_name} operation type is unsupported: {op_type!r}"
        )

    distance = operation.get("distance")
    if not isinstance(distance, (int, float)):
        raise ValueError(
            f"derived_regions.{region_name} offset operation requires numeric distance"
        )

    distance_dbu = microns_to_dbu(float(distance), dbu)
    if distance_dbu == 0:
        return region.dup().merged()

    join = operation.get("join", "miter")
    if join not in {"miter", "bevel", "round"}:
        raise ValueError(
            f"derived_regions.{region_name} offset join must be miter, bevel, or round"
        )

    tolerance = operation.get("tolerance", 64)
    if not isinstance(tolerance, int):
        raise ValueError(
            f"derived_regions.{region_name} offset tolerance must be an integer"
        )

    return offset_region_with_gdsfactory(
        region,
        distance=float(distance),
        join=join,
        tolerance=tolerance,
        dbu=dbu,
    ).merged()


def build_derived_regions(
    *,
    input_regions: dict[str, kdb.Region],
    derived_specs: dict[str, dict[str, Any]],
    dbu: float,
) -> dict[str, kdb.Region]:
    derived_regions: dict[str, kdb.Region] = {}

    for name, spec in derived_specs.items():
        if not isinstance(spec, dict):
            raise ValueError(f"derived_regions.{name} must be a mapping")

        source = spec.get("source")
        if not isinstance(source, str) or not source:
            raise ValueError(f"derived_regions.{name}.source must be a non-empty string")

        symbols = input_regions | derived_regions
        if source not in symbols:
            raise KeyError(f"derived_regions.{name}.source is unknown: {source}")

        region = symbols[source].dup()
        empty_policy = spec.get("empty_source_policy", "error")
        if region.is_empty() and empty_policy == "error":
            raise ValueError(f"derived_regions.{name}.source is empty: {source}")

        operations = spec.get("operations", [])
        if not isinstance(operations, list):
            raise ValueError(f"derived_regions.{name}.operations must be a list")

        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError(f"derived_regions.{name}.operations entries must map")
            if region.is_empty():
                continue
            region = apply_region_offset(
                region,
                operation=operation,
                dbu=dbu,
                region_name=name,
            )

        derived_regions[name] = region.merged()

    return derived_regions


def evaluate_region_expression(
    expression: str,
    symbols: dict[str, kdb.Region],
) -> kdb.Region:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid region expression: {expression!r}") from exc

    def eval_node(node: ast.AST) -> kdb.Region:
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Name):
            if node.id not in symbols:
                raise KeyError(f"Region expression references unknown symbol: {node.id}")
            return symbols[node.id].dup()
        if isinstance(node, ast.BinOp):
            left = eval_node(node.left)
            right = eval_node(node.right)
            if isinstance(node.op, ast.BitOr):
                return (left | right).merged()
            if isinstance(node.op, ast.BitAnd):
                return (left & right).merged()
            if isinstance(node.op, ast.Sub):
                return (left - right).merged()
            if isinstance(node.op, ast.BitXor):
                return (left ^ right).merged()

        raise ValueError(
            f"Unsupported region expression syntax: {ast.dump(node, include_attributes=False)}"
        )

    return eval_node(tree).merged()


def index_silicon_render_layers(
    render_layers: dict[str, dict[str, Any]],
) -> dict[str, dict[tuple[Any, ...], tuple[str, tuple[int, int]]]]:
    index: dict[str, dict[tuple[Any, ...], tuple[str, tuple[int, int]]]] = {
        "intrinsic": {},
        "doped": {},
        "conflict": {},
    }

    for name, spec in render_layers.items():
        if not isinstance(spec, dict) or spec.get("source") != "doping_generated":
            continue

        body = spec.get("body")
        slice_name = spec.get("slice")
        if not isinstance(body, str) or not isinstance(slice_name, str):
            raise ValueError(f"Doping render layer {name} requires body and slice")

        layer = get_layer(render_layers, name)
        role = spec.get("role")
        if role == "silicon_intrinsic":
            index["intrinsic"][(body, slice_name)] = (name, layer)
        elif role == "silicon_doped":
            polarity = spec.get("polarity")
            rank = spec.get("rank")
            if polarity not in {"n", "p"} or not isinstance(rank, int):
                raise ValueError(
                    f"Doping render layer {name} requires polarity and integer rank"
                )
            index["doped"][(body, slice_name, polarity, rank)] = (name, layer)
        elif role == "pn_conflict_debug":
            index["conflict"][(body, slice_name)] = (name, layer)

    return index


def silicon_body_slices(doping_rules: dict[str, Any]) -> list[tuple[str, str]]:
    silicon_bodies = require_mapping(
        doping_rules.get("silicon_bodies"), "silicon_bodies"
    )
    out: list[tuple[str, str]] = []

    for body_name, body_spec_any in silicon_bodies.items():
        body_spec = require_mapping(body_spec_any, f"silicon_bodies.{body_name}")
        mode = body_spec.get("material_mode")
        if mode == "sliced":
            slices = require_mapping(
                body_spec.get("slices"), f"silicon_bodies.{body_name}.slices"
            )
            for slice_name in slices:
                out.append((body_name, str(slice_name)))
        elif mode == "single":
            slice_name = body_spec.get("slice_name")
            if not isinstance(slice_name, str) or not slice_name:
                raise ValueError(
                    f"silicon_bodies.{body_name}.slice_name must be a non-empty string"
                )
            out.append((body_name, slice_name))
        else:
            raise ValueError(
                f"silicon_bodies.{body_name}.material_mode must be 'sliced' or 'single'"
            )

    return out


def build_silicon_body_regions(
    *,
    doping_rules: dict[str, Any],
    symbols: dict[str, kdb.Region],
) -> dict[str, kdb.Region]:
    silicon_bodies = require_mapping(
        doping_rules.get("silicon_bodies"), "silicon_bodies"
    )
    body_regions: dict[str, kdb.Region] = {}

    for body_name, body_spec_any in silicon_bodies.items():
        body_spec = require_mapping(body_spec_any, f"silicon_bodies.{body_name}")
        expression = body_spec.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError(
                f"silicon_bodies.{body_name}.expression must be a non-empty string"
            )
        body_regions[body_name] = evaluate_region_expression(expression, symbols)

    return body_regions


def marker_applies_to_slice(
    marker_spec: dict[str, Any],
    *,
    body: str,
    slice_name: str,
    marker_name: str,
) -> bool:
    applies_to = require_mapping(
        marker_spec.get("applies_to"), f"doping_markers.{marker_name}.applies_to"
    )
    slices = applies_to.get(body)
    return isinstance(slices, list) and slice_name in slices


def union_regions(regions: list[kdb.Region]) -> kdb.Region:
    out = kdb.Region()
    for region in regions:
        out = (out | region).merged()
    return out


def resolve_same_polarity_regions(
    rank_regions: dict[int, kdb.Region],
) -> dict[int, kdb.Region]:
    resolved: dict[int, kdb.Region] = {}
    higher = kdb.Region()

    for rank in sorted(rank_regions, reverse=True):
        region = (rank_regions[rank] - higher).merged()
        resolved[rank] = region
        higher = (higher | rank_regions[rank]).merged()

    return resolved


def resolve_silicon_slice_regions(
    *,
    body_region: kdb.Region,
    body: str,
    slice_name: str,
    doping_rules: dict[str, Any],
    input_regions: dict[str, kdb.Region],
) -> dict[str, Any]:
    doping_markers = require_mapping(
        doping_rules.get("doping_markers"), "doping_markers"
    )
    conflict_policy = require_mapping(
        doping_rules.get("conflict_policy", {}), "conflict_policy"
    )

    same_rank_policy = conflict_policy.get("same_polarity_same_rank", "union")
    if same_rank_policy != "union":
        raise ValueError(
            "Only conflict_policy.same_polarity_same_rank='union' is supported"
        )
    same_polarity_policy = conflict_policy.get(
        "same_polarity_different_rank", "higher_rank_wins"
    )
    if same_polarity_policy != "higher_rank_wins":
        raise ValueError(
            "Only conflict_policy.same_polarity_different_rank='higher_rank_wins' "
            "is supported"
        )

    rank_regions: dict[str, dict[int, kdb.Region]] = {"n": {}, "p": {}}

    for marker_name, marker_spec_any in doping_markers.items():
        marker_spec = require_mapping(marker_spec_any, f"doping_markers.{marker_name}")
        if not marker_applies_to_slice(
            marker_spec, body=body, slice_name=slice_name, marker_name=marker_name
        ):
            continue

        polarity = marker_spec.get("polarity")
        rank = marker_spec.get("rank")
        if polarity not in {"n", "p"} or not isinstance(rank, int):
            raise ValueError(
                f"doping_markers.{marker_name} requires polarity n/p and integer rank"
            )
        if marker_name not in input_regions:
            raise KeyError(f"Doping marker input layer missing: {marker_name}")

        marker_region = (body_region & input_regions[marker_name]).merged()
        if rank in rank_regions[polarity]:
            rank_regions[polarity][rank] = (
                rank_regions[polarity][rank] | marker_region
            ).merged()
        else:
            rank_regions[polarity][rank] = marker_region

    resolved_n = resolve_same_polarity_regions(rank_regions["n"])
    resolved_p = resolve_same_polarity_regions(rank_regions["p"])
    n_total = union_regions(list(resolved_n.values()))
    p_total = union_regions(list(resolved_p.values()))
    conflict = (n_total & p_total).merged()

    pn_policy = conflict_policy.get("pn_overlap", "debug_layer")
    if pn_policy == "error" and not conflict.is_empty():
        raise ValueError(f"PN doping overlap in {body}.{slice_name}")
    if pn_policy == "debug_layer":
        resolved_n = {
            rank: (region - conflict).merged() for rank, region in resolved_n.items()
        }
        resolved_p = {
            rank: (region - conflict).merged() for rank, region in resolved_p.items()
        }
        conflict_region = conflict
    elif pn_policy == "ignore":
        conflict_region = kdb.Region()
    elif pn_policy == "error":
        conflict_region = kdb.Region()
    else:
        raise ValueError("conflict_policy.pn_overlap must be debug_layer, error, or ignore")

    doped_regions: dict[tuple[str, int], kdb.Region] = {}
    for rank, region in resolved_n.items():
        doped_regions[("n", rank)] = region
    for rank, region in resolved_p.items():
        doped_regions[("p", rank)] = region

    all_doped = (n_total | p_total).merged()
    intrinsic = (body_region - all_doped).merged()

    return {
        "intrinsic": intrinsic,
        "doped": doped_regions,
        "conflict": conflict_region,
    }


def add_silicon_regions(
    *,
    c_out: gf.Component,
    render_layers: dict[str, dict[str, Any]],
    doping_rules: dict[str, Any],
    input_regions: dict[str, kdb.Region],
    region_symbols: dict[str, kdb.Region],
) -> dict[str, int]:
    layer_index = index_silicon_render_layers(render_layers)
    body_regions = build_silicon_body_regions(
        doping_rules=doping_rules,
        symbols=region_symbols,
    )

    stats = {
        "intrinsic_nonempty": 0,
        "doped_nonempty": 0,
        "conflict_nonempty": 0,
        "total_nonempty": 0,
    }

    for body, slice_name in silicon_body_slices(doping_rules):
        if body not in body_regions:
            raise KeyError(f"Silicon body region missing: {body}")

        resolved = resolve_silicon_slice_regions(
            body_region=body_regions[body],
            body=body,
            slice_name=slice_name,
            doping_rules=doping_rules,
            input_regions=input_regions,
        )

        intrinsic_entry = layer_index["intrinsic"].get((body, slice_name))
        if intrinsic_entry is None:
            raise KeyError(f"Missing intrinsic render layer for {body}.{slice_name}")
        _, intrinsic_layer = intrinsic_entry
        intrinsic_region = resolved["intrinsic"]
        add_region(c_out, region=intrinsic_region, layer=intrinsic_layer)
        if not intrinsic_region.is_empty():
            stats["intrinsic_nonempty"] += 1
            stats["total_nonempty"] += 1

        conflict_entry = layer_index["conflict"].get((body, slice_name))
        if conflict_entry is None:
            raise KeyError(f"Missing PN conflict render layer for {body}.{slice_name}")
        _, conflict_layer = conflict_entry
        conflict_region = resolved["conflict"]
        add_region(c_out, region=conflict_region, layer=conflict_layer)
        if not conflict_region.is_empty():
            stats["conflict_nonempty"] += 1
            stats["total_nonempty"] += 1

        for (polarity, rank), doped_region in resolved["doped"].items():
            doped_entry = layer_index["doped"].get((body, slice_name, polarity, rank))
            if doped_entry is None:
                raise KeyError(
                    f"Missing doped render layer for {body}.{slice_name}.{polarity}{rank}"
                )
            _, doped_layer = doped_entry
            add_region(c_out, region=doped_region, layer=doped_layer)
            if not doped_region.is_empty():
                stats["doped_nonempty"] += 1
                stats["total_nonempty"] += 1

    return stats


def add_static_expression_render_layers(
    *,
    c_out: gf.Component,
    render_layers: dict[str, dict[str, Any]],
    region_symbols: dict[str, kdb.Region],
    handled_layers: set[str],
) -> dict[str, int]:
    stats = {
        "candidate": 0,
        "nonempty": 0,
        "empty": 0,
    }

    for name, spec in render_layers.items():
        if name in handled_layers:
            continue
        if not isinstance(spec, dict) or spec.get("source") != "static":
            continue

        expression = spec.get("expression")
        if expression is None:
            continue
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError(f"render_layers.{name}.expression must be non-empty")

        stats["candidate"] += 1
        region = evaluate_region_expression(expression, region_symbols)
        if region.is_empty():
            stats["empty"] += 1
            continue

        add_region(c_out, region=region, layer=get_layer(render_layers, name))
        stats["nonempty"] += 1

    return stats


def preprocess_aim_gds(
    input_gds: str | Path,
    registry_yaml: str | Path,
    output_gds: str | Path,
    *,
    bbox_margin_override: float | None = None,
    show: bool = False,
) -> gf.Component:
    ensure_active_pdk()

    registry = load_yaml(registry_yaml)
    render_layers = get_render_layers(registry)
    input_layers = get_input_layers(registry)
    derived_specs = get_static_derived_regions(registry)
    doping_rules = get_doping_rules(registry)

    substrate_base_layer = get_layer(render_layers, "SUBSTRATE_BASE_RENDER")
    substrate_etchable = render_layers["SUBSTRATE_ETCHABLE_RENDER"]
    substrate_etchable_layer = get_layer(render_layers, "SUBSTRATE_ETCHABLE_RENDER")
    etch_expression = substrate_etchable.get("preprocessing_etch_expression")
    if not isinstance(etch_expression, str) or not etch_expression.strip():
        raise ValueError(
            "SUBSTRATE_ETCHABLE_RENDER.preprocessing_etch_expression must be set"
        )

    cladding = render_layers["CLADDING_RENDER"]
    cladding_layer = get_layer(render_layers, "CLADDING_RENDER")
    cladding_exclusion_expression = cladding.get(
        "preprocessing_exclusion_expression", "DIAM"
    )
    if (
        not isinstance(cladding_exclusion_expression, str)
        or not cladding_exclusion_expression.strip()
    ):
        raise ValueError("CLADDING_RENDER.preprocessing_exclusion_expression must be set")

    cladding_cutter = render_layers["CLADDING_UNDERCUT_CUTTER_RENDER"]
    cladding_cutter_layer = get_layer(
        render_layers, "CLADDING_UNDERCUT_CUTTER_RENDER"
    )
    cladding_cutter_expression = cladding_cutter.get("expression")
    if (
        not isinstance(cladding_cutter_expression, str)
        or not cladding_cutter_expression.strip()
    ):
        raise ValueError(
            "CLADDING_UNDERCUT_CUTTER_RENDER.expression must be set"
        )

    bbox_margin = (
        float(bbox_margin_override)
        if bbox_margin_override is not None
        else get_bbox_margin(render_layers)
    )

    c_flat = import_flat_gds(input_gds)
    bbox = component_bbox(c_flat)
    size, center = extended_bbox_size_and_center(bbox, bbox_margin)
    dbu = c_flat.kcl.dbu
    substrate_region = bbox_region(size=size, center=center, dbu=dbu)

    input_regions = build_input_regions(c_flat, input_layers)
    derived_regions = build_derived_regions(
        input_regions=input_regions,
        derived_specs=derived_specs,
        dbu=dbu,
    )
    region_symbols = input_regions | derived_regions
    etch_region = evaluate_region_expression(etch_expression, region_symbols)
    substrate_etchable_region = (substrate_region - etch_region).merged()
    cladding_exclusion_region = evaluate_region_expression(
        cladding_exclusion_expression, region_symbols
    )
    cladding_region = (substrate_region - cladding_exclusion_region).merged()
    cladding_cutter_region = evaluate_region_expression(
        cladding_cutter_expression, region_symbols
    )

    c_out = gf.Component(name=f"{Path(input_gds).stem}_VISUAL")
    if c_out.kcl.dbu != dbu:
        raise RuntimeError(f"Output dbu {c_out.kcl.dbu} does not match input dbu {dbu}")

    add_region(
        c_out,
        region=substrate_region,
        layer=substrate_base_layer,
    )

    add_region(
        c_out,
        region=substrate_etchable_region,
        layer=substrate_etchable_layer,
    )

    add_region(
        c_out,
        region=cladding_region,
        layer=cladding_layer,
    )

    add_region(
        c_out,
        region=cladding_cutter_region,
        layer=cladding_cutter_layer,
    )

    silicon_stats = add_silicon_regions(
        c_out=c_out,
        render_layers=render_layers,
        doping_rules=doping_rules,
        input_regions=input_regions,
        region_symbols=region_symbols,
    )
    static_expression_stats = add_static_expression_render_layers(
        c_out=c_out,
        render_layers=render_layers,
        region_symbols=region_symbols,
        handled_layers={
            "SUBSTRATE_BASE_RENDER",
            "SUBSTRATE_ETCHABLE_RENDER",
            "CLADDING_RENDER",
            "CLADDING_UNDERCUT_CUTTER_RENDER",
        },
    )

    output_gds = Path(output_gds)
    output_gds.parent.mkdir(parents=True, exist_ok=True)
    c_out.write_gds(str(output_gds))

    print(f"Input GDS:  {input_gds}")
    print(f"Registry:   {registry_yaml}")
    print(f"Output GDS: {output_gds}")
    print(
        f"Input bbox: xmin={bbox[0]:.3f}, ymin={bbox[1]:.3f}, xmax={bbox[2]:.3f}, ymax={bbox[3]:.3f}"
    )
    print(f"BBox margin: {bbox_margin:.3f} um")
    print(f"Render bbox size: {size[0]:.3f} x {size[1]:.3f} um")
    print(f"Render bbox center: ({center[0]:.3f}, {center[1]:.3f})")
    print(f"SUBSTRATE_BASE_RENDER layer: {substrate_base_layer}")
    print(f"SUBSTRATE_ETCHABLE_RENDER layer: {substrate_etchable_layer}")
    print(f"Etch expression: {etch_expression}")
    print(f"Etch region polygons: {count_region_polygons(etch_region)}")
    print(
        "SUBSTRATE_ETCHABLE_RENDER polygons: "
        f"{count_region_polygons(substrate_etchable_region)}"
    )
    print(f"CLADDING_RENDER layer: {cladding_layer}")
    print(f"CLADDING_UNDERCUT_CUTTER_RENDER layer: {cladding_cutter_layer}")
    print(f"Cladding exclusion expression: {cladding_exclusion_expression}")
    print(f"Cladding exclusion polygons: {count_region_polygons(cladding_exclusion_region)}")
    print(f"Cladding cutter expression: {cladding_cutter_expression}")
    print(f"CLADDING_RENDER polygons: {count_region_polygons(cladding_region)}")
    print(
        "CLADDING_UNDERCUT_CUTTER_RENDER polygons: "
        f"{count_region_polygons(cladding_cutter_region)}"
    )
    print(
        "Silicon non-empty outputs: "
        f"{silicon_stats['total_nonempty']} "
        f"(intrinsic={silicon_stats['intrinsic_nonempty']}, "
        f"doped={silicon_stats['doped_nonempty']}, "
        f"pn_conflict={silicon_stats['conflict_nonempty']})"
    )
    print(
        "Static expression outputs: "
        f"{static_expression_stats['nonempty']} non-empty, "
        f"{static_expression_stats['empty']} empty, "
        f"{static_expression_stats['candidate']} total"
    )

    if show:
        c_out.show()

    return c_out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Input raw GDS")
    parser.add_argument(
        "--registry", required=True, help="configs/aim/layer_registry.local.yaml"
    )
    parser.add_argument("--output", required=True, help="Output visual/render GDS")
    parser.add_argument(
        "--bbox-margin", type=float, default=None, help="Override bbox margin in um"
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Open output component in KLayout via gdsfactory",
    )
    args = parser.parse_args()

    preprocess_aim_gds(
        input_gds=args.input,
        registry_yaml=args.registry,
        output_gds=args.output,
        bbox_margin_override=args.bbox_margin,
        show=args.show,
    )


if __name__ == "__main__":
    main()
