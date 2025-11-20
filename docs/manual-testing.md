# Manual testing checklist

Use this checklist alongside the automated test suite when validating releases. The scenarios complement the [user guide](user-guide.md) and focus on end-to-end smoke tests across the desktop shell.

## Customizer dialog smoke test

1. Launch the desktop shell (`hatch run three-dfs`).
2. Select a parametric source asset such as `docs/examples/openscad/demo_parametric_bracket.scad`.
3. Confirm the preview pane shows the customization summary with a **Customize…** button.
4. Click **Customize…** to open the dialog and adjust a parameter (for example change the `segments` slider).
5. Trigger the build and verify that a success message appears summarising the generated artefact count.
6. Back in the preview pane ensure the derivatives list reflects the newly created output.

## Project workspace tidy-up

1. From the main window choose a project that already contains a few attachments.
2. Create a temporary text file inside the project folder (for example `touch notes_tmp.txt`).
3. Trigger **Refresh** and confirm the new file appears under the **Uploaded Files** header.
4. Delete the file from disk manually (`rm notes_tmp.txt`) to simulate a missing attachment.
5. In the project pane invoke the context menu on the stale entry and choose **Delete File…**.
6. Verify the confirmation dialog mentions that the file is already missing but will be removed from the library, then accept the deletion.
7. Confirm the item disappears immediately and the subsequent refresh does not reintroduce it (metadata was purged).

## Rename and removal

1. Right-click a project or container in the repository sidebar.

## Link container workflow

1. Open any container (project or container) in the main window and click **Link Container** in the project pane toolbar.
2. Pick a target container from the dialog. The newly created link should appear under **Linked Containers** and focus automatically.
3. Select the new link and confirm the application switches to the linked container immediately.
4. Press **Back** in the project pane to return to the previous container and verify the history restores the earlier view.
5. Re-open the container folder on disk and verify no extra helper files were created—the link is tracked purely in the library.
6. Remove the link via the context menu and confirm it disappears immediately without deleting the target container.

## Import from linked container

1. Ensure the active container has at least one linked container (see workflow above).
2. In the **Components** list open the context menu and choose **Import From Linked Container…**.
3. In the tree dialog pick a linked container/component combination and confirm it appears back in the list with the italic "linked" styling.
4. Double-click the new entry to verify the original model opens even though it lives in the remote container.
5. Delete the linked component via the context menu and confirm the confirmation text explains that only the reference will be removed.
6. Refresh the container and verify the linked component remains removed while the original file inside the source container is untouched.

## G-code machine tagging

1. With a `.gcode`/`.gco` file selected in the preview pane, confirm the file opens in the **Text** tab and the machine tag list appears.
2. Click **Machine Tags…**, add a new `Machine:<ID>` tag, and ensure it now appears in both the list and the preview summary.
3. Rename the newly added tag through the dialog, close the dialog, and verify the preview reflects the updated name.
4. Click the machine tag link in the preview and confirm the repository list filters down to containers carrying that tag (search box shows `#Machine:<ID>`).
5. Clear the search field and verify the repository list repopulates.
6. Re-open the dialog, remove the tag, and confirm the preview reverts to showing no machine tags.

## G-code preview rendering

1. Select a G-code asset and verify the **Thumbnail**, **Toolpath**, and **Text** tabs are enabled. Interact with the toolpath preview (orbit/pan/zoom) to confirm the 3D reconstruction renders without errors.
2. Add hint tags such as `GCodeHint:tool=EndMill`, `GCodeHint:workpiece=120x80`, or `GCodeHint:cut_color=#00ff88` and request a refresh; confirm both the thumbnail and toolpath previews update with the new annotations and colours.
3. Remove the hint tags and ensure the previews revert to the default colour scheme on the next refresh.
4. Trigger **Capture View** on a different asset, return to the G-code file, and confirm the cached preview remains available without re-rendering delays.

## PDF preview rendering

1. Select a managed PDF file that contains at least one page.
2. Confirm the **Thumbnail** tab shows a rasterised first-page preview and that the metadata lists the PDF type, page count, and page dimensions.
3. Remove or disable the QtPdf plug-in (or launch a build without it) and reopen the same PDF to verify the preview pane reports that PDF rendering is unavailable instead of crashing.
