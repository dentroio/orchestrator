# Windows Runner Installer

This package installs `windows_runner_agent.ps1` on a Windows host and registers a startup Scheduled Task so it reconnects to the orchestrator automatically.

## Build package

From repository root:

```bash
bash lab/orchestrator/app/windows-installer/build_windows_runner_package.sh
```

Output:

- `lab/orchestrator/app/windows-installer/dist/windows-runner-installer/`
- optional zip: `lab/orchestrator/app/windows-installer/dist/windows-runner-installer.zip`

## Install on Windows host

1. Copy the installer bundle to the Windows machine.
2. Open an elevated Command Prompt or PowerShell.
3. Run:

```cmd
install_windows_runner.cmd
```

4. Answer prompts for:
   - Orchestrator URL (e.g., `http://192.168.20.95:5000`)
   - Runner ID (must match configured runner in dashboard)
   - Fallback persona

The installer creates and starts a Scheduled Task (`Clarion Windows Runner` by default) running as `SYSTEM`.
