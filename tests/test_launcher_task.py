"""Validate the actual PowerShell task guard without registering/running a task."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'Game-LauncherTask.ps1'
POWERSHELL = shutil.which('powershell.exe')


@unittest.skipUnless(POWERSHELL, 'Windows PowerShell required')
class LauncherTaskGuardTests(unittest.TestCase):
    def check_task(self, mutation='', user_expression='$sid'):
        # Load only the guard's AST, never the script's installation/launch body.
        code = r'''
$ErrorActionPreference='Stop'
$scriptPath=SCRIPT_PATH
$ast=[Management.Automation.Language.Parser]::ParseFile($scriptPath,[ref]$null,[ref]$null)
$function=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-OwnedTask'},$true)[0]
Invoke-Expression $function.Extent.Text
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$taskName='NarakaCheckinAssistant-GameLauncher'
$launcher='E:\Naraka\tool\LootHoarder.exe'
$workingDirectory='E:\Naraka'
$description='guard fixture'
$fixture=[pscustomobject]@{
 TaskName=$taskName; Description=$description
 Actions=@([pscustomobject]@{Execute=$launcher;Arguments=$null;WorkingDirectory=$workingDirectory})
 Principal=[pscustomobject]@{UserId=USER_EXPRESSION;LogonType='Interactive';RunLevel='Highest'}
 Triggers=$null
}
function Get-ScheduledTask { param($TaskPath,$ErrorAction) return $fixture }
MUTATION
try { $result=Get-OwnedTask; @{accepted=[bool]$result} | ConvertTo-Json -Compress }
catch { @{accepted=$false;error=$_.Exception.Message} | ConvertTo-Json -Compress }
'''
        code = code.replace('SCRIPT_PATH', "'" + str(SCRIPT).replace("'", "''") + "'")
        code = code.replace('USER_EXPRESSION', user_expression).replace('MUTATION', mutation)
        import base64
        encoded = base64.b64encode(code.encode('utf-16le')).decode('ascii')
        completed = subprocess.run(
            [POWERSHELL, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
            capture_output=True, timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout.decode('utf-8-sig'))['accepted']

    def test_accepts_sid_and_null_trigger_collection(self):
        self.assertTrue(self.check_task())

    def test_accepts_windows_normalized_account_name(self):
        self.assertTrue(self.check_task(user_expression='[Security.Principal.WindowsIdentity]::GetCurrent().Name'))

    def test_rejects_changed_action_or_arguments(self):
        self.assertFalse(self.check_task("$fixture.Actions[0].Execute='powershell.exe'"))
        self.assertFalse(self.check_task("$fixture.Actions[0].Arguments='--unexpected'"))

    def test_rejects_another_user_or_noninteractive_principal(self):
        self.assertFalse(self.check_task("$fixture.Principal.UserId='S-1-5-18'"))
        self.assertFalse(self.check_task("$fixture.Principal.LogonType='ServiceAccount'"))

    def test_rejects_trigger_or_different_working_directory(self):
        self.assertFalse(self.check_task("$fixture.Triggers=@([pscustomobject]@{Enabled=$true})"))
        self.assertFalse(self.check_task("$fixture.Actions[0].WorkingDirectory='C:\\Windows'"))
