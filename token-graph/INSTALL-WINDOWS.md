# Install Token Graph on Windows

Needs: Windows 10/11, Python 3.11 or newer, Claude Code or Codex, internet once during setup. Optional: VS Code or JetBrains Rider for "Open in editor".

1. **Python** — install from python.org, or in PowerShell: `winget install Python.Python.3.13`. Tick "Add Python to PATH". Open a new terminal afterwards.
2. **Copy the skill** — put this whole `token-graph` folder at `%USERPROFILE%\.claude\skills\token-graph\`.
3. **Install the parsers** (one time, downloads about 30 packages from PyPI into the skill's own `.runtime` folder, about 150 MB):

   ```powershell
   python "$env:USERPROFILE\.claude\skills\token-graph\scripts\setup_runtime.py" --download-deps --installer pip
   ```

   It ends with "Token Graph runtime ready". It does not install Graphify itself (the engine is bundled in `vendor\`).
4. **Desktop shortcut** for the Project Explorer:

   ```powershell
   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.claude\skills\token-graph\create-shortcut.ps1"
   ```

5. **Restart Claude Code.** `/token-graph` is now available in every project.
6. **Optional:** add the token-saving rules from `extras\CLAUDE-token-economy.md` to `%USERPROFILE%\.claude\CLAUDE.md`.

Then read `GUIDE.md`. This release includes Windows command-line compatibility changes in `scripts\offline_policy.py`. Native Windows testing is still needed; the automated release tests run on macOS/Python 3.14.

For Codex, use `%USERPROFILE%\.codex\skills\token-graph\` instead of the Claude path. The desktop shortcut helper accepts `-Assistant codex`.
