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
* `plan_build(path, schema, values, *, output_dir, ...)`
  prepares a `CustomizerSession` including the command line invocation required
  to produce managed artefacts.  The resulting session is consumed by the
  `three_dfs.customizer.execute_customization` pipeline which executes the
  backend, copies generated files into managed storage, and persists the
  resulting assets and metadata.

A session captures the normalised parameter values, the command arguments
required to run the build, and the artefacts that will be produced.  The
pipeline records derivative relationships between the base asset and each
generated artefact, storing preview hints where available so subsequent UI
flows can display backend-provided imagery without re-rendering thumbnails.

## OpenSCAD backend

`three_dfs.customizer.openscad.OpenSCADBackend` demonstrates how a backend can
interpret tool-specific annotations.  It understands OpenSCAD customiser
comments such as `[1:10]` sliders and option lists, maps them into parameter
metadata, and constructs an `openscad` CLI invocation with the appropriate
`-D` overrides.  The backend focuses on describing the build; persistence is
handled by the pipeline which records the parameter schema, normalised values,
and all produced artefacts.  See the [transformation helpers](customizer-transformations.md)
for reusable mesh operations that backends can compose into build plans.

Practical example sources live in `docs/examples/openscad/`.  They include a
utility for embossing arbitrary STL files (`emboss_utility.scad`) and a
demonstration corner bracket (`demo_parametric_bracket.scad`) that highlights a
variety of parameter types available to the customizer.

## Desktop workflow

The desktop shell now surfaces customization metadata directly within the
preview pane. Selecting a supported source file (for example an OpenSCAD
script) enables the **Customize…** action and presents a summary panel that
lists recent derivative artefacts along with the parameters that produced
them.  The summary includes quick-launch buttons so you can jump to generated
assets or reopen the customizer dialog pre-populated with the previous
settings.

Launching the dialog instantiates the embedded `CustomizerPanel` inside a
modal window, preserving the familiar sliders and toggles while delegating run
execution to `execute_customization`.  Successful runs automatically refresh
the preview summary, keeping related artefacts one click away without
requiring a manual library rescan.

## Adapting additional engines

Extending the system to support another parametric modelling engine—such as a
Build123D-based workflow—only requires translating that tool's metadata into a
`ParameterSchema` and implementing the `plan_build` hook to express how the
engine should be executed.  The resulting `CustomizerSession` integrates with
the shared execution pipeline, enabling consistent storage and reproducibility
across all backends.

Backends that rely on auxiliary resources (for example Build123D Python
scripts) can declare these dependencies by returning `GeneratedArtifact`
entries with the `asset_id` field populated.  The pipeline links such artifacts
to the customization without copying or mutating the original asset metadata,
allowing reproducible builds while keeping script sources managed alongside
their owning library entries.
