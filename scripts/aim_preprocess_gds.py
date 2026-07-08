#!/usr/bin/env python3
"""
scripts/aim_preprocess_gds.py

Minimal first-pass AIM raw GDS -> render GDS preprocessor.

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
  - Write output visual/render GDS.

Not implemented yet:
  - Doping resolution.
  - Static layer expression copying.
  - Metal splitting.
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

    This intentionally keeps the first version simple. If we later need explicit
    top-cell selection, add --top and pass the proper option to gf.import_gds
    depending on the installed gdsfactory version.
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


def preprocess_minimal(
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

    c_out = gf.Component(name=f"{Path(input_gds).stem}_VISUAL_MINIMAL")
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

    preprocess_minimal(
        input_gds=args.input,
        registry_yaml=args.registry,
        output_gds=args.output,
        bbox_margin_override=args.bbox_margin,
        show=args.show,
    )


if __name__ == "__main__":
    main()
