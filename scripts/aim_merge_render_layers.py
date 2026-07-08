#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import OrderedDict
from pathlib import Path
from typing import Any

import yaml

LayerKey = tuple[int, int]


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


def read_render_layers(
    path: Path, *, label: str, allow_missing: bool = False
) -> OrderedDict[str, dict[str, Any]]:
    data = load_yaml(path, allow_missing=allow_missing)
    layers_any = data.get("render_layers", {})
    layers = require_mapping(layers_any, f"{label}.render_layers")

    out: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for name, spec_any in layers.items():
        if not isinstance(name, str) or not name:
            raise ValueError(
                f"{label}.render_layers has a non-string or empty layer name: {name!r}"
            )
        spec = require_mapping(spec_any, f"{label}.render_layers.{name}")
        validate_layer_spec(name=name, spec=spec, label=label)
        out[name] = dict(spec)
    return out


def read_derived_regions(
    path: Path,
    *,
    label: str,
    allow_missing: bool = False,
) -> OrderedDict[str, dict[str, Any]]:
    data = load_yaml(path, allow_missing=allow_missing)
    derived_any = data.get("derived_regions", {})
    if derived_any is None:
        derived_any = {}

    derived = require_mapping(derived_any, f"{label}.derived_regions")

    out: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for name, spec_any in derived.items():
        if not isinstance(name, str) or not name:
            raise ValueError(
                f"{label}.derived_regions has a non-string or empty region name: {name!r}"
            )
        spec = require_mapping(spec_any, f"{label}.derived_regions.{name}")
        out[name] = dict(spec)

    return out


def parse_layer_key(value: Any, *, label: str) -> LayerKey:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{label}.layer must be a two-element list: [layer, datatype]")
    layer, datatype = value
    if not isinstance(layer, int) or not isinstance(datatype, int):
        raise ValueError(f"{label}.layer must contain two ints: [layer, datatype]")
    if layer < 0 or datatype < 0:
        raise ValueError(f"{label}.layer values must be non-negative")
    return (layer, datatype)


def validate_layer_spec(*, name: str, spec: dict[str, Any], label: str) -> None:
    parse_layer_key(spec.get("layer"), label=f"{label}.render_layers.{name}")

    source = spec.get("source")
    if source is not None and not isinstance(source, str):
        raise ValueError(
            f"{label}.render_layers.{name}.source must be a string if present"
        )

    for numeric_key in ("zmin", "thickness"):
        if numeric_key in spec and not isinstance(spec[numeric_key], (int, float)):
            raise ValueError(
                f"{label}.render_layers.{name}.{numeric_key} must be numeric if present"
            )

    if "thickness" in spec and isinstance(spec["thickness"], (int, float)):
        if spec["thickness"] <= 0:
            raise ValueError(f"{label}.render_layers.{name}.thickness must be positive")


def merge_render_layers(
    *,
    doping_layers: OrderedDict[str, dict[str, Any]],
    static_layers: OrderedDict[str, dict[str, Any]],
) -> OrderedDict[str, dict[str, Any]]:
    merged: OrderedDict[str, dict[str, Any]] = OrderedDict()
    layer_to_name: dict[LayerKey, str] = {}

    def add_group(group_name: str, layers: OrderedDict[str, dict[str, Any]]) -> None:
        for name, spec in layers.items():
            if name in merged:
                raise ValueError(
                    f"Render layer name collision: {name!r} appears in both merged output "
                    f"and {group_name} layers"
                )

            key = parse_layer_key(
                spec.get("layer"), label=f"{group_name}.render_layers.{name}"
            )
            if key in layer_to_name:
                previous = layer_to_name[key]
                raise ValueError(
                    f"GDS layer/datatype collision: {name!r} and {previous!r} both use "
                    f"layer [{key[0]}, {key[1]}]"
                )

            merged[name] = spec
            layer_to_name[key] = name

    # Keep doping layers first so generated silicon layers remain stable and easy to inspect.
    add_group("doping", doping_layers)
    add_group("static", static_layers)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Merge AIM doping-generated render layers and hand-written static render layers "
            "into a single render_layers.yaml."
        )
    )
    parser.add_argument(
        "--doping",
        required=True,
        type=Path,
        help="Path to render_layers.doping.generated.yaml",
    )
    parser.add_argument(
        "--static",
        required=True,
        type=Path,
        help="Path to render_layers.static.yaml",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path to write merged render_layers.yaml",
    )
    parser.add_argument(
        "--allow-missing-static",
        action="store_true",
        help="Treat a missing static render layer file as render_layers: {}.",
    )
    args = parser.parse_args()

    doping_layers = read_render_layers(args.doping, label="doping")
    static_layers = read_render_layers(
        args.static,
        label="static",
        allow_missing=args.allow_missing_static,
    )
    static_derived_regions = read_derived_regions(
        args.static,
        label="static",
        allow_missing=args.allow_missing_static,
    )
    merged_layers = merge_render_layers(
        doping_layers=doping_layers,
        static_layers=static_layers,
    )

    output = {
        "metadata": {
            "generated_by": "scripts/aim_merge_render_layers.py",
            "sources": {
                "doping": str(args.doping),
                "static": str(args.static),
            },
            "do_not_edit": True,
        },
        "derived_regions": static_derived_regions,
        "render_layers": merged_layers,
    }
    write_yaml(args.output, output)

    print(
        f"Merged {len(doping_layers)} doping render layers + "
        f"{len(static_layers)} static render layers = {len(merged_layers)} total"
    )
    print(f"Static derived regions: {len(static_derived_regions)}")
    print(f"Wrote: {args.output}")


if __name__ == "__main__":
    main()
