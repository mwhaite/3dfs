# Customizer backends

The `three_dfs.customizer` package exposes a lightweight protocol for tools
that transform parameterised source files into build artefacts.  Backends
implement the `CustomizerBackend` protocol which defines three primary hooks:

* `load_schema(path) -> ParameterSchema` parses a source file and returns a
  serialisable schema made up of `ParameterDescriptor` entries describing each
  parameter exposed by the backend.
* `validate(schema, values) -> dict` normalises user supplied overrides using the
  schema information and raises `ValueError` when values fall outside the
  declared constraints.
* `plan_build(path, schema, values, *, output_dir, asset_service, ...)`
  prepares a `CustomizerSession` including the command line invocation required
  to produce managed artefacts.  The session metadata is persisted through the
  `AssetService.record_customization_session` helper so that workflows can be
  rehydrated, cloned, or inspected at a later date.

A session captures the normalised parameter values, the command arguments
required to run the build, and the artefacts that will be produced.  Each
`GeneratedArtifact` stores the managed asset identifier once persisted which
allows `AssetService.get_customization_session` to provide a fully hydrated
view of previous runs.

## OpenSCAD backend

`three_dfs.customizer.openscad.OpenSCADBackend` demonstrates how a backend can
interpret tool-specific annotations.  It understands OpenSCAD customiser
comments such as `[1:10]` sliders and option lists, maps them into parameter
metadata, and constructs an `openscad` CLI invocation with the appropriate
`-D` overrides.  The backend stores session metadata via the asset service so
repeat builds have access to both the parameter schema and the generated
outputs.

## Adapting additional engines

Extending the system to support another parametric modelling engine—such as a
Build123D-based workflow—only requires translating that tool's metadata into a
`ParameterSchema` and implementing the `plan_build` hook to express how the
engine should be executed.  The resulting `CustomizerSession` will integrate
with the existing persistence helpers, enabling consistent storage and
rehydration across all backends.
