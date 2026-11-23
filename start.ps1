$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m inbox_archive serve
exit $LASTEXITCODE
