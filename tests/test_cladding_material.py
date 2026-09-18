"""Blender integration checks: run with blender --background --python this_file."""
from pathlib import Path
import sys
import tempfile
import unittest

try:
    import bpy
except ImportError:
    bpy = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


@unittest.skipIf(bpy is None, "Run this integration test inside Blender")
class CladdingMaterialTests(unittest.TestCase):
    def setUp(self):
        from aim_build_blender_scene import apply_color_schema
        self.apply = apply_color_schema
        for material in list(bpy.data.materials):
            bpy.data.materials.remove(material, do_unlink=True)
        self.material = bpy.data.materials.new("Mat_CLADDING_RENDER")
        self.material.use_nodes = True
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def schema(self, name, settings):
        import yaml
        path = Path(self.temp.name) / f"{name}.yaml"
        path.write_text(yaml.safe_dump({"layers": {"CLADDING_RENDER": settings}}))
        return path

    def test_scheme_switch_and_legacy_restore(self):
        for scheme in ("realistic", "fancy", "marketing", "realistic"):
            self.apply(bpy, ROOT / f"configs/blender/colors/aim/{scheme}.yaml")
            bsdf = self.material.node_tree.nodes.get("Principled BSDF")
            output = next(n for n in self.material.node_tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output)
            self.assertEqual(output.inputs["Surface"].links[0].from_node.name, "AIM_Glass_Visible glass")
            self.assertEqual(sum(n.name.startswith("AIM_Glass_Visible glass") for n in self.material.node_tree.nodes), 1)
            clear = self.material.node_tree.nodes["AIM_Glass_Clear transmission"]
            self.assertEqual(tuple(clear.inputs["Color"].default_value), (1, 1, 1, 1))
            wall = self.material.node_tree.nodes["AIM_Glass_Refractive opening walls"]
            self.assertEqual(wall.inputs[2].links[0].from_node, bsdf)
            self.assertAlmostEqual(bsdf.inputs["IOR"].default_value, 1.45)
            self.assertAlmostEqual(self.material.node_tree.nodes["AIM_Glass_Glass Fresnel"].inputs["IOR"].default_value, 1.45)
        legacy = self.schema("legacy", {"Alpha": .16, "Transmission Weight": .86, "IOR": 1.45})
        self.apply(bpy, legacy)
        self.assertEqual(output.inputs["Surface"].links[0].from_node, bsdf)
        self.assertAlmostEqual(bsdf.inputs["Alpha"].default_value, .16)
        self.assertAlmostEqual(bsdf.inputs["Transmission Weight"].default_value, .86)
        self.assertFalse(any(n.get("aim_presentation_glass") for n in self.material.node_tree.nodes))

    def test_save_reload_preserves_shader_and_geometry(self):
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.object
        obj.data.materials.append(self.material)
        obj.rotation_euler = (.3, .4, .5)
        obj.scale = (2, 3, 4)
        before = ([tuple(v.co) for v in obj.data.vertices], [tuple(p.vertices) for p in obj.data.polygons])
        self.apply(bpy, ROOT / "configs/blender/colors/aim/realistic.yaml")
        after = ([tuple(v.co) for v in obj.data.vertices], [tuple(p.vertices) for p in obj.data.polygons])
        self.assertEqual(before, after)
        path = Path(self.temp.name) / "cladding.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path))
        material = bpy.data.materials["Mat_CLADDING_RENDER"]
        output = next(n for n in material.node_tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output)
        self.assertEqual(output.inputs["Surface"].links[0].from_node.name, "AIM_Glass_Visible glass")
        normal = material.node_tree.nodes.get("AIM_Glass_Object normal")
        self.assertEqual((normal.vector_type, normal.convert_from, normal.convert_to), ("NORMAL", "WORLD", "OBJECT"))

    def test_invalid_glass_settings_are_refused(self):
        from aim_build_blender_scene import apply_cladding_presentation_glass
        bsdf = self.material.node_tree.nodes.get("Principled BSDF")
        for value in (True, -.1, 1.1, float("nan"), "opaque"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                apply_cladding_presentation_glass(self.material, bsdf, {"Presentation Glass": {"surface_sheen": value}})
        for config in ({}, {"surface_sheen": .4, "sheen_tint": [.7, .9, 1]}, "glass"):
            with self.subTest(config=config), self.assertRaises(ValueError):
                apply_cladding_presentation_glass(self.material, bsdf, {"Presentation Glass": config})



if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
