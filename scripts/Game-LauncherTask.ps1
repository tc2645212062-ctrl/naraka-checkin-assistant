param(
    [ValidateSet('Inspect', 'Install', 'Start', 'Remove')]
    [string]$Mode = 'Inspect',
    [string]$ExpectedSid = ''
)
$ErrorActionPreference = 'Stop'
$taskName = 'NarakaCheckinAssistant-GameLauncher'
$launcher = 'E:\Naraka\tool\LootHoarder.exe'
$workingDirectory = 'E:\Naraka'
$description = 'NarakaCheckinAssistant: on-demand official game launcher only; no daily trigger.'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $identity.User.Value
$isAdmin = ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$logDirectory = Join-Path $env:LOCALAPPDATA 'NarakaCheckinAssistant'
$logPath = Join-Path $logDirectory 'game-launcher-setup.json'

function Write-SetupResult([string]$Status, [string]$Detail) {
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $result = [ordered]@{ time = (Get-Date).ToString('o'); mode = $Mode; status = $Status; detail = $Detail; task = $taskName }
    $result | ConvertTo-Json | Set-Content -LiteralPath $logPath -Encoding UTF8
    $result | ConvertTo-Json | Write-Output
}

function Assert-OfficialLauncher {
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw "Launcher missing: $launcher" }
    $signature = Get-AuthenticodeSignature -LiteralPath $launcher
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O="?NetEase \(Hangzhou\) Network Co\., Ltd"?,') {
        throw 'Launcher must have a valid signature from the expected NetEase publisher.'
    }
}

function Get-OwnedTask {
    $task = Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object { $_.TaskName -eq $taskName }
    if (-not $task) { return $null }
    $actions = @($task.Actions)
    $taskSid = $task.Principal.UserId
    if ($taskSid -notmatch '^S-1-') {
        $taskSid = ([Security.Principal.NTAccount]::new($taskSid)).Translate([Security.Principal.SecurityIdentifier]).Value
    }
    if ($task.Description -ne $description -or $actions.Count -ne 1 -or
        $actions[0].Execute -ne $launcher -or $actions[0].Arguments -or
        $actions[0].WorkingDirectory -ne $workingDirectory -or
        $taskSid -ne $sid -or $task.Principal.LogonType.ToString() -ne 'Interactive' -or
        $task.Principal.RunLevel.ToString() -ne 'Highest' -or @($task.Triggers | Where-Object { $null -ne $_ }).Count -ne 0) {
        throw 'Existing task does not match this assistant; refusing to change or run it.'
    }
    return $task
}

function Assert-TaskPermissions {
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $sddl = $service.GetFolder('\').GetTask($taskName).GetSecurityDescriptor(4)
    $security = [Security.AccessControl.RawSecurityDescriptor]::new($sddl)
    if (-not $security.DiscretionaryAcl -or $security.DiscretionaryAcl.Count -eq 0) { throw 'Task permissions are missing.' }
    $canRun = $false
    foreach ($ace in $security.DiscretionaryAcl) {
        if ($ace.AceQualifier -ne [Security.AccessControl.AceQualifier]::AccessAllowed) { throw 'Unexpected task permission entry.' }
        $aceSid = $ace.SecurityIdentifier.Value
        if ($aceSid -in @('S-1-5-18', 'S-1-5-32-544')) { continue }
        # Task Scheduler maps generic read/execute to file read/execute rights.
        if ($aceSid -ne $sid -or ($ace.AccessMask -band (-bnot 0x1200a9)) -ne 0) { throw 'Task grants unexpected permissions.' }
        if (($ace.AccessMask -band 0x20) -ne 0) { $canRun = $true }
    }
    if (-not $canRun) { throw 'Current user cannot execute the task.' }
}

try {
    if ($ExpectedSid -and $ExpectedSid -ne $sid) { throw 'Use the same Windows account for administrator approval.' }
    if ($Mode -eq 'Inspect') {
        Assert-OfficialLauncher
        $task = Get-OwnedTask
        if ($task) { Assert-TaskPermissions }
        [ordered]@{ task = $taskName; installed = [bool]$task; launcher = $launcher;
            working_directory = $workingDirectory; user_sid = $sid; logon = 'Interactive';
            run_level = 'Highest'; triggers = @(); arguments = ''; signature = 'Valid';
            action = 'Start the official launcher only; this does not log in or claim points.' } | ConvertTo-Json
        exit 0
    }
    $existing = Get-OwnedTask
    if ($Mode -eq 'Install' -and $existing) {
        Assert-OfficialLauncher
        Assert-TaskPermissions
        Write-SetupResult 'installed' 'Existing task verified, including user read/run permissions. No new administrator approval needed.'
        exit 0
    }
    if (($Mode -eq 'Install' -or $Mode -eq 'Remove') -and -not $isAdmin) {
        # Windows handles the secure UAC prompt. No automation clicks it.
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Mode ' + $Mode + ' -ExpectedSid "' + $sid + '"'
        $child = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru
        $child.WaitForExit()
        if ($child.ExitCode -ne 0) {
            if (Test-Path -LiteralPath $logPath) { Get-Content -LiteralPath $logPath | Write-Output }
            exit $child.ExitCode
        }
        exit 0
    }
    if ($Mode -eq 'Remove') {
        if ($existing) { Unregister-ScheduledTask -TaskName $taskName -TaskPath '\' -Confirm:$false }
        Write-SetupResult 'removed' 'Launcher task removed; game files and check-in data were retained.'
        exit 0
    }
    Assert-OfficialLauncher
    if ($Mode -eq 'Install') {
        $action = New-ScheduledTaskAction -Execute $launcher -WorkingDirectory $workingDirectory
        $principal = New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
        $definition = New-ScheduledTask -Action $action -Principal $principal -Settings $settings -Description $description
        Register-ScheduledTask -TaskName $taskName -TaskPath '\' -InputObject $definition -Force | Out-Null
        # Only SYSTEM/admins can change the task; this Windows user can read/run it.
        $scheduler = New-Object -ComObject 'Schedule.Service'
        $scheduler.Connect()
        $registered = $scheduler.GetFolder('\').GetTask($taskName)
        $registered.SetSecurityDescriptor("D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGX;;;$sid)", 0)
        $verified = Get-OwnedTask
        if (-not $verified) { throw 'Task could not be read back after registration.' }
        Assert-TaskPermissions
        Write-SetupResult 'installed' 'Verified on-demand task installed. No game launched and no daily trigger added.'
        exit 0
    }
    if (-not $existing) { throw 'Launcher task is not installed. Run -Mode Install first.' }
    Assert-TaskPermissions
    Start-ScheduledTask -TaskName $taskName -TaskPath '\'
    Write-SetupResult 'launch_requested' 'Windows accepted the launch request. A visible launcher/game window must still be verified.'
} catch {
    Write-SetupResult 'failed' $_.Exception.Message
    exit 1
}
