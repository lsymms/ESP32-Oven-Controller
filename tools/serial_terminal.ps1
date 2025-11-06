<#  
  Launches CircuitPython REPL via pyserial’s miniterm.
  Auto-detects the COM port by matching the CIRCUITPY USB device.
#>

[CmdletBinding()]
param(
  [int]$Baud = 115200
)

function Get-CircuitPyComPort {
    # Find COM port by manufacturer/description keywords
    $ports = Get-CimInstance Win32_PnPEntity |
        Where-Object { $_.Name -match 'COM\d+' -and ($_.Name -match 'USB Serial' -or $_.Name -match 'CIRCUITPY' -or $_.Name -match 'Adafruit' -or $_.Name -match 'Silicon Labs' -or $_.Name -match 'CH9102' -or $_.Name -match 'ESP32' ) }

    if (-not $ports) {
        Write-Error "No serial devices found. Plug in your board and try again."
        exit 1
    }

    # Extract COM port number from the name string
    foreach ($p in $ports) {
        if ($p.Name -match '.*\((COM\d+)\)') {
            return $matches[1]
        }
    }

    Write-Error "Unable to determine COM port."
    exit 1
}

# Find COM port
$port = Get-CircuitPyComPort
Write-Host "Detected port: $port at $Baud baud" -ForegroundColor Cyan

# Ensure pyserial is installed
try {
    py -m serial.tools.list_ports | Out-Null
} catch {
    Write-Host "Installing pyserial..." -ForegroundColor Yellow
    py -m pip install pyserial
}

# Launch miniterm
Write-Host "`nOpening REPL (Ctrl+C to break, Ctrl+] to quit)...`n"
py -m serial.tools.miniterm $port $Baud
