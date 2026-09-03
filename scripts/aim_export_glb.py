#!/usr/bin/env python3
"""Export the current Blender scene as a presentation-ready GLB.

Run from Blender:

  blender --background scene.blend --python scripts/aim_export_glb.py -- \
    --output .local/runs/example/realtime/example.glb

The exporter leaves the source .blend untouched. It adds temporary object extras so
the web viewer can recover the GDS render-layer and material names from the GLB.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any


BLENDER_SUFFIX = re.compile(r"\.\d{3}$")
GDS_LAYER_OBJECT = re.compile(r"^L[A-Z0-9_]+$")


def blender_argv(argv: list[str]) -> list[str]:
    if "--" not in argv:
        return []
    return argv[argv.index("--") + 1 :]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Output .glb path")
    return parser.parse_args(argv)


def canonical_layer_name(object_name: str) -> str:
    base = BLENDER_SUFFIX.sub("", object_name)
    if GDS_LAYER_OBJECT.fullmatch(base):
        return base[1:]
    return base


def mesh_receipt(obj: Any) -> dict[str, Any]:
    materials = [material.name for material in obj.data.materials if material is not None]
    layer = canonical_layer_name(obj.name)
    obj["gds_layer"] = layer
    obj["gds_material"] = materials[0] if materials else "Unassigned"
    obj["gds_source_object"] = obj.name
    return {
        "object": obj.name,
        "layer": layer,
        "materials": materials,
        "vertices": len(obj.data.vertices),
        "polygons": len(obj.data.polygons),
    }


def main(argv: list[str] | None = None) -> int:
    import bpy

    args = parse_args(blender_argv(sys.argv if argv is None else argv))
    output = args.output.expanduser().resolve()
    if output.suffix.lower() != ".glb":
        raise SystemExit("--output must use the .glb extension")

    meshes = [
        obj for obj in bpy.context.scene.objects
        if obj.type == "MESH" and not obj.hide_render
    ]
    if not meshes:
        raise SystemExit("Scene contains no renderable mesh objects")

    object_receipts = [mesh_receipt(obj) for obj in meshes]
    output.parent.mkdir(parents=True, exist_ok=True)
    result = bpy.ops.export_scene.gltf(
        filepath=str(output),
        check_existing=False,
        export_format="GLB",
        export_materials="EXPORT",
        export_animations=False,
        export_cameras=False,
        export_lights=False,
        export_extras=True,
        export_apply=True,
        export_yup=True,
        use_renderable=True,
        use_visible=False,
    )
    if "FINISHED" not in result or not output.is_file() or output.stat().st_size == 0:
        raise SystemExit(f"GLB export failed: {sorted(result)}")

    layers = sorted({item["layer"] for item in object_receipts})
    materials = sorted({name for item in object_receipts for name in item["materials"]})
    receipt = {
        "workflow": "aim-export-glb",
        "source_blend": bpy.data.filepath,
        "output": str(output),
        "size_bytes": output.stat().st_size,
        "mesh_objects": len(object_receipts),
        "layers": layers,
        "materials": materials,
        "vertices": sum(item["vertices"] for item in object_receipts),
        "polygons": sum(item["polygons"] for item in object_receipts),
    }
    print("AIM_GLTF_EXPORT " + json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
