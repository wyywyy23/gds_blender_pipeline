#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import OrderedDict
from pathlib import Path
from typing import Any

import yaml

DEFAULT_BODY_BASES = {
    "SEAM": 3000,
    "REAM": 3100,
    "RYAM": 3200,
}

DEFAULT_BODY_BLOCK_SIZE = 100
DEFAULT_DATATYPE = 0


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    data = yaml.safe_load(path.read_text())
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping at top level")
    return data


def to_plain_data(value: Any) -> Any:
    """Convert OrderedDict and other mapping/list containers to plain YAML-safe data."""
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


def get_numbering_config(rules: dict[str, Any]) -> tuple[dict[str, int], int, int]:
    """Return body base layers, datatype, and block size.

    doping_rules.yaml may optionally define:

    render_layer_numbering:
      datatype: 0
      body_block_size: 100
      body_bases:
        SEAM: 1000
        REAM: 1100
        RYAM: 1200
    """

    cfg = require_mapping(
        rules.get("render_layer_numbering", {}), "render_layer_numbering"
    )

    body_bases = dict(DEFAULT_BODY_BASES)
    user_bases = cfg.get("body_bases", {})
    if user_bases is not None:
        user_bases = require_mapping(user_bases, "render_layer_numbering.body_bases")
        for body, base in user_bases.items():
            if not isinstance(base, int):
                raise ValueError(
                    f"render_layer_numbering.body_bases.{body} must be an int"
                )
            body_bases[body] = base

    datatype = cfg.get("datatype", DEFAULT_DATATYPE)
    if not isinstance(datatype, int):
        raise ValueError("render_layer_numbering.datatype must be an int")

    block_size = cfg.get("body_block_size", DEFAULT_BODY_BLOCK_SIZE)
    if not isinstance(block_size, int) or block_size <= 0:
        raise ValueError(
            "render_layer_numbering.body_block_size must be a positive int"
        )

    return body_bases, datatype, block_size


def iter_body_slices(body_name: str, body_spec: dict[str, Any]) -> list[dict[str, Any]]:
    mode = body_spec.get("material_mode")
    if mode == "sliced":
        slices = require_mapping(
            body_spec.get("slices"), f"silicon_bodies.{body_name}.slices"
        )
        out: list[dict[str, Any]] = []
        for slice_name, slice_spec in slices.items():
            slice_spec = require_mapping(
                slice_spec, f"silicon_bodies.{body_name}.slices.{slice_name}"
            )
            zmin = require_number(
                slice_spec.get("zmin"),
                f"silicon_bodies.{body_name}.slices.{slice_name}.zmin",
            )
            thickness = require_number(
                slice_spec.get("thickness"),
                f"silicon_bodies.{body_name}.slices.{slice_name}.thickness",
            )
            out.append(
                {
                    "body": body_name,
                    "slice": slice_name,
                    "zmin": zmin,
                    "thickness": thickness,
                    "material_mode": mode,
                }
            )
        return out

    if mode == "single":
        slice_name = body_spec.get("slice_name")
        if not isinstance(slice_name, str) or not slice_name:
            raise ValueError(
                f"silicon_bodies.{body_name}.slice_name must be a non-empty string"
            )
        zmin = require_number(body_spec.get("zmin"), f"silicon_bodies.{body_name}.zmin")
        thickness = require_number(
            body_spec.get("thickness"), f"silicon_bodies.{body_name}.thickness"
        )
        return [
            {
                "body": body_name,
                "slice": slice_name,
                "zmin": zmin,
                "thickness": thickness,
                "material_mode": mode,
            }
        ]

    raise ValueError(
        f"silicon_bodies.{body_name}.material_mode must be either 'sliced' or 'single'"
    )


def output_name(
    template: str,
    *,
    body: str,
    polarity: str | None = None,
    rank: int | None = None,
    slice_name: str | None = None,
) -> str:
    """Render output layer name.

    The YAML templates are written with placeholders such as:
      {body}_{polarity}{rank}_{slice}_RENDER

    We intentionally uppercase polarity and slice in output names so that a
    marker with polarity 'n' on slice 'top' becomes e.g. SEAM_N25_TOP_RENDER.
    """

    values = {
        "body": body.upper(),
        "polarity": polarity.upper() if polarity is not None else None,
        "rank": rank,
        "slice": slice_name.upper() if slice_name is not None else None,
    }
    return template.format(**values)


def get_templates(rules: dict[str, Any]) -> dict[str, str]:
    render_output = require_mapping(rules.get("render_output"), "render_output")
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


def applicable_markers_for(
    *,
    body: str,
    slice_name: str,
    doping_markers: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    applicable: dict[str, dict[str, Any]] = {}

    for marker_name, marker_spec_any in doping_markers.items():
        marker_spec = require_mapping(marker_spec_any, f"doping_markers.{marker_name}")
        applies_to = require_mapping(
            marker_spec.get("applies_to"), f"doping_markers.{marker_name}.applies_to"
        )

        body_slices = applies_to.get(body)
        if body_slices is None:
            continue
        if not isinstance(body_slices, list) or not body_slices:
            raise ValueError(
                f"doping_markers.{marker_name}.applies_to.{body} must be a non-empty list"
            )

        if slice_name in body_slices:
            applicable[marker_name] = marker_spec

    return applicable


def add_or_merge_layer(
    render_layers: OrderedDict[str, dict[str, Any]],
    name: str,
    spec: dict[str, Any],
) -> None:
    """Add a render layer, merging marker names if same output already exists.

    Multiple same-polarity markers with the same rank on the same body/slice
    intentionally produce the same render output. In that case, the output
    layer is a union bucket for that rank/material class.
    """

    if name not in render_layers:
        render_layers[name] = spec
        return

    existing = render_layers[name]
    merge_keys = ["body", "slice", "role", "material_class", "zmin", "thickness"]
    for key in merge_keys:
        if existing.get(key) != spec.get(key):
            raise ValueError(
                f"Generated layer name collision for {name}: field {key!r} differs "
                f"({existing.get(key)!r} vs {spec.get(key)!r})"
            )

    if "doping_markers" in spec:
        markers = set(existing.get("doping_markers", []))
        markers.update(spec["doping_markers"])
        existing["doping_markers"] = sorted(markers)


def generate_doping_render_layers(
    rules: dict[str, Any],
) -> OrderedDict[str, dict[str, Any]]:
    silicon_bodies = require_mapping(rules.get("silicon_bodies"), "silicon_bodies")
    doping_markers = require_mapping(rules.get("doping_markers"), "doping_markers")
    templates = get_templates(rules)
    body_bases, datatype, block_size = get_numbering_config(rules)

    render_layers: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for body_name, body_spec_any in silicon_bodies.items():
        body_spec = require_mapping(body_spec_any, f"silicon_bodies.{body_name}")
        body_base = body_bases.get(body_name)
        if body_base is None:
            raise ValueError(
                f"No render layer base assigned for body {body_name!r}. "
                "Add it under render_layer_numbering.body_bases in doping_rules.yaml."
            )

        body_layer_names_before = len(render_layers)

        for body_slice in iter_body_slices(body_name, body_spec):
            slice_name = body_slice["slice"]
            mode = body_slice["material_mode"]
            zmin = body_slice["zmin"]
            thickness = body_slice["thickness"]

            if mode == "sliced":
                intrinsic_template = templates["intrinsic_template_sliced"]
                doped_template = templates["doped_template_sliced"]
                conflict_template = templates["conflict_template_sliced"]
                intrinsic_name = output_name(
                    intrinsic_template,
                    body=body_name,
                    slice_name=slice_name,
                )
                conflict_name = output_name(
                    conflict_template,
                    body=body_name,
                    slice_name=slice_name,
                )
            else:
                intrinsic_template = templates["intrinsic_template_single"]
                doped_template = templates["doped_template_single"]
                conflict_template = templates["conflict_template_single"]
                intrinsic_name = output_name(
                    intrinsic_template,
                    body=body_name,
                )
                conflict_name = output_name(
                    conflict_template,
                    body=body_name,
                )

            add_or_merge_layer(
                render_layers,
                intrinsic_name,
                {
                    "source": "doping_generated",
                    "role": "silicon_intrinsic",
                    "body": body_name,
                    "slice": slice_name,
                    "material_class": "silicon_intrinsic",
                    "zmin": float(zmin),
                    "thickness": float(thickness),
                },
            )

            applicable = applicable_markers_for(
                body=body_name, slice_name=slice_name, doping_markers=doping_markers
            )

            # Sort by polarity, rank, name to make output stable.
            sorted_applicable = sorted(
                applicable.items(),
                key=lambda item: (
                    str(item[1].get("polarity")),
                    float(item[1].get("rank")),
                    item[0],
                ),
            )

            for marker_name, marker_spec in sorted_applicable:
                polarity = marker_spec.get("polarity")
                if polarity not in {"n", "p"}:
                    raise ValueError(
                        f"doping_markers.{marker_name}.polarity must be 'n' or 'p'"
                    )

                rank_value = marker_spec.get("rank")
                if not isinstance(rank_value, int):
                    raise ValueError(
                        f"doping_markers.{marker_name}.rank must be an int; "
                        f"got {rank_value!r}"
                    )
                rank = rank_value

                if mode == "sliced":
                    name = output_name(
                        doped_template,
                        body=body_name,
                        polarity=polarity,
                        rank=rank,
                        slice_name=slice_name,
                    )
                else:
                    name = output_name(
                        doped_template,
                        body=body_name,
                        polarity=polarity,
                        rank=rank,
                    )

                add_or_merge_layer(
                    render_layers,
                    name,
                    {
                        "source": "doping_generated",
                        "role": "silicon_doped",
                        "body": body_name,
                        "slice": slice_name,
                        "polarity": polarity,
                        "rank": rank,
                        "material_class": f"silicon_{polarity}{rank}",
                        "zmin": float(zmin),
                        "thickness": float(thickness),
                        "doping_markers": [marker_name],
                    },
                )

            add_or_merge_layer(
                render_layers,
                conflict_name,
                {
                    "source": "doping_generated",
                    "role": "pn_conflict_debug",
                    "body": body_name,
                    "slice": slice_name,
                    "material_class": "pn_conflict",
                    "zmin": float(zmin),
                    "thickness": float(thickness),
                },
            )

        # Assign GDS layer/datatype after all outputs for this body have been
        # collected. This keeps numbering compact and deterministic by body.
        body_layer_names = [
            name
            for name in list(render_layers.keys())[body_layer_names_before:]
            if render_layers[name]["body"] == body_name
        ]
        if len(body_layer_names) > block_size:
            raise ValueError(
                f"Body {body_name} generated {len(body_layer_names)} layers, "
                f"exceeding block size {block_size}"
            )

        for offset, layer_name in enumerate(body_layer_names):
            render_layers[layer_name]["layer"] = [body_base + offset, datatype]

    return render_layers


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate doping-derived AIM render layer YAML from doping_rules.yaml."
    )
    parser.add_argument(
        "--doping-rules",
        required=True,
        type=Path,
        help="Path to configs/aim/doping_rules.yaml",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path to write render_layers.doping.generated.yaml",
    )
    args = parser.parse_args()

    rules = load_yaml(args.doping_rules)
    render_layers = generate_doping_render_layers(rules)

    output = {
        "metadata": {
            "generated_by": "scripts/aim_generate_doping_render_layers.py",
            "source": str(args.doping_rules),
            "do_not_edit": True,
        },
        "render_layers": render_layers,
    }
    write_yaml(args.output, output)

    print(f"Generated {len(render_layers)} doping render layers")
    print(f"Wrote: {args.output}")


if __name__ == "__main__":
    main()
