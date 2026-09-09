param([Parameter(Mandatory=$true)][string]$Python)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
& $Python -c "import sys,struct; assert sys.platform=='win32' and sys.version_info[:2]==(3,12) and struct.calcsize('P')==8, 'Windows CPython 3.12 x64 required'"
if ($LASTEXITCODE -ne 0) { throw 'Unsupported Python executable.' }
$environmentPath = Join-Path $projectRoot '.venv'
if (Test-Path -LiteralPath $environmentPath) { throw 'A .venv already exists; inspect it before reinstalling.' }
& $Python -m venv $environmentPath
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
$environmentPython = Join-Path $environmentPath 'Scripts\python.exe'
& $environmentPython -m pip install -r (Join-Path $projectRoot 'requirements.lock')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; do not configure the MCP yet.' }
Write-Output "Installed. Start the licensed Maxsurf apps manually. MCP command: $environmentPython"
Write-Output "MCP args: $(Join-Path $projectRoot 'server.py')"
