#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any

import klayout.db as kdb
import yaml

LayerKey = tuple[int, int]


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _normalize_rgba(base_color: Any, alpha_override: Any) -> list[float]:
    rgba = [1.0, 1.0, 1.0, 1.0]
    if isinstance(base_color, list):
        for idx in range(min(4, len(base_color))):
            channel = base_color[idx]
            if isinstance(channel, (int, float)):
                rgba[idx] = float(channel)
    if isinstance(alpha_override, (int, float)):
        rgba[3] = float(alpha_override)
    return rgba


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping at top level")
    return data


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
        raise ValueError(f"{label} must be a two-element list: [layer, datatype]")
    layer, datatype = value
    if not isinstance(layer, int) or not isinstance(datatype, int):
        raise ValueError(f"{label} must contain two ints: [layer, datatype]")
    if layer < 0 or datatype < 0:
        raise ValueError(f"{label} values must be non-negative")
    return layer, datatype


def get_render_layers(data: dict[str, Any]) -> dict[str, Any]:
    if "render_layers" in data:
        return require_mapping(data["render_layers"], "render_layers")

    output_layers = require_mapping(data.get("output_layers", {}), "output_layers")
    return require_mapping(output_layers.get("render", {}), "output_layers.render")


def load_color_scheme(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    data = load_yaml(path)
    layers = require_mapping(data.get("layers", {}), "color_scheme.layers")
    meta = {
        "name": data.get("name"),
        "description": data.get("description"),
    }
    return meta, layers


def load_present_layers(visual_gds: Path) -> set[LayerKey]:
    layout = kdb.Layout()
    layout.read(str(visual_gds))

    top_cell = layout.top_cell()
    if top_cell is None:
        return set()

    present: set[LayerKey] = set()

    for layer_index in layout.layer_indexes():
        shapes_iter = top_cell.begin_shapes_rec(layer_index)
        if shapes_iter.at_end():
            continue

        layer_info = layout.get_info(layer_index)
        present.add((int(layer_info.layer), int(layer_info.datatype)))

    return present


def to_plain_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: to_plain_data(val) for key, val in value.items()}
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    return value


def should_include_layer(
    name: str,
    spec: dict[str, Any],
    *,
    layer_key: LayerKey,
    present_layers: set[LayerKey],
    min_z: float,
    excluded_roles: set[str],
    excluded_material_classes: set[str],
) -> bool:
    if layer_key not in present_layers:
        return False

    role = spec.get("role")
    if isinstance(role, str) and role in excluded_roles:
        return False

    material_class = spec.get("material_class")
    if isinstance(material_class, str) and material_class in excluded_material_classes:
        return False

    # Keep cladding when it is not explicitly excluded, even if an epsilon
    # overlap makes zmin fall below the export threshold.
    if spec.get("role") == "cladding" or name == "CLADDING_RENDER":
        return True

    zmin = float(require_number(spec.get("zmin"), "render layer zmin"))
    if zmin < min_z:
        return False

    return True


def friendly_material_name(material_class: Any) -> str:
    material_map = {
        "silicon_intrinsic": "intrinsic silicon",
        "silicon_n10": "n-doped silicon",
        "silicon_n12": "n-doped silicon",
        "silicon_n20": "n-doped silicon",
        "silicon_n25": "n-doped silicon",
        "silicon_n30": "n-doped silicon",
        "silicon_n70": "n-doped silicon",
        "silicon_p10": "p-doped silicon",
        "silicon_p12": "p-doped silicon",
        "silicon_p15": "p-doped silicon",
        "silicon_p20": "p-doped silicon",
        "silicon_p25": "p-doped silicon",
        "silicon_p30": "p-doped silicon",
        "silicon_p70": "p-doped silicon",
        "oxide_cladding": "oxide cladding",
        "silicon_nitride": "silicon nitride",
        "tungsten_contact": "tungsten contact",
        "copper": "copper",
        "aluminum": "aluminum",
        "pdk_blackbox_proxy": "PDK black-box proxy",
    }
    if isinstance(material_class, str) and material_class in material_map:
        return material_map[material_class]
    if isinstance(material_class, str) and material_class.strip():
        return material_class.replace("_", " ")
    return "unspecified material"


def friendly_purpose(role: Any) -> str:
    purpose_map = {
        "silicon_intrinsic": "waveguide/device silicon core",
        "silicon_doped": "electrically doped silicon region",
        "pn_conflict_debug": "debug layer for PN overlap",
        "cladding": "optical cladding volume",
        "nitride": "nitride photonic layer",
        "contact": "electrical contact",
        "via": "electrical via",
        "metal": "electrical routing metal",
        "pdk_blackbox": "PDK abstract proxy region",
    }
    if isinstance(role, str) and role in purpose_map:
        return purpose_map[role]
    if isinstance(role, str) and role.strip():
        return role.replace("_", " ")
    return "general geometry"


def build_student_layer_legend(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    legend: list[dict[str, Any]] = []

    for layer in layers:
        gds_layer = int(layer["gds_layer"])
        gds_datatype = int(layer["gds_datatype"])
        material = friendly_material_name(layer.get("material_class"))
        purpose = friendly_purpose(layer.get("role"))

        body = layer.get("body")
        slice_name = layer.get("slice")
        slice_suffix = ""
        if isinstance(body, str) and isinstance(slice_name, str):
            slice_suffix = f" ({body} {slice_name})"

        answer = (
            f"Layer {gds_layer}/{gds_datatype} is {material}; "
            f"purpose: {purpose}{slice_suffix}."
        )

        legend.append(
            {
                "gds_layer": gds_layer,
                "gds_datatype": gds_datatype,
                "name": layer.get("name"),
                "material": material,
                "purpose": purpose,
                "student_answer": answer,
            }
        )

    legend.sort(key=lambda item: (item["gds_layer"], item["gds_datatype"]))
    return legend


def export_vr_starter_metadata(
    *,
    render_layers: dict[str, Any],
    present_layers: set[LayerKey],
    min_z: float,
    excluded_roles: set[str],
    excluded_material_classes: set[str],
) -> tuple[OrderedDict[str, Any], dict[str, Any]]:
    stack_config: OrderedDict[str, Any] = OrderedDict()
    manifest_layers: list[dict[str, Any]] = []

    for name, spec_any in render_layers.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"Render layer name must be a non-empty string: {name!r}")

        spec = require_mapping(spec_any, f"render_layers.{name}")
        layer_key = parse_layer_key(
            spec.get("layer"), label=f"render_layers.{name}.layer"
        )

        if not should_include_layer(
            name,
            spec,
            layer_key=layer_key,
            present_layers=present_layers,
            min_z=min_z,
            excluded_roles=excluded_roles,
            excluded_material_classes=excluded_material_classes,
        ):
            continue

        z = float(require_number(spec.get("zmin"), f"render_layers.{name}.zmin"))
        height = float(
            require_number(spec.get("thickness"), f"render_layers.{name}.thickness")
        )
        if height <= 0:
            raise ValueError(f"render_layers.{name}.thickness must be positive")

        layer, datatype = layer_key
        stack_config[name] = {
            "index": layer,
            "type": datatype,
            "z": z,
            "height": height,
        }

        layer_info = {
            "name": name,
            "gds_layer": layer,
            "gds_datatype": datatype,
            "zmin_um": z,
            "thickness_um": height,
            "zmax_um": z + height,
            "source": spec.get("source"),
            "role": spec.get("role"),
            "material_class": spec.get("material_class"),
            "description": spec.get("description"),
        }

        if "body" in spec:
            layer_info["body"] = spec["body"]
        if "slice" in spec:
            layer_info["slice"] = spec["slice"]
        if "polarity" in spec:
            layer_info["polarity"] = spec["polarity"]
        if "rank" in spec:
            layer_info["rank"] = spec["rank"]
        if "doping_markers" in spec:
            layer_info["doping_markers"] = spec["doping_markers"]

        manifest_layers.append(layer_info)

    student_legend = build_student_layer_legend(manifest_layers)

    manifest = {
        "metadata": {
            "schema": "aim_vr_starter_layer_manifest.v1",
            "min_z_um": min_z,
            "excluded_roles": sorted(excluded_roles),
            "excluded_material_classes": sorted(excluded_material_classes),
            "layer_count": len(manifest_layers),
        },
        "student_layer_legend": student_legend,
        "layers": manifest_layers,
    }

    return stack_config, manifest


def export_vr_color_scheme(
    *,
    included_layer_names: list[str],
    source_meta: dict[str, Any],
    source_layers: dict[str, Any],
) -> dict[str, Any]:
    out_layers: OrderedDict[str, Any] = OrderedDict()
    for layer_name in included_layer_names:
        raw_cfg = source_layers.get(layer_name)
        if not isinstance(raw_cfg, dict):
            continue

        # BlenderGDS versions published in Blender extensions expect lowercase
        # fields: color / metallic / roughness.
        out_layers[layer_name] = {
            "color": _normalize_rgba(raw_cfg.get("Base Color"), raw_cfg.get("Alpha")),
            "metallic": _as_float(raw_cfg.get("Metallic"), 0.1),
            "roughness": _as_float(raw_cfg.get("Roughness"), 0.5),
        }

    out_name = source_meta.get("name")
    if isinstance(out_name, str) and out_name.strip():
        out_name = f"{out_name} (VR Starter)"
    else:
        out_name = "AIM VR Starter"

    out_description = source_meta.get("description")
    if isinstance(out_description, str) and out_description.strip():
        out_description = f"{out_description}. Filtered to layers present in the VR starter visual GDS."
    else:
        out_description = "Filtered to layers present in the VR starter visual GDS."

    return {
        "name": out_name,
        "description": out_description,
        "notes": (
            "This VR starter color file uses BlenderGDS direct-import "
            "runtime keys (color/metallic/roughness) for compatibility."
        ),
        "layers": out_layers,
    }


def write_yaml(
    path: Path, data: OrderedDict[str, Any], source: Path, visual_gds: Path
) -> None:
    header = (
        "# SPDX-FileCopyrightText: 2026\n"
        "#\n"
        "# SPDX-License-Identifier: GPL-3.0-or-later\n"
        "#\n"
        "# Generated by scripts/aim_export_vr_starter_metadata.py\n"
        f"# Source render layers: {source}\n"
        f"# Source visual GDS: {visual_gds}\n"
        "# Do not edit by hand; regenerate from workflow inputs.\n\n"
    )
    body = yaml.safe_dump(to_plain_data(data), sort_keys=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + body, encoding="utf-8")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def parse_csv_set(value: str) -> set[str]:
    out: set[str] = set()
    for raw in value.split(","):
        item = raw.strip()
        if item:
            out.add(item)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Export VR starter metadata from visual GDS and AIM render layers: "
            "BlenderGDS stack YAML plus layer-properties JSON manifest."
        )
    )
    parser.add_argument(
        "--render-layers",
        required=True,
        type=Path,
        help="Path to configs/aim/render_layers.yaml or layer_registry.local.yaml",
    )
    parser.add_argument(
        "--visual-gds",
        required=True,
        type=Path,
        help="Path to the already-generated visual GDS",
    )
    parser.add_argument(
        "--stack-output",
        required=True,
        type=Path,
        help="Output YAML stack config for Blender GDS importer",
    )
    parser.add_argument(
        "--manifest-output",
        required=True,
        type=Path,
        help="Output JSON layer manifest for VR controls/UI",
    )
    parser.add_argument(
        "--student-legend-output",
        type=Path,
        default=None,
        help=(
            "Optional concise student-facing legend JSON path with "
            "layer-number -> plain-language mapping"
        ),
    )
    parser.add_argument(
        "--color-source",
        type=Path,
        default=None,
        help=(
            "Optional source color scheme YAML (for example realistic.yaml). "
            "When set with --color-output, exports a filtered VR color scheme."
        ),
    )
    parser.add_argument(
        "--color-output",
        type=Path,
        default=None,
        help="Optional output color scheme YAML path for VR starter package.",
    )
    parser.add_argument(
        "--min-z",
        type=float,
        default=0.0,
        help="Only include layers with zmin >= this value (default: 0.0)",
    )
    parser.add_argument(
        "--exclude-roles",
        default="cladding,cladding_cutter",
        help=(
            "Comma-separated role names to exclude "
            "(default: cladding,cladding_cutter)"
        ),
    )
    parser.add_argument(
        "--exclude-material-classes",
        default="cutter_air",
        help="Comma-separated material_class values to exclude (default: cutter_air)",
    )
    args = parser.parse_args()

    data = load_yaml(args.render_layers)
    render_layers = get_render_layers(data)
    present_layers = load_present_layers(args.visual_gds)

    excluded_roles = parse_csv_set(args.exclude_roles)
    excluded_material_classes = parse_csv_set(args.exclude_material_classes)

    stack_config, manifest = export_vr_starter_metadata(
        render_layers=render_layers,
        present_layers=present_layers,
        min_z=args.min_z,
        excluded_roles=excluded_roles,
        excluded_material_classes=excluded_material_classes,
    )

    write_yaml(args.stack_output, stack_config, args.render_layers, args.visual_gds)
    write_json(args.manifest_output, manifest)
    if args.student_legend_output is not None:
        write_json(
            args.student_legend_output,
            {
                "metadata": {
                    "schema": "aim_vr_starter_layer_legend.v1",
                    "layer_count": manifest["metadata"]["layer_count"],
                },
                "layers": manifest["student_layer_legend"],
            },
        )

    if (args.color_source is None) != (args.color_output is None):
        raise ValueError("--color-source and --color-output must be provided together")

    if args.color_source is not None and args.color_output is not None:
        source_meta, source_layers = load_color_scheme(args.color_source)
        vr_color_scheme = export_vr_color_scheme(
            included_layer_names=list(stack_config.keys()),
            source_meta=source_meta,
            source_layers=source_layers,
        )
        write_yaml(
            args.color_output,
            OrderedDict(vr_color_scheme),
            args.color_source,
            args.visual_gds,
        )
        print(f"Color scheme: {args.color_output}")

    print(f"Detected {len(present_layers)} present layer/datatype pairs in visual GDS")
    print(f"Exported {len(stack_config)} VR starter layers")
    print(f"Stack config: {args.stack_output}")
    print(f"Layer manifest: {args.manifest_output}")
    if args.student_legend_output is not None:
        print(f"Student legend: {args.student_legend_output}")


if __name__ == "__main__":
    main()
