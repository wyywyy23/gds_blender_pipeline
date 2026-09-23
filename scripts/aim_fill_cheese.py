"""ONE Lab fill/cheese geometry for visual GDS (not a foundry mask deck).

Adapted from onelab-layout's src/onelab/finishing.py and _geometry.py,
2026-09-23 working snapshot over 28970731221029ff4048616951db01237957f1e5.
The source/configuration hashes and supported behavior are in docs/web-app.md.
This standalone adapter needs only the pipeline's existing KLayout and gdstk;
no sibling checkout, vendor code or different gdsfactory environment is required.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
import json
import math
from pathlib import Path
from kfactory import kdb

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "finishing"
Layer = tuple[int, int]


def read_settings(name):
    return json.loads((CONFIG_DIR / name).read_text())


def offset_region(region, distance, dbu, join="round", tolerance=144, *, conservative=False):
    if region.is_empty() or distance == 0:
        return region.dup()
    import gdstk  # Keep ordinary component/font imports independent of this backend.

    if conservative and join == "round":
        # gdstk represents arcs by chords. Compensate chord sagitta and grid
        # rounding so a requested minimum clearance is not shortened at corners.
        magnitude = abs(distance) / math.cos(math.pi / tolerance) + math.sqrt(2) * dbu
        distance = math.copysign(magnitude, distance)
    points = [[(p.x * dbu, p.y * dbu) for p in polygon.resolved_holes().each_point_hull()]
              for polygon in region.merged().each()]
    # The input is already merged into disjoint connected components. Offsetting
    # each component then unioning avoids Clipper's expensive global intersections
    # on large layouts, with the same round arcs, compensation and database grid.
    batches = ([path] for path in points) if len(points) > 1 and sum(map(len, points)) > 20_000 else (points,)
    result = kdb.Region()
    for batch in batches:
        polygons = gdstk.offset(batch, distance, join=join, tolerance=tolerance,
                               precision=dbu, use_union=True)
        for polygon in polygons:
            result.insert(kdb.DPolygon([kdb.DPoint(float(x), float(y)) for x, y in polygon.points]).to_itype(dbu))
    return result.merged()



def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")
    return float(value)


def _pair(value, name, *, layer=False):
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError(f"{name} must contain two values")
    if layer:
        if any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 65535 for v in value):
            raise ValueError(f"{name} must be a valid GDS layer/datatype")
    elif any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ValueError(f"{name} must contain finite coordinates")
    return tuple(value)


@dataclass(frozen=True)
class Pattern:
    kind: str = "square"
    width: float = 1.6
    pitch: tuple[float, float] = (3.0, 3.0)
    phase: tuple[float, float] = (0.0, 0.0)
    length: float = 0.0
    stagger: bool = False
    rotation_deg: int = 0

    def __post_init__(self):
        if self.kind not in {"square", "circle", "capsule", "dots_capsules"}:
            raise ValueError(f"Unsupported pattern: {self.kind}")
        _number(self.width, "pattern width", positive=True)
        _number(self.length, "pattern length")
        object.__setattr__(self, "pitch", _pair(self.pitch, "pitch"))
        object.__setattr__(self, "phase", _pair(self.phase, "phase"))
        if type(self.rotation_deg) is not int or self.rotation_deg not in (0, 90):
            raise ValueError("rotation_deg must be 0 or 90")
        if type(self.stagger) is not bool:
            raise ValueError("stagger must be a boolean")
        if self.width >= min(self.pitch):
            raise ValueError("Pattern width must be smaller than both pitches")
        if self.kind in {"capsule", "dots_capsules"}:
            limit = self.pitch[1] * (3 if self.kind == "dots_capsules" else 1)
            if not self.width <= self.length < limit:
                raise ValueError("Capsule length must be at least its width and below the allocated pitch")
        if self.kind == "dots_capsules" and self.stagger:
            raise ValueError("The dot/capsule motif has its own interleave; stagger is not applicable")


@dataclass(frozen=True)
class LayerRecipe:
    name: str
    layer: Layer
    fill: Pattern | None = None
    fill_block: Layer | None = None
    fill_clearance: float = 1.0
    cheese: Pattern | None = None
    cheese_block: Layer | None = None
    width_trigger: float = 5.0
    edge_clearance: float = 1.0
    via_clearance: float = 0.5
    via_layers: tuple[Layer, ...] = ()
    diam_clearance: float | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Every layer recipe needs a name")
        for attr in ("layer", "fill_block", "cheese_block"):
            value = getattr(self, attr)
            if value is not None:
                object.__setattr__(self, attr, _pair(value, attr, layer=True))
        if self.diam_clearance is not None:
            _number(self.diam_clearance, "DIAM clearance")
        for attr in ("fill_clearance", "edge_clearance", "via_clearance", "width_trigger"):
            _number(getattr(self, attr), attr, positive=attr == "width_trigger")
        object.__setattr__(self, "via_layers", tuple(_pair(v, "via layer", layer=True) for v in self.via_layers))
        for attr in ("fill", "cheese"):
            if getattr(self, attr) is not None and not isinstance(getattr(self, attr), Pattern):
                raise ValueError(f"{attr} must be a Pattern or None")
        if self.layer in (self.fill_block, self.cheese_block) or self.layer in self.via_layers:
            raise ValueError("A physical layer cannot also be its own blocker or via layer")


def _diam_rules():
    """Process minima come from the canonical rule reference, never a caller profile."""
    data = read_settings("aim_diam_rules.json")
    if data.get("schema_version") != 1 or not isinstance(data.get("rules"), list):
        raise ValueError("Malformed AIM DIAM rule reference")
    layer = _pair(data["diam_layer"], "DIAM layer", layer=True)
    rules = {}
    for rule in data["rules"]:
        target = _pair(rule["layer"], "DIAM rule layer", layer=True)
        minimum = rule["minimum_clearance"]
        if minimum is not None:
            _number(minimum, "DIAM rule minimum")
        if target in rules:
            raise ValueError("Duplicate DIAM rule layer")
        rules[target] = rule
    return data, layer, rules


def _diam_clearance(recipe, rules):
    rule = rules.get(recipe.layer, {})
    minimum = rule.get("minimum_clearance") or 0.0
    distance = minimum if recipe.diam_clearance is None else recipe.diam_clearance
    if distance < minimum:
        raise ValueError(f"{recipe.name} DIAM clearance {distance:g} um is below "
                         f"{rule['rule']} minimum {minimum:g} um")
    return distance


@dataclass(frozen=True)
class AreaKeepout:
    """Exclude one source layer plus its own margin from both operations."""
    layer: Layer
    clearance: float = 0.0

    def __post_init__(self):
        object.__setattr__(self, "layer", _pair(self.layer, "keepout layer", layer=True))
        _number(self.clearance, "keepout clearance")


@dataclass(frozen=True)
class FinishConfig:
    layers: tuple[LayerRecipe, ...]
    waveguide_layers: tuple[Layer, ...] = ((709, 727), (702, 727), (733, 727), (735, 727))
    waveguide_keepout: float = 7.0
    # Reuse the canonical profile for generic constructors and legacy omissions.
    global_fill_keepouts: tuple[Layer, ...] = field(default_factory=lambda:
        tuple(read_settings("aim_fill_cheese.json")["global_fill_keepouts"]))
    global_keepouts: tuple[Layer, ...] = field(default_factory=lambda:
        tuple(read_settings("aim_fill_cheese.json")["global_keepouts"]))
    global_keepout_clearance: float = 0.0
    max_candidates: int = 2_000_000
    schema_version: int = 1
    layer_keepouts: tuple[AreaKeepout, ...] = ()
    keepout_tolerance: float = 144
    # Device markers in global_keepouts share the waveguide clearance.
    device_layers: tuple[Layer, ...] = field(default_factory=lambda:
        tuple(read_settings("aim_fill_cheese.json")["device_layers"]))

    def __post_init__(self):
        object.__setattr__(self, "layers", tuple(self.layers))
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported finishing config schema")
        if not self.layers or any(not isinstance(v, LayerRecipe) for v in self.layers):
            raise ValueError("At least one LayerRecipe is required")
        if len({v.layer for v in self.layers}) != len(self.layers) or len({v.name for v in self.layers}) != len(self.layers):
            raise ValueError("Layer recipes must have unique physical layers and names")
        _, diam_layer, diam_rules = _diam_rules()
        object.__setattr__(self, "layers", tuple(
            replace(v, diam_clearance=_diam_clearance(v, diam_rules)) for v in self.layers))
        physical = {v.layer for v in self.layers}
        if diam_layer in physical:
            raise ValueError("DIAM cannot also be a physical recipe layer")
        _number(self.keepout_tolerance, "keepout tolerance", positive=True)
        if self.keepout_tolerance < 8:
            raise ValueError("Round keepout tolerance must be at least 8")
        for rule in self.layers:
            if physical.intersection([v for v in [rule.fill_block, rule.cheese_block, *rule.via_layers] if v is not None]):
                raise ValueError("A recipe's blocker/via layer collides with a configured physical layer")
        for attr in ("waveguide_layers", "global_fill_keepouts", "global_keepouts", "device_layers"):
            object.__setattr__(self, attr, tuple(_pair(v, attr, layer=True) for v in getattr(self, attr)))
        if physical.intersection(self.global_fill_keepouts):
            raise ValueError("Global fill blockers cannot also be physical recipe layers")
        if physical.intersection(self.global_keepouts):
            raise ValueError("Global keepouts cannot also be physical recipe layers")
        object.__setattr__(self, "layer_keepouts", tuple(self.layer_keepouts))
        if any(not isinstance(v, AreaKeepout) for v in self.layer_keepouts):
            raise ValueError("layer_keepouts must contain AreaKeepout values")
        if physical.intersection(v.layer for v in self.layer_keepouts):
            raise ValueError("Keepout layers cannot also be physical recipe layers")
        _number(self.global_keepout_clearance, "global keepout clearance")
        _number(self.waveguide_keepout, "waveguide keepout")
        if isinstance(self.max_candidates, bool) or not isinstance(self.max_candidates, int) or self.max_candidates < 1:
            raise ValueError("max_candidates must be a positive integer")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, obj):
        if not isinstance(obj, dict):
            raise ValueError("Finishing config must be an object")
        def checked(kind, raw):
            if not isinstance(raw, dict) or set(raw) - {f.name for f in fields(kind)}:
                raise ValueError(f"Unknown or malformed {kind.__name__} configuration")
            return dict(raw)
        data = checked(cls, obj)
        try:
            recipes = []
            for raw in data.get("layers", []):
                raw = checked(LayerRecipe, raw)
                for key in ("fill", "cheese"):
                    if raw.get(key) is not None:
                        raw[key] = Pattern(**checked(Pattern, raw[key]))
                recipes.append(LayerRecipe(**raw))
            data["layers"] = tuple(recipes)
            data["layer_keepouts"] = tuple(
                AreaKeepout(**checked(AreaKeepout, raw)) for raw in data.get("layer_keepouts", []))
            return cls(**data)
        except TypeError as exc:
            raise ValueError(f"Incomplete finishing configuration: {exc}") from exc


def load_fill_cheese_config(path=None) -> FinishConfig:
    """Load the single editable profile used by Studio and the CLI."""
    path = Path(path) if path is not None else CONFIG_DIR / "aim_fill_cheese.json"
    return FinishConfig.from_dict(json.loads(path.read_text()))


def _region(cell, layout, layer):
    index = layout.find_layer(*layer)
    return kdb.Region() if index is None else kdb.Region(cell.begin_shapes_rec(index)).merged()


def _units(value, dbu):
    return math.ceil(value / dbu - 1e-10)


def _shape(kind, x, y, width, length, dbu):
    if kind == "square":
        return kdb.DPolygon(kdb.DBox(x-width/2, y-width/2, x+width/2, y+width/2)).to_itype(dbu)
    if kind == "circle":
        return kdb.DPolygon.ellipse(kdb.DBox(x-width/2, y-width/2, x+width/2, y+width/2), 32).to_itype(dbu)
    radius = width / 2
    half_line = (length-width)/2
    points = []
    for angle in [i * math.pi/16 for i in range(17)]:
        points.append(kdb.DPoint(x+radius*math.cos(angle), y+half_line+radius*math.sin(angle)))
    for angle in [math.pi+i*math.pi/16 for i in range(17)]:
        points.append(kdb.DPoint(x+radius*math.cos(angle), y-half_line+radius*math.sin(angle)))
    return kdb.DPolygon(points).to_itype(dbu)


def _candidates(pattern, bounds, dbu, budget):
    if pattern.rotation_deg == 90:
        # Generate in the motif frame, then rotate the complete dot/capsule lattice.
        # Phase remains an offset in GDS coordinates; pitch belongs to the motif frame.
        x0, y0, x1, y1 = bounds
        ox, oy = pattern.phase
        local = replace(pattern, rotation_deg=0, phase=(oy, -ox))
        region, count = _candidates(local, (y0, -x1, y1, -x0), dbu, budget)
        return region.transformed(kdb.Trans(1, False, 0, 0)), count
    if max(abs(v) for v in (*pattern.pitch, *pattern.phase, pattern.width, pattern.length))/dbu >= 2**29:
        raise ValueError("Pattern parameters exceed the supported GDS coordinate range")
    if pattern.width < 4*dbu or min(pattern.pitch)-pattern.width < 2*dbu:
        raise ValueError("Pattern is too fine for the input GDS database unit")
    x0, y0, x1, y1 = bounds
    px, py = pattern.pitch
    motif = pattern.kind == "dots_capsules"
    tx, ty = (4*px, 6*py) if motif else (px, py)
    ox, oy = pattern.phase
    # Include one extra cell at each edge; whole-shape selection clips the lattice.
    ix0, ix1 = math.floor((x0-ox)/tx)-1, math.ceil((x1-ox)/tx)+1
    iy0, iy1 = math.floor((y0-oy)/ty)-1, math.ceil((y1-oy)/ty)+1
    count = (ix1-ix0+1)*(iy1-iy0+1)*(16 if motif else 1)
    if count > budget:
        raise ValueError(f"Candidate budget exceeded ({count} > {budget}); reduce the ROI or use a coarser profile")
    region = kdb.Region()
    for j in range(iy0, iy1+1):
        for i in range(ix0, ix1+1):
            x, y = ox+i*tx, oy+j*ty
            if motif:
                for col in range(4):
                    center = 1 if col % 2 == 0 else 4
                    region.insert(_shape("capsule", x+col*px, y+center*py,
                                         pattern.width, pattern.length, dbu))
                    for row in (range(3, 6) if center == 1 else range(3)):
                        region.insert(_shape("circle", x+col*px, y+row*py,
                                             pattern.width, 0, dbu))
            else:
                x += tx/2 if pattern.stagger and j % 2 else 0
                region.insert(_shape(pattern.kind, x, y, pattern.width, pattern.length, dbu))
    return region, count


def finish_regions(selected, *, bounds=None, config=None, progress=None):
    """Shared geometry engine for in-memory components and selected GDS cells."""
    config = config if config is not None else load_fill_cheese_config()
    if not isinstance(config, FinishConfig):
        raise ValueError("config must be a FinishConfig")
    layout = selected.layout()
    bounds_source = "layout_bbox" if bounds is None else "explicit"
    if bounds is None:
        box = selected.dbbox()
        if box.empty():
            raise ValueError("Layout bbox is empty; supply explicit bounds")
        bounds = (box.left, box.bottom, box.right, box.top)
    if not isinstance(bounds, (tuple, list)) or len(bounds) != 4:
        raise ValueError("Bounds must contain four finite coordinates")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bounds):
        raise ValueError("Bounds must contain four finite coordinates")
    x0, y0, x1, y1 = bounds
    if x0 >= x1 or y0 >= y1:
        raise ValueError("Bounds must have positive width and height")
    dbu = layout.dbu
    if max(abs(v)/dbu for v in bounds) >= 2**30:
        raise ValueError("ROI exceeds the supported GDS coordinate range")
    roi = kdb.Region(kdb.DBox(*bounds).to_itype(dbu))
    if roi.area() == 0:
        raise ValueError("ROI collapses on the input database grid")
    snapshots = {}
    def original(layer):
        if layer not in snapshots:
            snapshots[layer] = _region(selected, layout, layer)
        return snapshots[layer]
    waveguides = kdb.Region()
    for layer in config.waveguide_layers:
        waveguides += original(layer)
    def offset(region, distance):
        return offset_region(region, distance, dbu, tolerance=config.keepout_tolerance,
                             conservative=True)
    if progress:
        progress("Fill / cheese: preparing original waveguide clearance")
    wave_keepout = offset(waveguides.merged(), config.waveguide_keepout)
    if progress:
        progress("Fill / cheese: preparing dicing and undercut clearance")
    excluded, devices = kdb.Region(), kdb.Region()
    for layer in config.global_keepouts:
        if layer in config.device_layers:
            devices |= original(layer)
        else:
            excluded |= original(layer)
    excluded = offset(excluded, config.global_keepout_clearance)
    device_distance = max(config.waveguide_keepout, config.global_keepout_clearance)
    excluded |= offset(devices, device_distance)
    for keepout in config.layer_keepouts:
        excluded |= offset(original(keepout.layer), keepout.clearance)
    global_keepout = wave_keepout | excluded
    for layer in config.global_fill_keepouts:
        global_keepout = global_keepout | original(layer)
    diam_reference, diam_layer, diam_rules = _diam_rules()
    diam_cache = {}
    report = []
    used_candidates = 0
    expected = {}
    candidate_cache = {}
    def candidates_for(pattern, remaining):
        # Aligned waveguide/metal groups reuse the exact lattice, without
        # charging fewer candidates or changing per-layer whole-shape selection.
        if pattern not in candidate_cache:
            candidate_cache[pattern] = _candidates(pattern, bounds, dbu, remaining)
        candidates, count = candidate_cache[pattern]
        if count > remaining:
            raise ValueError(f"Candidate budget exceeded ({count} > {remaining}); reduce the ROI or use a coarser profile")
        return candidates, count
    for recipe in config.layers:
        if progress:
            progress(f"Fill / cheese: processing {recipe.name}")
        old = original(recipe.layer)
        # DIAM's per-target process floor is independent of user/global KO lists.
        diam_distance = max(_diam_clearance(recipe, diam_rules),
            config.global_keepout_clearance if diam_layer in config.global_keepouts else 0,
            max((v.clearance for v in config.layer_keepouts if v.layer == diam_layer), default=0))
        if diam_distance not in diam_cache:
            diam_cache[diam_distance] = offset(original(diam_layer), diam_distance)
        diam_keepout = diam_cache[diam_distance]
        recipe_excluded = excluded | diam_keepout
        fill, holes = kdb.Region(), kdb.Region()
        if recipe.fill:
            candidates, count = candidates_for(recipe.fill, config.max_candidates-used_candidates)
            used_candidates += count
            blocked = offset(old, recipe.fill_clearance) | global_keepout | diam_keepout
            if recipe.fill_block is not None:
                blocked |= original(recipe.fill_block)
            fill = candidates.inside(roi - blocked).merged()
        if recipe.cheese and not old.is_empty():
            candidates, count = candidates_for(recipe.cheese, config.max_candidates-used_candidates)
            used_candidates += count
            # Morphological width proxy. +1 dbu makes the stated trigger strict.
            radius = _units(recipe.width_trigger/2, dbu)+1
            wide = old.sized(-radius).sized(radius)
            safe = offset(old, -recipe.edge_clearance) & wide & (roi - recipe_excluded)
            if recipe.cheese_block is not None:
                safe -= original(recipe.cheese_block)
            for layer in recipe.via_layers:
                safe -= offset(original(layer), recipe.via_clearance)
            holes = candidates.inside(safe).merged()
        new = (old - holes) | fill
        expected[recipe.layer] = new
        report.append({"name": recipe.name, "layer": list(recipe.layer),
            "diam_clearance_um": diam_distance,
            "diam_rule": diam_rules.get(recipe.layer, {}).get("rule"),
            "fill_shapes": fill.size(), "cheese_holes": holes.size(),
            "fill_area_um2": fill.area()*dbu**2, "removed_area_um2": holes.area()*dbu**2,
            "before_roi_fraction": (old & roi).area()/roi.area(),
            "after_roi_fraction": (new & roi).area()/roi.area()})
        if progress:
            progress(f"Fill / cheese {recipe.name}: added {fill.size()} shapes, cut {holes.size()} holes")
    manifest = {"schema_version": 1, "purpose": "photo-informed visual preview",
        "foundry_signoff": False, "input_cell": selected.name,
        "dbu_um": dbu, "bounds_um": list(bounds),
        "bounds_source": bounds_source,
        "hierarchy": "regions from original transformed input; source untouched",
        "config": config.to_dict(), "candidate_count": used_candidates, "layers": report,
        "keepout_join": "round", "device_keepout_um": device_distance,
        "diam_rule_source": diam_reference["source"],
        "limits": ["No foundry density closure, connectivity signoff, or mask preparation equivalence.",
                   "Photo color does not uniquely identify buried layers; phases and cheese recipes are hypotheses.",
                   "Waveguide keepout defaults to7um; the source guide also states6um.",
                   "Contact fill and custom process layers are omitted from the default profile.",
                   "Width trigger uses an axis-aligned morphological approximation, not an exact Euclidean width test."]}
    return expected, manifest
