$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$taskPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create the build environment.' }
}
& $taskPython -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Unable to install the build tools.' }

$dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
if (-not $dotnet) { throw '.NET SDK is required to build InboxArchive.exe.' }

$taskStage = Join-Path $PSScriptRoot ('build/package-' + [guid]::NewGuid().ToString('N'))
$bundleDir = Join-Path $taskStage 'InboxArchive'

& $taskPython -m PyInstaller --clean --noconfirm --onedir --console --name InboxArchiveCore --distpath $taskStage --workpath build --specpath build archive_core_entry.py
if ($LASTEXITCODE -ne 0) { throw 'Archive core executable build failed.' }

Move-Item -LiteralPath (Join-Path $taskStage 'InboxArchiveCore') -Destination $bundleDir

if (-not $env:DOTNET_CLI_HOME) {
    $env:DOTNET_CLI_HOME = Join-Path $PSScriptRoot '.dotnet-home'
}
& $dotnet.Source publish (Join-Path $PSScriptRoot 'native-ui/InboxArchive.Native.csproj') -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o $bundleDir
if ($LASTEXITCODE -ne 0) { throw 'Native desktop UI build failed.' }

& $taskPython -m inbox_archive.packaging --source $bundleDir --dist (Join-Path $PSScriptRoot 'dist')
if ($LASTEXITCODE -ne 0) { throw 'Unable to publish the new portable folder.' }
Write-Host 'Use Open Inbox Archive.cmd to reuse this directory''s existing settings and data.'
