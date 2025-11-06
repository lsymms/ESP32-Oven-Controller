param(
  [string]$Src = "$PSScriptRoot\..\src",
  [string]$Dst = "D:\",              
  [switch]$Quiet
)

if (-not (Test-Path $Dst)) { Write-Error "Destination $Dst not found"; exit 1 }
$opts = "/MIR /FFT /XD .git __pycache__ /R:1 /W:1"
if ($Quiet) { $opts += " /NFL /NDL /NJH /NJS" }
& robocopy $Src $Dst $opts | Out-Null
