from __future__ import annotations

import argparse
import ast
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text())
    return data or {}


def extract_aim_layers_from_tech(
    tech_py: Path,
    class_name: str = "LayerMapAIM",
) -> dict[str, dict[str, list[int]]]:
    """Extract AIM native layers from tech.py without importing the PDK."""

    tree = ast.parse(tech_py.read_text())
    layers: dict[str, dict[str, list[int]]] = {}

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name != class_name:
            continue

        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign):
                continue
            if not isinstance(stmt.target, ast.Name):
                continue
            if not isinstance(stmt.value, ast.Tuple):
                continue
            if len(stmt.value.elts) != 2:
                continue

            first, second = stmt.value.elts
            if not (
                isinstance(first, ast.Constant)
                and isinstance(second, ast.Constant)
                and isinstance(first.value, int)
                and isinstance(second.value, int)
            ):
                continue

            name = stmt.target.id
            layers[name] = {
                "layer": [first.value, second.value],
                "source": "aim_tech_py",
            }

        return layers

    raise ValueError(f"Could not find class {class_name} in {tech_py}")


def normalize_layer_block(
    block: dict[str, Any],
    source: str,
) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}

    for name, spec in block.items():
        if "layer" not in spec:
            raise ValueError(f"Layer {name} is missing required key 'layer'")

        layer = spec["layer"]
        if not (
            isinstance(layer, list)
            and len(layer) == 2
            and all(isinstance(x, int) for x in layer)
        ):
            raise ValueError(f"Layer {name} must have layer: [int, int]")

        normalized[name] = dict(spec)
        normalized[name]["source"] = source

    return normalized


def check_duplicate_numbers(groups: dict[str, dict[str, dict[str, Any]]]) -> None:
    seen: dict[tuple[int, int], str] = {}

    for group_name, group in groups.items():
        for layer_name, spec in group.items():
            key = tuple(spec["layer"])
            full_name = f"{group_name}.{layer_name}"

            if key in seen:
                raise ValueError(
                    f"Duplicate layer number {key}: {seen[key]} and {full_name}"
                )

            seen[key] = full_name


def write_combined_aliases(
    aim_layers: dict[str, dict[str, Any]],
    raw_custom_layers: dict[str, dict[str, Any]],
    render_layers: dict[str, dict[str, Any]],
    output: Path,
) -> None:
    groups = {
        "aim_layers": aim_layers,
        "raw_custom_layers": raw_custom_layers,
        "render_layers": render_layers,
    }

    check_duplicate_numbers(groups)

    data = {
        "aim_layers": aim_layers,
        "raw_custom_layers": raw_custom_layers,
        "render_layers": render_layers,
        "all_layers": {
            **aim_layers,
            **raw_custom_layers,
            **render_layers,
        },
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(data, sort_keys=False))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract AIMPhotonics layer numbers from tech.py and merge local custom layers."
    )
    parser.add_argument("--tech", required=True, type=Path)
    parser.add_argument(
        "--raw-custom",
        default=Path("configs/aim/raw_custom_layers.yaml"),
        type=Path,
    )
    parser.add_argument(
        "--render-layers",
        default=Path("configs/aim/render_layers.yaml"),
        type=Path,
    )
    parser.add_argument(
        "--output",
        default=Path("configs/aim/layer_aliases.local.yaml"),
        type=Path,
    )
    parser.add_argument("--class-name", default="LayerMapAIM")
    args = parser.parse_args()

    aim_layers = extract_aim_layers_from_tech(args.tech, args.class_name)

    raw_custom_yaml = load_yaml(args.raw_custom)
    render_yaml = load_yaml(args.render_layers)

    raw_custom_layers = normalize_layer_block(
        raw_custom_yaml.get("raw_custom_layers", {}),
        source="raw_custom_layers",
    )
    render_layers = normalize_layer_block(
        render_yaml.get("render_layers", {}),
        source="render_layers",
    )

    write_combined_aliases(
        aim_layers=aim_layers,
        raw_custom_layers=raw_custom_layers,
        render_layers=render_layers,
        output=args.output,
    )

    print(f"Extracted AIM native layers: {len(aim_layers)}")
    print(f"Loaded raw custom layers: {len(raw_custom_layers)}")
    print(f"Loaded render layers: {len(render_layers)}")
    print(f"Wrote: {args.output}")


if __name__ == "__main__":
    main()
