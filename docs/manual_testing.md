# Manual testing checklist

## Customizer dialog smoke test

1. Launch the desktop shell (`hatch run three-dfs`).
2. Select a parametric source asset such as `docs/examples/openscad/demo_parametric_bracket.scad`.
3. Confirm the preview pane shows the customization summary with a **Customize…** button.
4. Click **Customize…** to open the dialog and adjust a parameter (for example change the `segments` slider).
5. Trigger the build and verify that a success message appears summarising the generated artefact count.
6. Back in the preview pane ensure the derivatives list reflects the newly created output and the tag sidebar shows the derivative entry.
