$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPidFile = Join-Path $PSScriptRoot '.archive-service.pid'
if (-not (Test-Path -LiteralPath $taskPidFile)) {
    Write-Host 'The background archive service is not running.'
    exit 0
}
$taskPidText = [System.IO.File]::ReadAllText($taskPidFile).Trim()
if ($taskPidText -notmatch '^\d+$') {
    Remove-Item -LiteralPath $taskPidFile -Force
    throw 'The archive service PID file was invalid and has been removed.'
}
$taskProcess = Get-Process -Id ([int]$taskPidText) -ErrorAction SilentlyContinue
if (-not $taskProcess) {
    Remove-Item -LiteralPath $taskPidFile -Force
    Write-Host 'The archive service was already stopped.'
    exit 0
}
if ($taskProcess.ProcessName -notin @('powershell', 'pwsh')) {
    throw 'Refusing to stop an unexpected process. Remove the stale PID file only after checking it.'
}
Stop-Process -Id $taskProcess.Id
Remove-Item -LiteralPath $taskPidFile -Force -ErrorAction SilentlyContinue
Write-Host 'The background archive service was stopped.'
