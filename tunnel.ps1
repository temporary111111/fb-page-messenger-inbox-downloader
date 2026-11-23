param([int]$Port = 8787)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    throw 'cloudflared is not installed. See README.md for installation instructions.'
}
Write-Host 'Copy the https://...trycloudflare.com URL and append /webhook in the Meta dashboard.'
Write-Host 'When this tunnel restarts, update the callback URL in Meta.'
cloudflared tunnel --url "http://127.0.0.1:$Port"
exit $LASTEXITCODE
