# User guide

The desktop shell presents a cohesive workflow for browsing managed assets, generating derivatives, and organising project containers. This guide highlights the major panes and actions you will use day to day.

## Repository explorer

The repository sidebar lists all containers tracked in the managed library. Use the search bar to filter by name or tag; typing `#tag` applies a tag filter immediately. Context menus expose shortcuts such as opening the container on disk, toggling the sidebar, or navigating linked containers.

Linked containers appear with version metadata when available. Selecting a link focuses the remote container and updates the rest of the UI to match. Use the Back button in the project pane to return to the previous context.

## Preview pane

Selecting an asset populates the preview pane with metadata and a set of tabs:

- **Thumbnail** – cached imagery generated from the managed asset. When a snapshot is missing the tab provides guidance on how to generate one.
- **3D Viewer** – OpenGL previewer for STL, OBJ, PLY, GLB/GLTF, and FBX meshes (FBX support requires the Autodesk FBX SDK). STEP assets fall back to bounding box previews when full meshes are unavailable.
- **Text** – renders arrangement scripts, README content, and other plaintext files directly in the UI.
- **Customizer** – available for assets backed by a parametric engine. Launches the embedded dialog to run the configured backend and review prior derivative runs.

Tabs disable themselves automatically when decoding fails; tooltips capture the failure reason so you can resolve missing codecs or oversized binaries.

## Project workspace

Projects open in a dedicated pane that groups the container’s components, attachments, arrangement scripts, outgoing links, and inbound **Linked Here** references. Containers are flat: every file and link lives at the root of the project directory. Filesystem watchers keep the pane in sync with on-disk changes so new files appear after a short debounce and stale metadata is removed automatically.

Use context menus to refresh metadata, import attachments, or open linked containers. Removing a linked component deletes only the metadata reference; the source container on disk remains untouched.

## Customization workflow

When an asset advertises a supported backend, the **Customizer** tab surfaces a summary of recent derivative artefacts along with the parameters that produced them. Launching **Customize…** opens the embedded dialog, pre-populated with the stored schema and previous values. Successful runs persist the derivative assets, refresh the summary, and keep the history accessible. Read more about the backend protocol and helper utilities in the [customization documentation](customizer-backends.md).

## Importing assets

The importer registers local files or remote identifiers and stores them in managed storage. Supported formats include `.stl`, `.obj`, `.step`, `.stp`, and CNC programs such as `.gcode`, `.gco`, `.g`, `.nc`, and `.ngc`. Metadata such as vertex counts, face counts, bounding boxes, and toolpath summaries are recorded automatically. Remote imports delegate to registered plugins; see [Extending 3dfs](extending.md#import-plugins) for implementation details.

## Tagging

Use the tag sidebar to create, rename, or remove tags scoped to the current container or file. Machine tags (`Machine:<ID>`) applied to G-code files display as clickable links in the preview pane. Clicking a tag applies a repository-wide filter matching the selected machine. Clear the search field to restore the full container list.

## G-code previews

Selecting a G-code asset enables both the **Thumbnail** and **Text** tabs. The thumbnail renders a 2D projection of the toolpath, highlighting rapid motions, cutting passes, the program origin, and recorded feed rates. Preview metadata summarises motion counts, travel distances, and axis bounds so you can assess a program without inspecting the raw commands.

Annotate files with `GCodeHint:<key>=<value>` tags to steer the renderer. Hints support tool names, materials, workpiece dimensions, and colour overrides—for example:

- `GCodeHint:tool=Ball Nose 6mm`
- `GCodeHint:workpiece=120x80`
- `GCodeHint:cut_color=#ff6600`

Hints persist in the preview metadata alongside the cached image, and cached renders refresh automatically when the program contents or hint tags change. Use **Machine Tags…** in the preview pane to manage machine identifiers without disturbing rendering hints.

## Troubleshooting tips

- Reset the metadata store by deleting `~/.3dfs/assets.sqlite3` while the application is closed.
- Enable demo data by setting `THREE_DFS_BOOTSTRAP_DEMO=1` before launching the UI.
- Review the [manual testing checklist](manual-testing.md) when validating new builds or verifying bug fixes.
