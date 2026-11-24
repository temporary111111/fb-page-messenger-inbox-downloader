$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create the build environment.' }
}
& $taskPython -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Unable to install the build tools.' }
& $taskPython -c "import tkinter; t=tkinter.Tcl(); print('Tcl runtime:', t.eval('info patchlevel'))"
if ($LASTEXITCODE -ne 0) { throw 'Tcl/Tk cannot initialize in this environment. Run the build in a normal Windows terminal.' }
$taskStage = Join-Path $PSScriptRoot ('build/package-' + [guid]::NewGuid().ToString('N'))
& $taskPython -m PyInstaller --clean --noconfirm --onedir --windowed --name InboxArchive --distpath $taskStage --workpath build --specpath build desktop_entry.py
if ($LASTEXITCODE -ne 0) { throw 'Desktop executable build failed.' }
& $taskPython -m inbox_archive.packaging --source (Join-Path $taskStage 'InboxArchive') --dist (Join-Path $PSScriptRoot 'dist')
if ($LASTEXITCODE -ne 0) { throw 'Unable to publish the new portable folder.' }
Write-Host 'Use Open Inbox Archive.cmd to reuse this directory''s existing settings and data.'
