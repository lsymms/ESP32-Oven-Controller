$folder = Join-Path $PSScriptRoot "..\src\ovencontroller"
if (-not (Test-Path $folder)) {
    Write-Host "Folder '$folder' not found."
    exit 1
}
Set-Location $folder
python -m http.server 8000
