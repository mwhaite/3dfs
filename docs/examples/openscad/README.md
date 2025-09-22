# OpenSCAD customizer examples

The files in this directory demonstrate how OpenSCAD sources can expose rich
parameters for the `three_dfs` customizer backend.  Each script uses annotated
assignments (e.g. `// [1:10]`) so the backend can extract slider ranges and
choice lists when building parameter schemas.

## `emboss_utility.scad`

A utility-focused helper that embosses text or an imported 2D design onto an
existing STL:

- Toggle between a generated rounded plate or an external STL specified by the
  `base_model` string parameter.
- Choose between raised and recessed embossing while positioning the artwork
  with X/Y offsets, rotation, and a configurable reference plane.
- Independently configure lettering and imported vector artwork, including font
  selection, mirroring, scaling, and thickness controls.

Example command line usage:

```bash
openscad -o embossed_plate.stl \
  -D 'base_model="path/to/base_plate.stl"' \
  -D text_content="Lab 42" \
  -D text_offset_x=20 \
  docs/examples/openscad/emboss_utility.scad
```

## `demo_parametric_bracket.scad`

A demonstration part that highlights a variety of customizer controls:

- Parametric leg dimensions with adjustable fillets and curve resolution.
- Switchable gusset styles (`solid`, `lightweight`, or `none`) with span and
  width controls.
- Configurable mounting holes, including safety margins and an optional second
  position shared across both bracket legs.

The generated bracket occupies the positive X/Y quadrant with the inside corner
at the origin, making it easy to position in downstream assemblies.
