#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

import yaml

LayerKey = tuple[int, int]
_SYMBOL_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_EXPR_OPERATOR_WORDS = {"and", "or", "not", "AND", "OR", "NOT"}


def load_yaml(path: Path, *, allow_missing: bool = False) -> dict[str, Any]:
    if not path.exists():
        if allow_missing:
            return {}
        raise FileNotFoundError(path)
    data = yaml.safe_load(path.read_text())
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping at top level")
    return data


def to_plain_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: to_plain_data(val) for key, val in value.items()}
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    return value


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(to_plain_data(data), sort_keys=False))


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def require_number(value: Any, label: str) -> int | float:
    if not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    return value


def parse_layer_key(value: Any, *, label: str) -> LayerKey:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{label}.layer must be a two-element list: [layer, datatype]")
    layer, datatype = value
    if not isinstance(layer, int) or not isinstance(datatype, int):
        raise ValueError(f"{label}.layer must contain two ints: [layer, datatype]")
    if layer < 0 or datatype < 0:
        raise ValueError(f"{label}.layer values must be non-negative")
    return (layer, datatype)


def ast_layer_tuple(node: ast.AST | None) -> LayerKey | None:
    if not isinstance(node, (ast.Tuple, ast.List)) or len(node.elts) != 2:
        return None
    first, second = node.elts
    if not (
        isinstance(first, ast.Constant)
        and isinstance(second, ast.Constant)
        and isinstance(first.value, int)
        and isinstance(second.value, int)
    ):
        return None
    return (first.value, second.value)


def extract_aim_layers_from_tech(
    tech_py: Path,
    *,
    class_name: str = "LayerMapAIM",
) -> OrderedDict[str, dict[str, Any]]:
    """Extract AIM layer numbers from tech.py without importing the PDK.

    This intentionally uses AST parsing instead of importing tech.py, because
    importing the PDK may require local paths, gdsfactory state, or proprietary
    files that should not be needed for registry construction.
    """

    if not tech_py.exists():
        raise FileNotFoundError(tech_py)

    tree = ast.parse(tech_py.read_text())
    out: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue

        for stmt in node.body:
            name: str | None = None
            value_node: ast.AST | None = None

            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                name = stmt.target.id
                value_node = stmt.value
            elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                name = stmt.targets[0].id
                value_node = stmt.value

            if name is None:
                continue

            layer_key = ast_layer_tuple(value_node)
            if layer_key is None:
                continue

            out[name] = {
                "layer": [layer_key[0], layer_key[1]],
                "source": "aim_tech_py",
            }

        if not out:
            raise ValueError(f"Found class {class_name} in {tech_py}, but no layer tuples were parsed")
        return out

    raise ValueError(f"Could not find class {class_name} in {tech_py}")


def normalize_layer_block(
    data: dict[str, Any],
    *,
    block_name: str,
    default_source: str,
    source_policy: str = "preserve",
) -> OrderedDict[str, dict[str, Any]]:
    block_any = data.get(block_name, {})
    if block_any is None:
        block_any = {}
    block = require_mapping(block_any, block_name)

    out: OrderedDict[str, dict[str, Any]] = OrderedDict()
    layer_to_name: dict[LayerKey, str] = {}

    for name, spec_any in block.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{block_name} contains a non-string or empty layer name: {name!r}")
        spec = require_mapping(spec_any, f"{block_name}.{name}")
        key = parse_layer_key(spec.get("layer"), label=f"{block_name}.{name}")
        if key in layer_to_name:
            raise ValueError(
                f"{block_name} has duplicate GDS layer/datatype [{key[0]}, {key[1]}]: "
                f"{layer_to_name[key]!r} and {name!r}"
            )
        layer_to_name[key] = name

        normalized = dict(spec)
        if source_policy == "override" or "source" not in normalized:
            normalized["source"] = default_source
        out[name] = normalized

    return out


def flatten_input_layers(
    aim_layers: OrderedDict[str, dict[str, Any]],
    raw_custom_layers: OrderedDict[str, dict[str, Any]],
) -> OrderedDict[str, dict[str, Any]]:
    overlap = set(aim_layers) & set(raw_custom_layers)
    if overlap:
        raise ValueError(
            "raw_custom_layers redefine AIM native layer names: " + ", ".join(sorted(overlap))
        )

    out: OrderedDict[str, dict[str, Any]] = OrderedDict()
    out.update(aim_layers)
    out.update(raw_custom_layers)
    return out


def flatten_all_layers(
    input_layers: OrderedDict[str, dict[str, Any]],
    render_layers: OrderedDict[str, dict[str, Any]],
) -> OrderedDict[str, dict[str, Any]]:
    overlap = set(input_layers) & set(render_layers)
    if overlap:
        raise ValueError(
            "render_layers reuse input layer names: " + ", ".join(sorted(overlap))
        )
    out: OrderedDict[str, dict[str, Any]] = OrderedDict()
    out.update(input_layers)
    out.update(render_layers)
    return out


def check_duplicate_layer_numbers(groups: dict[str, dict[str, dict[str, Any]]]) -> None:
    seen: dict[LayerKey, str] = {}
    for group_name, group in groups.items():
        for name, spec in group.items():
            key = parse_layer_key(spec.get("layer"), label=f"{group_name}.{name}")
            full_name = f"{group_name}.{name}"
            if key in seen:
                raise ValueError(
                    f"Duplicate GDS layer/datatype [{key[0]}, {key[1]}]: "
                    f"{seen[key]} and {full_name}"
                )
            seen[key] = full_name


def expression_symbols(expression: str) -> set[str]:
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("expression must be a non-empty string")
    return {token for token in _SYMBOL_RE.findall(expression) if token not in _EXPR_OPERATOR_WORDS}


def body_slice_names(body_name: str, body_spec: dict[str, Any]) -> set[str]:
    mode = body_spec.get("material_mode")
    if mode == "sliced":
        slices = require_mapping(body_spec.get("slices"), f"silicon_bodies.{body_name}.slices")
        if not slices:
            raise ValueError(f"silicon_bodies.{body_name}.slices must be non-empty")
        return set(slices)
    if mode == "single":
        slice_name = body_spec.get("slice_name")
        if not isinstance(slice_name, str) or not slice_name:
            raise ValueError(f"silicon_bodies.{body_name}.slice_name must be a non-empty string")
        return {slice_name}
    raise ValueError(f"silicon_bodies.{body_name}.material_mode must be 'sliced' or 'single'")


def validate_silicon_bodies(
    silicon_bodies: dict[str, Any],
    *,
    input_layers: dict[str, dict[str, Any]],
) -> dict[str, set[str]]:
    if not silicon_bodies:
        raise ValueError("silicon_bodies must be non-empty")

    body_to_slices: dict[str, set[str]] = {}

    for body_name, body_spec_any in silicon_bodies.items():
        if not isinstance(body_name, str) or not body_name:
            raise ValueError(f"silicon_bodies contains an invalid body name: {body_name!r}")
        body_spec = require_mapping(body_spec_any, f"silicon_bodies.{body_name}")

        expression = body_spec.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError(f"silicon_bodies.{body_name}.expression must be a non-empty string")
        unknown_symbols = expression_symbols(expression) - set(input_layers)
        if unknown_symbols:
            raise ValueError(
                f"silicon_bodies.{body_name}.expression references unknown input layers: "
                f"{sorted(unknown_symbols)}"
            )

        mode = body_spec.get("material_mode")
        if mode == "sliced":
            slices = require_mapping(body_spec.get("slices"), f"silicon_bodies.{body_name}.slices")
            if not slices:
                raise ValueError(f"silicon_bodies.{body_name}.slices must be non-empty")
            for slice_name, slice_spec_any in slices.items():
                if not isinstance(slice_name, str) or not slice_name:
                    raise ValueError(f"silicon_bodies.{body_name}.slices has invalid name: {slice_name!r}")
                slice_spec = require_mapping(
                    slice_spec_any, f"silicon_bodies.{body_name}.slices.{slice_name}"
                )
                zmin = require_number(slice_spec.get("zmin"), f"silicon_bodies.{body_name}.slices.{slice_name}.zmin")
                thickness = require_number(
                    slice_spec.get("thickness"),
                    f"silicon_bodies.{body_name}.slices.{slice_name}.thickness",
                )
                if thickness <= 0:
                    raise ValueError(f"silicon_bodies.{body_name}.slices.{slice_name}.thickness must be positive")
                # zmin may be negative for future layers, so only require numeric.
                _ = zmin
        elif mode == "single":
            slice_name = body_spec.get("slice_name")
            if not isinstance(slice_name, str) or not slice_name:
                raise ValueError(f"silicon_bodies.{body_name}.slice_name must be a non-empty string")
            zmin = require_number(body_spec.get("zmin"), f"silicon_bodies.{body_name}.zmin")
            thickness = require_number(body_spec.get("thickness"), f"silicon_bodies.{body_name}.thickness")
            if thickness <= 0:
                raise ValueError(f"silicon_bodies.{body_name}.thickness must be positive")
            _ = zmin
        else:
            raise ValueError(f"silicon_bodies.{body_name}.material_mode must be 'sliced' or 'single'")

        body_to_slices[body_name] = body_slice_names(body_name, body_spec)

    return body_to_slices


def output_name(
    template: str,
    *,
    body: str,
    polarity: str | None = None,
    rank: int | None = None,
    slice_name: str | None = None,
) -> str:
    values = {
        "body": body.upper(),
        "polarity": polarity.upper() if polarity is not None else None,
        "rank": rank,
        "slice": slice_name.upper() if slice_name is not None else None,
    }
    try:
        return template.format(**values)
    except KeyError as exc:
        raise ValueError(
            f"Unsupported render_output template placeholder {exc.args[0]!r}; "
            "supported placeholders are {body}, {polarity}, {rank}, and {slice}"
        ) from exc


def get_render_templates(doping_rules: dict[str, Any]) -> dict[str, str]:
    render_output = require_mapping(doping_rules.get("render_output"), "render_output")
    required = [
        "intrinsic_template_sliced",
        "intrinsic_template_single",
        "doped_template_sliced",
        "doped_template_single",
        "conflict_template_sliced",
        "conflict_template_single",
    ]
    templates: dict[str, str] = {}
    for key in required:
        value = render_output.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"render_output.{key} must be a non-empty string")
        templates[key] = value
    return templates


def expected_doping_render_layer_names(doping_rules: dict[str, Any]) -> set[str]:
    silicon_bodies = require_mapping(doping_rules.get("silicon_bodies"), "silicon_bodies")
    doping_markers = require_mapping(doping_rules.get("doping_markers"), "doping_markers")
    templates = get_render_templates(doping_rules)

    names: set[str] = set()

    for body_name, body_spec_any in silicon_bodies.items():
        body_spec = require_mapping(body_spec_any, f"silicon_bodies.{body_name}")
        mode = body_spec.get("material_mode")
        slices = body_slice_names(body_name, body_spec)

        for slice_name in slices:
            if mode == "sliced":
                intrinsic_template = templates["intrinsic_template_sliced"]
                doped_template = templates["doped_template_sliced"]
                conflict_template = templates["conflict_template_sliced"]
                names.add(output_name(intrinsic_template, body=body_name, slice_name=slice_name))
                names.add(output_name(conflict_template, body=body_name, slice_name=slice_name))
            elif mode == "single":
                intrinsic_template = templates["intrinsic_template_single"]
                doped_template = templates["doped_template_single"]
                conflict_template = templates["conflict_template_single"]
                names.add(output_name(intrinsic_template, body=body_name))
                names.add(output_name(conflict_template, body=body_name))
            else:
                raise ValueError(f"silicon_bodies.{body_name}.material_mode must be 'sliced' or 'single'")

            for marker_name, marker_spec_any in doping_markers.items():
                marker_spec = require_mapping(marker_spec_any, f"doping_markers.{marker_name}")
                applies_to = require_mapping(
                    marker_spec.get("applies_to"), f"doping_markers.{marker_name}.applies_to"
                )
                body_slices = applies_to.get(body_name)
                if body_slices is None or slice_name not in body_slices:
                    continue

                polarity = marker_spec.get("polarity")
                if polarity not in {"n", "p"}:
                    raise ValueError(f"doping_markers.{marker_name}.polarity must be 'n' or 'p'")
                rank = marker_spec.get("rank")
                if not isinstance(rank, int):
                    raise ValueError(f"doping_markers.{marker_name}.rank must be an int")

                if mode == "sliced":
                    names.add(
                        output_name(
                            doped_template,
                            body=body_name,
                            polarity=polarity,
                            rank=rank,
                            slice_name=slice_name,
                        )
                    )
                else:
                    names.add(output_name(doped_template, body=body_name, polarity=polarity, rank=rank))

    return names


def validate_doping_rules(
    *,
    doping_rules: dict[str, Any],
    input_layers: OrderedDict[str, dict[str, Any]],
    render_layers: OrderedDict[str, dict[str, Any]],
) -> None:
    silicon_bodies = require_mapping(doping_rules.get("silicon_bodies"), "silicon_bodies")
    doping_markers = require_mapping(doping_rules.get("doping_markers"), "doping_markers")
    _ = get_render_templates(doping_rules)

    body_to_slices = validate_silicon_bodies(silicon_bodies, input_layers=input_layers)

    for marker_name, marker_spec_any in doping_markers.items():
        if marker_name not in input_layers:
            raise ValueError(f"Doping marker {marker_name!r} is not defined in tech.py or raw_custom_layers.yaml")
        marker_spec = require_mapping(marker_spec_any, f"doping_markers.{marker_name}")

        polarity = marker_spec.get("polarity")
        if polarity not in {"n", "p"}:
            raise ValueError(f"doping_markers.{marker_name}.polarity must be 'n' or 'p'")
        rank = marker_spec.get("rank")
        if not isinstance(rank, int):
            raise ValueError(f"doping_markers.{marker_name}.rank must be an int")

        applies_to = require_mapping(marker_spec.get("applies_to"), f"doping_markers.{marker_name}.applies_to")
        if not applies_to:
            raise ValueError(f"doping_markers.{marker_name}.applies_to must be non-empty")

        for body_name, slices_any in applies_to.items():
            if body_name not in body_to_slices:
                raise ValueError(
                    f"doping_markers.{marker_name}.applies_to references unknown body {body_name!r}"
                )
            if not isinstance(slices_any, list) or not slices_any:
                raise ValueError(
                    f"doping_markers.{marker_name}.applies_to.{body_name} must be a non-empty list"
                )
            unknown_slices = set(slices_any) - body_to_slices[body_name]
            if unknown_slices:
                raise ValueError(
                    f"doping_markers.{marker_name}.applies_to.{body_name} references unknown slices: "
                    f"{sorted(unknown_slices)}"
                )

    expected_outputs = expected_doping_render_layer_names(doping_rules)
    missing_outputs = sorted(expected_outputs - set(render_layers))
    if missing_outputs:
        preview = ", ".join(missing_outputs[:20])
        suffix = "" if len(missing_outputs) <= 20 else f" ... (+{len(missing_outputs) - 20} more)"
        raise ValueError(
            "render_layers.yaml is missing doping-derived output layers expected from "
            f"doping_rules.yaml: {preview}{suffix}"
        )

    # Sanity-check generated render layer metadata where present.
    for name in expected_outputs:
        spec = render_layers[name]
        role = spec.get("role")
        if role is not None and role not in {"silicon_intrinsic", "silicon_doped", "pn_conflict_debug"}:
            raise ValueError(f"Render layer {name} has unexpected doping role: {role!r}")

    conflict_policy = doping_rules.get("conflict_policy", {})
    if conflict_policy is not None:
        conflict_policy = require_mapping(conflict_policy, "conflict_policy")
        pn_policy = conflict_policy.get("pn_overlap", "debug_layer")
        if pn_policy not in {"debug_layer", "error", "ignore"}:
            raise ValueError("conflict_policy.pn_overlap must be debug_layer, error, or ignore")

    empty_policy = doping_rules.get("empty_geometry_policy", {})
    if empty_policy is not None:
        require_mapping(empty_policy, "empty_geometry_policy")


def validate_render_layer_expressions(
    *,
    render_layers: OrderedDict[str, dict[str, Any]],
    input_layers: OrderedDict[str, dict[str, Any]],
) -> None:
    input_names = set(input_layers)
    for name, spec in render_layers.items():
        expression = spec.get("expression")
        if expression is None:
            continue
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError(f"render_layers.{name}.expression must be a non-empty string if present")
        unknown = expression_symbols(expression) - input_names
        if unknown:
            raise ValueError(
                f"render_layers.{name}.expression references unknown input layers: {sorted(unknown)}"
            )


def build_registry(
    *,
    tech_py: Path,
    raw_custom_path: Path,
    render_layers_path: Path,
    doping_rules_path: Path,
    class_name: str = "LayerMapAIM",
) -> dict[str, Any]:
    aim_layers = extract_aim_layers_from_tech(tech_py, class_name=class_name)

    raw_custom_yaml = load_yaml(raw_custom_path, allow_missing=True)
    render_layers_yaml = load_yaml(render_layers_path)
    doping_rules = load_yaml(doping_rules_path)

    raw_custom_layers = normalize_layer_block(
        raw_custom_yaml,
        block_name="raw_custom_layers",
        default_source="raw_custom_layers",
        source_policy="override",
    )
    render_layers = normalize_layer_block(
        render_layers_yaml,
        block_name="render_layers",
        default_source="render_layers",
        source_policy="preserve",
    )

    input_layers = flatten_input_layers(aim_layers, raw_custom_layers)
    all_layers = flatten_all_layers(input_layers, render_layers)

    check_duplicate_layer_numbers(
        {
            "input.aim": aim_layers,
            "input.raw_custom": raw_custom_layers,
            "output.render": render_layers,
        }
    )

    validate_doping_rules(
        doping_rules=doping_rules,
        input_layers=input_layers,
        render_layers=render_layers,
    )
    validate_render_layer_expressions(render_layers=render_layers, input_layers=input_layers)

    registry: dict[str, Any] = {
        "metadata": {
            "generated_by": "scripts/aim_build_layer_registry.py",
            "sources": {
                "tech": str(tech_py),
                "raw_custom": str(raw_custom_path),
                "render_layers": str(render_layers_path),
                "doping_rules": str(doping_rules_path),
            },
            "class_name": class_name,
            "do_not_edit": True,
        },
        "input_layers": {
            "aim": aim_layers,
            "raw_custom": raw_custom_layers,
        },
        "output_layers": {
            "render": render_layers,
        },
        "processing": {
            "silicon_doping_resolution": doping_rules,
        },
        "all_layers": all_layers,
    }
    return registry


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build an AIM layer registry from AIM tech.py, custom raw layers, merged "
            "render layers, and doping processing rules."
        )
    )
    parser.add_argument("--tech", required=True, type=Path, help="Path to AIM tech.py")
    parser.add_argument(
        "--raw-custom",
        default=Path("configs/aim/raw_custom_layers.yaml"),
        type=Path,
        help="Path to raw_custom_layers.yaml",
    )
    parser.add_argument(
        "--render-layers",
        default=Path("configs/aim/render_layers.yaml"),
        type=Path,
        help="Path to merged render_layers.yaml",
    )
    parser.add_argument(
        "--doping-rules",
        default=Path("configs/aim/doping_rules.yaml"),
        type=Path,
        help="Path to doping_rules.yaml",
    )
    parser.add_argument(
        "--output",
        default=Path("configs/aim/layer_registry.local.yaml"),
        type=Path,
        help="Path to write layer_registry.local.yaml",
    )
    parser.add_argument(
        "--class-name",
        default="LayerMapAIM",
        help="LayerMap class name to parse from tech.py",
    )
    args = parser.parse_args()

    registry = build_registry(
        tech_py=args.tech,
        raw_custom_path=args.raw_custom,
        render_layers_path=args.render_layers,
        doping_rules_path=args.doping_rules,
        class_name=args.class_name,
    )
    write_yaml(args.output, registry)

    aim_count = len(registry["input_layers"]["aim"])
    raw_custom_count = len(registry["input_layers"]["raw_custom"])
    render_count = len(registry["output_layers"]["render"])
    all_count = len(registry["all_layers"])
    marker_count = len(registry["processing"]["silicon_doping_resolution"].get("doping_markers", {}))

    print(f"AIM native layers: {aim_count}")
    print(f"Raw custom layers: {raw_custom_count}")
    print(f"Render output layers: {render_count}")
    print(f"Doping markers in rules: {marker_count}")
    print(f"All named layers: {all_count}")
    print(f"Wrote: {args.output}")


if __name__ == "__main__":
    main()
