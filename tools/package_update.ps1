param(
    [ValidateSet("major", "minor", "patch")]
    [string]$Bump = "patch"
)

$root = Split-Path -Parent $PSScriptRoot
$srcFolder = Join-Path $root "src\ovencontroller"
$versionPath = Join-Path $srcFolder "version.txt"
$manifestPath = Join-Path $srcFolder "manifest.json"

if (-not (Test-Path $srcFolder)) {
    Write-Error "Cannot locate ovencontroller folder at $srcFolder"
    exit 1
}

function Get-VersionParts {
    param([string]$Version)
    $parts = $Version.Split(".")
    while ($parts.Count -lt 3) {
        $parts += "0"
    }
    return $parts | ForEach-Object { [int]$_ }
}

function Format-Version {
    param([int[]]$Parts)
    return ("{0}.{1}.{2}" -f $Parts[0], $Parts[1], $Parts[2])
}

$currentVersion = "0.0.0"
if (Test-Path $versionPath) {
    $text = Get-Content $versionPath -ErrorAction SilentlyContinue
    if ($text) {
        $currentVersion = $text.Trim()
    }
}
$parts = Get-VersionParts -Version $currentVersion
switch ($Bump) {
    "major" {
        $parts[0] += 1
        $parts[1] = 0
        $parts[2] = 0
    }
    "minor" {
        $parts[1] += 1
        $parts[2] = 0
    }
    "patch" {
        $parts[2] += 1
    }
}
$newVersion = Format-Version -Parts $parts

$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($versionPath, $newVersion, $utf8NoBom)

$files = Get-ChildItem -Path $srcFolder -Recurse -File -Filter *.py | ForEach-Object {
    $relative = $_.FullName.Substring($srcFolder.Length).TrimStart("\")
    @{ path = $relative -replace "\\", "/" }
}
$manifest = @{
    version = $newVersion
    files   = $files
}
$json = $manifest | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($manifestPath, $json, $utf8NoBom)

Write-Host "Updated version to $newVersion and regenerated manifest."
