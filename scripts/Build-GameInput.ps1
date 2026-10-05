$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$native = Join-Path $project 'native'
$output = Join-Path $native 'dist'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$arguments = @('/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/reference:System.Drawing.dll', '/reference:System.Web.Extensions.dll')
$arguments += '/out:' + (Join-Path $output 'GameInputBroker.exe')
foreach ($template in @('launcher_start', 'announcement', 'saved_account', 'title_enter', 'dismiss_monthly', 'exit_lobby', 'lobby_nav', 'lobby_nav_current', 'dismiss_promotion', 'promotion_notice')) {
    $arguments += '/resource:' + (Join-Path $native "templates\$template.png") + ',' + $template + '.png'
}
$arguments += Join-Path $native 'GameInputBroker.cs'
& $compiler @arguments
if ($LASTEXITCODE -ne 0) { throw 'Game input broker build failed.' }
Get-FileHash -LiteralPath (Join-Path $output 'GameInputBroker.exe') -Algorithm SHA256
