param([switch]$Remove, [string]$ExpectedSid = '', [string]$ExpectedHash = '')
$ErrorActionPreference = 'Stop'
$taskName = 'NarakaCheckinAssistant-GameInput'
$description = 'NarakaCheckinAssistant: bounded official game UI input broker; interactive, on demand.'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $identity.User.Value
$isAdmin = ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$folder = Join-Path $env:ProgramFiles 'NarakaCheckinAssistant'
$target = Join-Path $folder 'GameInputBroker.exe'
$source = Join-Path (Split-Path -Parent $PSScriptRoot) 'native\dist\GameInputBroker.exe'
$log = Join-Path $env:LOCALAPPDATA 'NarakaCheckinAssistant\game-input-setup.json'
try {
    if ($ExpectedSid -and $ExpectedSid -ne $sid) { throw 'Approve using the same Windows account.' }
    if (-not $Remove) {
        $hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
        if ($ExpectedHash -and $hash -ne $ExpectedHash) { throw 'Build changed during administrator approval; installation stopped.' }
    }
    if (-not $isAdmin) {
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -ExpectedSid "' + $sid + '"'
        if ($Remove) { $arguments += ' -Remove' } else { $arguments += ' -ExpectedHash ' + $hash }
        $child = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru
        $child.WaitForExit()
        if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log }
        exit $child.ExitCode
    }
    $existing = Get-ScheduledTask -TaskPath '\' | Where-Object { $_.TaskName -eq $taskName }
    if ($existing -and ($existing.Description -ne $description -or @($existing.Actions).Count -ne 1 -or $existing.Actions[0].Execute -ne $target)) {
        throw 'Existing task belongs to another application; refusing to replace it.'
    }
    if ($Remove) {
        if ($existing) { Stop-ScheduledTask -TaskName $taskName; Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
        $status = 'removed'
    } else {
        foreach ($path in @($env:ProgramFiles, $folder, $target)) {
            if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Reparse point in protected install path.' }
        }
        New-Item -ItemType Directory -Path $folder -Force | Out-Null
        $acl = New-Object Security.AccessControl.DirectorySecurity
        $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
        $acl.SetAccessRuleProtection($true,$false)
        foreach ($principal in @('S-1-5-18','S-1-5-32-544','S-1-5-32-545')) {
            $rights = if ($principal -eq 'S-1-5-32-545') { 'ReadAndExecute' } else { 'FullControl' }
            $rule = [Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($principal),[Security.AccessControl.FileSystemRights]$rights,[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
            $acl.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $folder -AclObject $acl
        if ($existing) { Stop-ScheduledTask -TaskName $taskName }
        Copy-Item -LiteralPath $source -Destination $target -Force
        $fileAcl = New-Object Security.AccessControl.FileSecurity
        $fileAcl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
        $fileAcl.SetAccessRuleProtection($true,$false)
        foreach ($filePrincipal in @('S-1-5-18','S-1-5-32-544','S-1-5-32-545')) {
            $fileRights = if ($filePrincipal -eq 'S-1-5-32-545') { 'ReadAndExecute' } else { 'FullControl' }
            $fileAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($filePrincipal),[Security.AccessControl.FileSystemRights]$fileRights,[Security.AccessControl.AccessControlType]::Allow))
        }
        Set-Acl -LiteralPath $target -AclObject $fileAcl
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $hash) { throw 'Installed binary hash mismatch.' }
        $action = New-ScheduledTaskAction -Execute $target -WorkingDirectory $folder
        $principal = New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 9)
        $definition = New-ScheduledTask -Action $action -Principal $principal -Settings $settings -Description $description
        Register-ScheduledTask -TaskName $taskName -TaskPath '\' -InputObject $definition -Force | Out-Null
        $service = New-Object -ComObject 'Schedule.Service'; $service.Connect()
        $service.GetFolder('\').GetTask($taskName).SetSecurityDescriptor("D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGX;;;$sid)",0)
        $verified = Get-ScheduledTask -TaskName $taskName
        if ($verified.Actions[0].Execute -ne $target -or $verified.Actions[0].Arguments -or $verified.Principal.RunLevel.ToString() -ne 'Highest' -or @($verified.Triggers | Where-Object { $null -ne $_ }).Count) { throw 'Installed task verification failed.' }
        $status = 'installed'
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $log) -Force | Out-Null
    @{status=$status;task=$taskName;path=$target;time=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $log -Encoding UTF8
    Get-Content -LiteralPath $log
} catch {
    New-Item -ItemType Directory -Path (Split-Path -Parent $log) -Force | Out-Null
    @{status='failed';detail=$_.Exception.Message;time=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $log -Encoding UTF8
    Get-Content -LiteralPath $log
    exit 1
}
