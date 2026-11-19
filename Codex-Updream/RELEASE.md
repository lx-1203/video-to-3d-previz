Codex-native Updream plugin 0.3.5 with automatic Windows installation.

- Carries the canvas merge rules in the plugin. Reserve space before pasting, finish and verify the layout of each batch, and distinguish transfer from layout progress.

- Separates native Codex browser diagnostics from the independent Edge/Chrome Bridge. Bridge status is never a native-browser connection test.
- Reports a missing browser in the current chat without taking over another chat or automatically requesting extension pairing.
- Resolves current WebMCP names from a fresh catalog. Read-only checks retry once when the host reports that the registration went stale.
- Documents separate recovery for read failures and interrupted writes. The helper does not replay writes.
- Builds and verifies the installer ZIP for the version declared by the plugin manifest.

- Extract the Windows ZIP and double-click install.bat.
- The official Codex CLI registers the local marketplace, installs the plugin, and verifies installed/enabled/version state automatically.
- No Edge developer mode, extension-folder picker, pairing JSON, desktop input or extra Bridge daemon is required.
- Uses the host's supported built-in browser and current site tools; it does not add browser permissions or authentication credentials.
- Live website access requires a working browser connection and a successful read of the intended project. The installer smoke test verifies installation and reinstallation in an isolated Codex configuration.

Use the Windows x64 ZIP, not the automatically generated source archive. Codex desktop must already be installed; normal website login and host browser permissions still apply.
