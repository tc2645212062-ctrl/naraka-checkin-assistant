param([string]$Python, [string]$DataDir)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $project '.venv\Scripts\pythonw.exe' }
if ((Split-Path -Leaf $Python) -eq 'python.exe') {
    $windowless = Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
    if (Test-Path -LiteralPath $windowless) { $Python = $windowless }
}
if (-not (Test-Path -LiteralPath $Python)) { throw 'Python runtime missing; run Setup.ps1 first.' }
$startup = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup 'NarakaCheckinAssistant.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = (Resolve-Path -LiteralPath $Python).Path
$shortcut.WorkingDirectory = $project
$shortcut.Arguments = '"' + (Join-Path $project 'app.py') + '" --minimized'
if ($DataDir) { $shortcut.Arguments += ' --data-dir "' + $DataDir + '"' }
$shortcut.Description = 'Naraka local automatic daily check-in'
$shortcut.Save()
Write-Output $shortcutPath
