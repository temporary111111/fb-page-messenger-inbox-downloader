$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPidFile = Join-Path $PSScriptRoot '.archive-service.pid'
[System.IO.File]::WriteAllText($taskPidFile, $PID.ToString())
try {
    python -m inbox_archive serve
    exit $LASTEXITCODE
}
finally {
    Remove-Item -LiteralPath $taskPidFile -Force -ErrorAction SilentlyContinue
}
