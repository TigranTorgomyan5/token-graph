# Creates the "Token Graph Projects" desktop shortcut for the Project Explorer.
param([ValidateSet('claude', 'codex')][string]$Assistant = 'claude')
$skill = Join-Path $env:USERPROFILE ('.' + $Assistant + '\skills\token-graph')
$pyw = Join-Path $skill '.runtime\Scripts\pythonw.exe'
if (-not (Test-Path $pyw)) { Write-Error 'The runtime is missing: run scripts\setup_runtime.py first (see INSTALL-WINDOWS.md).'; exit 1 }
$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Token Graph Projects.lnk'
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = $pyw
$s.Arguments = '"' + (Join-Path $skill 'scripts\explorer_server.py') + '"'
$s.WorkingDirectory = $skill
$s.IconLocation = (Join-Path $skill 'assets\token-graph.ico') + ',0'
$s.Description = 'Token Graph: choose a project, build its graph, explore it'
$s.Save()
"Created $lnk"
