# Customizer mesh transformations

The :mod:`three_dfs.customizer.transformations` module provides reusable
building blocks that backends can compose into their build plans.  Each
transformation is described by a lightweight descriptor object which exposes a
``to_dict`` method so plans can be persisted alongside other customization
metadata.  During execution the descriptors are applied to a source mesh using
``apply_transformations`` which relies on :mod:`build123d` for CAD operations
and emits both STL meshes and companion OpenSCAD snippets that mirror the
transformation pipeline.  Structured metadata such as bounding boxes and
component statistics are recorded alongside the exported assets.

## Available operations

The following descriptors are available for backends:

* ``ScaleTransformation`` – perform uniform or axis-aligned scaling.  Optional
  ``origin`` values allow scaling around a specific reference point.
* ``TranslateTransformation`` – offset the current mesh by an ``(x, y, z)``
  vector.
* ``EmbossMeshTransformation`` – load another mesh from disk, optionally scale
  and translate it, then emboss the result onto the base geometry.  Component
  statistics are captured alongside the combined bounding box.
* ``EmbossTextTransformation`` – generate 3D text with ``build123d`` primitives
  and emboss it onto the base mesh.  The helper centres extruded text around
  the origin so descriptors can position the lettering precisely while still
  emitting component metadata for storage.
* ``BooleanUnionTransformation`` – boolean union between the current mesh and
  one or more external meshes using ``build123d``'s solid modelling kernels.

All descriptors expose ``to_dict``/``from_dict`` helpers via
``serialise_descriptors`` and ``descriptor_from_dict`` so build plans can be
recorded within :class:`three_dfs.customizer.CustomizerSession` metadata.

## Example build plan payload

Backends can persist transformation pipelines as JSON payloads.  The following
example highlights a scale/translate sequence followed by text embossing and a
boolean union with an auxiliary mesh.  The snippet matches the structure
returned by ``serialise_descriptors``::

    {
      "operations": [
        {
          "operation": "scale",
          "factors": [1.25, 1.0, 0.8]
        },
        {
          "operation": "translate",
          "offset": [0.0, 0.0, 5.0]
        },
        {
          "operation": "emboss_text",
          "text": "Container 42",
          "height": 2.0,
          "depth": 0.4,
          "position": [10.0, 0.0, 6.2]
        },
        {
          "operation": "boolean_union",
          "mesh_paths": ["./fixtures/fasteners/logo.stl"]
        }
      ]
    }

When the plan executes ``apply_transformations`` returns metadata capturing each
operation's descriptor along with statistics for the transformed mesh:

* ``operations`` – ordered list of per-operation metadata including the
  original descriptor parameters and bounding boxes.
* ``bounding_box_min`` / ``bounding_box_max`` – extents for the final mesh.
* ``vertex_count`` / ``face_count`` – totals for the resulting geometry.
* ``backend`` – identifies the modelling engine used (``build123d``).
* ``openscad_script`` – a runnable OpenSCAD representation of the applied
  transformations, allowing downstream tooling to re-run the pipeline if
  desired.
* ``units`` – unit metadata derived from the source mesh when available.

Backends can store the metadata directly within the customization session so
the UI or downstream automation can reconstruct the applied steps and inspect
the resulting geometry without re-running the backend. Continue with the
[customizer backend guide](customizer-backends.md) for information about
defining parameter schemas and execution plans.
