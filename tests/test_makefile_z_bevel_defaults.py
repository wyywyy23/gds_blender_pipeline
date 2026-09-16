from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]


class MakefileZBevelPolicyTests(unittest.TestCase):
    def dry_run(self, *overrides: str) -> str:
        result = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-n",
                "aim-build-scene",
                "AIM_GDS=/private/tmp/input.gds",
                "AIM_VISUAL_GDS=/private/tmp/input.visual.gds",
                "AIM_BLEND=/private/tmp/input.blend",
                "AIM_BLENDER_COLORS=configs/blender/colors/aim/realistic.yaml",
                *overrides,
            ],
            cwd=REPOSITORY,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def test_default_generates_all_metal_sidecars_and_enables_cap_bevel(self) -> None:
        output = self.dry_run()
        self.assertIn("scripts/aim_generate_unfractured_layer_mesh.py", output)
        self.assertIn("--presentation-metal-z-bevel-width-um 0.05", output)
        for layer in ("M1AM_RENDER", "M2AM_RENDER", "MLAM_RENDER"):
            self.assertIn(
                f"metal-z-bevel-sidecars/{layer}.unfractured-layer.npz",
                output,
            )

    def test_top_level_build_places_sidecars_beside_run_blend(self) -> None:
        result = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-n",
                "aim-build",
                "GDS=examples/aim/raw/tx_array_checkered.gds",
                "AIM_RUN_ROOT=/private/tmp/default-z-bevel-policy",
            ],
            cwd=REPOSITORY,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn(
            (
                'sidecar_dir="/private/tmp/default-z-bevel-policy/'
                "tx_array_checkered/blender/metal-z-bevel-sidecars"
            ),
            result.stdout,
        )

    def test_large_gds_opt_out_keeps_fracture_and_disables_z_bevel(self) -> None:
        output = self.dry_run(
            "AIM_BLENDER_PRESENTATION_METAL_Z_BEVEL_ENABLED=0"
        )
        self.assertIn('--max-polygon-vertices "256"', output)
        self.assertIn("--source-max-polygon-vertices 256", output)
        self.assertIn("--presentation-metal-z-bevel-width-um 0", output)
        self.assertNotIn("--presentation-metal-z-bevel-sidecar", output)

    def test_explicit_sidecars_replace_automatic_layer_set(self) -> None:
        output = self.dry_run(
            "AIM_BLENDER_PRESENTATION_METAL_Z_BEVEL_SIDECARS=/private/tmp/custom.npz"
        )
        self.assertIn(
            '--presentation-metal-z-bevel-sidecar "/private/tmp/custom.npz"',
            output,
        )
        self.assertNotIn("M1AM_RENDER.unfractured-layer.npz", output)
        self.assertNotIn("M2AM_RENDER.unfractured-layer.npz", output)
        self.assertNotIn("MLAM_RENDER.unfractured-layer.npz", output)


if __name__ == "__main__":
    unittest.main()
