Windows x64 offline installer for the standalone Updream MCP Bridge 0.3.1.

- Extract the Windows ZIP and double-click `install.bat`.
- Bundles verified Node.js and pinned production dependencies. No Node/Python setup or terminal commands are required for end users.
- Installs to a stable per-user directory, preserves unrelated Codex settings, verifies the MCP catalog, and opens a Chinese browser-install guide.
- Generates private pairing locally. Loading the provisioned extension automatically pairs it; no manual JSON import is needed.
- Browser-native extension loading and the site's enable/refresh step remain user actions. The installer never controls the mouse/keyboard or submits paid generation.
- Source is restored from the retained complete Bridge 0.3.0 snapshot. This release does not claim to restore the historical 0.4/0.6 Bridge source or change the root skill's version.

Download `updream-mcp-bridge-0.3.1-windows-x64.zip`, not GitHub's automatic source archives. Check `SHA256SUMS.txt` for integrity.

Automated verification covers isolated installation/reinstallation, configuration preservation, real MCP discovery and a simulated extension's authenticated handshake with the local daemon. Real-browser/site installation remains user-verified.
