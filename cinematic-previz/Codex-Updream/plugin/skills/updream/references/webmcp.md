# WebMCP reads and recovery

Read the host's current browser and WebMCP documentation first. Bind the intended Updream tab in the current chat. The /projects page can answer a current-project read with no project selected. For a canvas, verify the returned project ID before using its nodes or making a change. If the snapshot already identifies the project, use it for that check and save a separate round trip. Successful discovery alone establishes only that the catalog was visible.

## Read through the current registration

1. Discover the capability and inspect the catalog and schemas returned by `await webmcp.fetchTools()` and `tools.description()`. A logical name such as `project_getCurrent` may have a registration suffix. Use the exact advertised name, including that suffix, with `await tools.call(name, input)`.
2. Fetch and invoke in the same host execution, without navigating or reusing a catalog from a previous execution. On a slow connection use a bounded budget for both steps, for example 120 seconds for one discovery and call. Allow for one more discovery and call if using the read retry below. Report the stage that failed if the host times out.
3. The bundled [read helper](../scripts/webmcp-read.mjs) handles current-project, project-list, and canvas-snapshot reads. With the documented `webmcp` capability in the host's Node execution tool, import `readUpdreamTool` from the helper's absolute installed path (use a file URL on Windows) and call:

   ```js
   const result = await readUpdreamTool(webmcp, 'project_getCurrent', {});
   nodeRepl.write(result);
   ```

   For other input, use the advertised schema. The helper returns the host result unchanged. Check for application errors and confirm project identity. Paginate a truncated snapshot before claiming that all nodes or connections have been read.

## Interpret errors before taking another action

- **Registration stale on a read.** Re-fetch once and resolve the name again. The helper does this only for the host's explicit stale-registration exception. If it fails again, report the failing tab and stage. When a separate read is needed, a temporary tab in this chat can open the same known canvas URL without refreshing the working tab. Verify the project ID and treat the result as evidence for the new tab only. Preserve the working tab and any pending writes.
- **Catalog or schema unavailable.** If the tab can be read, use the documented UI and inspect the visible project identity. A failed site-tool read does not require reinstalling Codex or pairing the independent Bridge.
- **Write or navigation interrupted.** Perform a fresh project or canvas read to establish the result. Treat a timeout or interruption as unknown until checked. Keep writes and navigation out of the read helper and do not apply its automatic retry to them.

## Canvas merge and layout

For an authorized cross-project merge, deliver a readable canvas as part of the merge. Prefer a suitable native operation when the catalog advertises one. Otherwise, use the canvas UI's copy and paste. Complete each batch before importing the next:

1. **Reserve a clear area before pasting.** Read the destination's current occupied areas and the source's node sizes and shot groups. Compute the batch's full footprint from the actual paste anchor, including title clearance. Choose a separate area or split the batch along existing shot groups if it would overlap the destination.
2. **Confirm the copy and read back the paste.** Confirm the full selection and copy count in the source before switching projects. Confirm the destination project and editable scope, paste once, then read back the added nodes, content, groups and links. After an interruption, establish what saved before trying again.
3. **Lay out and verify the batch.** Keep useful shot groups and relative order. Separate zones by segment and arrange script or notes, assets, and video packages in that order. Give titles and cards clearance and keep valid links. Check saved positions and inspect the canvas overview and a readable close view. A paste notice or matching count only verifies transfer progress. If later imports are blocked, continue arranging the batches already imported wherever the scope and outcome can be checked.
4. **Use the visible UI if native tools fail.** Move only confirmed nodes or groups. Before using the UI's automatic arrangement, verify what it will affect so it preserves the shot groups. Read back what the page exposes and report transfer, layout, content, links, and visual checks separately. Preserve sources whose content or references cannot yet be verified. Handle originals only after verification and within the user's authorization.
