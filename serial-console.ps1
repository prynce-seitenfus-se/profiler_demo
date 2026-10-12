<#
.SYNOPSIS
    Interactive Serial Console CLI for STM32G070 / SerTOS.

.DESCRIPTION
    Connects to an STM32 Virtual COM Port (or any specified serial port) and provides
    an interactive serial CLI with live streaming, command sending, and profiler dump capture.

.PARAMETER PortName
    Serial COM port (e.g. COM3). If omitted, automatically selects STLink Virtual COM Port or prompts.

.PARAMETER BaudRate
    Baud rate for serial communication. Default is 115200.

.PARAMETER Parity
    Parity setting (None, Odd, Even, Mark, Space). Default is None.

.PARAMETER DataBits
    Number of data bits (5 to 8). Default is 8.

.PARAMETER StopBits
    Stop bits (One, Two, OnePointFive). Default is One.

.PARAMETER NewLine
    Line ending format for sent commands: CRLF, LF, or CR. Default is CRLF.

.PARAMETER LogFile
    Optional path to log all received serial output to a file.

.EXAMPLE
    .\serial-console.ps1
    .\serial-console.ps1 -PortName COM3
    .\serial-console.ps1 -PortName COM3 -BaudRate 115200 -LogFile serial_log.txt
#>
[CmdletBinding()]
param(
    [string] $PortName,
    [int] $BaudRate = 115200,
    [System.IO.Ports.Parity] $Parity = [System.IO.Ports.Parity]::None,
    [int] $DataBits = 8,
    [System.IO.Ports.StopBits] $StopBits = [System.IO.Ports.StopBits]::One,
    [ValidateSet('CRLF', 'LF', 'CR')]
    [string] $NewLine = 'CRLF',
    [string] $LogFile,
    [string] $CaptureDump
)

$ErrorActionPreference = 'Stop'

function Get-AvailablePortsInfo
{
    $ports = [System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object -Unique
    $pnpList = @()
    try {
        $pnpList = Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
            Where-Object { $_.PNPClass -eq 'Ports' -or $_.Name -match 'COM\d+' }
    }
    catch { }

    $result = @()
    foreach ($p in $ports) {
        $matched = $pnpList | Where-Object { $_.Name -match "\($p\)" -or $_.Caption -match "\($p\)" } | Select-Object -First 1
        $desc = if ($matched) { $matched.Name } else { "Serial Device ($p)" }
        $isSTLink = ($desc -match 'STLink' -or $desc -match 'STMicroelectronics')
        $result += [PSCustomObject]@{
            Port        = $p
            Description = $desc
            IsSTLink    = $isSTLink
        }
    }
    return $result
}

# Resolve COM Port
if (-not $PortName) {
    $available = Get-AvailablePortsInfo
    if ($available.Count -eq 0) {
        Write-Error "No serial COM ports found on this system. Connect the STM32 board and try again."
        exit 1
    }

    $stlink = $available | Where-Object { $_.IsSTLink } | Select-Object -First 1
    if ($stlink) {
        $PortName = $stlink.Port
        Write-Host "[AUTO-DETECT] Found STLink port: $($stlink.Description)" -ForegroundColor Cyan
    }
    elseif ($available.Count -eq 1) {
        $PortName = $available[0].Port
        Write-Host "[AUTO-DETECT] Selected port: $($available[0].Description)" -ForegroundColor Cyan
    }
    else {
        Write-Host "`nAvailable COM Ports:" -ForegroundColor Yellow
        for ($i = 0; $i -lt $available.Count; $i++) {
            Write-Host "  [$($i+1)] $($available[$i].Port) - $($available[$i].Description)"
        }
        $choice = Read-Host "Select port number (1-$($available.Count))"
        $idx = [int]$choice - 1
        if ($idx -ge 0 -and $idx -lt $available.Count) {
            $PortName = $available[$idx].Port
        }
        else {
            Write-Error "Invalid port selection."
            exit 1
        }
    }
}

function Resolve-ScriptPath([string]$Path)
{
    if (-not $Path) { return $null }
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot $Path))
}

$lineEnding = switch ($NewLine) {
    'CRLF' { "`r`n" }
    'LF'   { "`n" }
    'CR'   { "`r" }
}

$port = New-Object System.IO.Ports.SerialPort($PortName, $BaudRate, $Parity, $DataBits, $StopBits)
$port.Encoding = [System.Text.Encoding]::GetEncoding("iso-8859-1")
$port.ReadTimeout = 100
$port.WriteTimeout = 1000
$port.DtrEnable = $true
$port.RtsEnable = $true

$logStream = $null
if ($LogFile) {
    $logFullPath = Resolve-ScriptPath $LogFile
    $logDir = [System.IO.Path]::GetDirectoryName($logFullPath)
    if (-not [System.IO.Directory]::Exists($logDir)) {
        [void][System.IO.Directory]::CreateDirectory($logDir)
    }
    $logStream = [System.IO.File]::AppendText($logFullPath)
    Write-Host "[LOGGING] Logging output to: $logFullPath" -ForegroundColor DarkGray
}

function Show-Help
{
    Write-Host "`n--- Serial Console Commands ---" -ForegroundColor Cyan
    Write-Host "  /help, /?              Show this help message"
    Write-Host "  /exit, /quit, exit     Close port and exit console"
    Write-Host "  /clear, /cls           Clear console screen"
    Write-Host "  /dump [file.bin]       Request and capture PROF-BIN v2/v3/v4 profiler dump"
    Write-Host "  /send <hex bytes>      Send raw hex bytes (e.g. /send 70 72 6f 66)"
    Write-Host "  /info                  Show connection and port information"
    Write-Host "  <any text> + Enter     Send line with $NewLine ending"
    Write-Host "-------------------------------`n" -ForegroundColor Cyan
}

function Capture-ProfilerDump([System.IO.Ports.SerialPort]$serialPort, [string]$outputFile)
{
    if (-not $outputFile) {
        $outputFile = "profiler-dump-$(Get-Date -Format 'yyyyMMdd-HHmmss').bin"
    }

    Write-Host "`n[PROFILER] Sending 'prof-dump'..." -ForegroundColor Yellow
    $cmdBytes = [System.Text.Encoding]::ASCII.GetBytes("prof-dump`r`n")
    $serialPort.Write($cmdBytes, 0, $cmdBytes.Length)

    $magicWindow = New-Object 'System.Collections.Generic.Queue[byte]'
    $magicFound = $false
    $timeoutAt = [DateTime]::UtcNow.AddSeconds(10)

    while (-not $magicFound -and [DateTime]::UtcNow -lt $timeoutAt) {
        try {
            $nextByte = $serialPort.ReadByte()
            if ($nextByte -ge 0) {
                if ($magicWindow.Count -eq 4) { $null = $magicWindow.Dequeue() }
                $magicWindow.Enqueue([byte]$nextByte)

                if ($magicWindow.Count -eq 4) {
                    $w = $magicWindow.ToArray()
                    $magicFound = ($w[0] -eq 0x50 -and $w[1] -eq 0x52 -and $w[2] -eq 0x4F -and $w[3] -eq 0x46)
                }
            }
        }
        catch [System.TimeoutException] { }
    }

    if (-not $magicFound) {
        Write-Host "[ERROR] Timeout waiting for PROF header magic." -ForegroundColor Red
        return
    }

    # Read remaining 12 bytes of header
    $headerTail = New-Object byte[] 12
    $readCount = 0
    while ($readCount -lt 12 -and [DateTime]::UtcNow -lt $timeoutAt) {
        try {
            $b = $serialPort.ReadByte()
            if ($b -ge 0) {
                $headerTail[$readCount++] = [byte]$b
            }
        }
        catch [System.TimeoutException] { }
    }

    if ($readCount -lt 12) {
        Write-Host "[ERROR] Incomplete profiler header." -ForegroundColor Red
        return
    }

    $header = [byte[]](@(0x50, 0x52, 0x4F, 0x46) + $headerTail)
    $version = [System.BitConverter]::ToUInt16($header, 4)
    $recordCount = [System.BitConverter]::ToUInt16($header, 6)

    if ($version -eq 2) {
        $headerLength = 16
    } elseif ($version -eq 3) {
        $headerLength = 24
    } elseif ($version -eq 4) {
        $headerLength = 28
    } else {
        Write-Host "[ERROR] Unsupported profiler version: $version" -ForegroundColor Red
        return
    }

    $packetLength = $headerLength + (24 * $recordCount) + 4
    $payloadLength = $packetLength - 16
    $payload = New-Object byte[] $payloadLength
    $readPayload = 0

    while ($readPayload -lt $payloadLength -and [DateTime]::UtcNow -lt $timeoutAt) {
        try {
            $b = $serialPort.ReadByte()
            if ($b -ge 0) {
                $payload[$readPayload++] = [byte]$b
            }
        }
        catch [System.TimeoutException] { }
    }

    if ($readPayload -lt $payloadLength) {
        Write-Host "[ERROR] Incomplete payload: received $readPayload of $payloadLength bytes." -ForegroundColor Red
        return
    }

    $packet = [byte[]]($header + $payload)
    $fullPath = Resolve-ScriptPath $outputFile
    [System.IO.File]::WriteAllBytes($fullPath, $packet)
    Write-Host "[SUCCESS] Saved PROF-BIN v$version dump: $fullPath ($packetLength bytes, $recordCount records)" -ForegroundColor Green
}

try {
    $port.Open()
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

    if ($CaptureDump) {
        Capture-ProfilerDump -serialPort $port -outputFile $CaptureDump
        return
    }

    Write-Host "============================================================" -ForegroundColor Green
    Write-Host " STM32G070 Serial CLI Console" -ForegroundColor Green
    Write-Host " Connected: $PortName @ $BaudRate baud ($DataBits-$Parity-$StopBits)" -ForegroundColor Green
    Write-Host " Type '/help' for commands, '/exit' or Ctrl+C to disconnect." -ForegroundColor DarkGray
    Write-Host "============================================================`n" -ForegroundColor Green

    $inputBuffer = New-Object System.Text.StringBuilder
    $running = $true

    # Catch Ctrl+C gracefully
    [Console]::TreatControlCAsInput = $false

    while ($running) {
        # 1. Drain incoming serial data
        if ($port.IsOpen -and $port.BytesToRead -gt 0) {
            $byteCount = $port.BytesToRead
            $rawBuffer = New-Object byte[] $byteCount
            $readBytes = $port.Read($rawBuffer, 0, $byteCount)
            if ($readBytes -gt 0) {
                $text = [System.Text.Encoding]::ASCII.GetString($rawBuffer, 0, $readBytes)
                [Console]::Write($text)
                if ($logStream) {
                    $logStream.Write($text)
                    $logStream.Flush()
                }
            }
        }

        # 2. Process user keyboard input non-blockingly
        if ([Console]::KeyAvailable) {
            $keyInfo = [Console]::ReadKey($true)

            if ($keyInfo.Key -eq [ConsoleKey]::Enter) {
                [Console]::WriteLine()
                $line = $inputBuffer.ToString().Trim()
                $inputBuffer.Clear()

                if ($line.Length -gt 0) {
                    if ($line -eq '/exit' -or $line -eq '/quit' -or $line -eq 'exit' -or $line -eq 'quit') {
                        $running = $false
                    }
                    elseif ($line -eq '/help' -or $line -eq '/?') {
                        Show-Help
                    }
                    elseif ($line -eq '/clear' -or $line -eq '/cls') {
                        [Console]::Clear()
                    }
                    elseif ($line -eq '/info') {
                        Write-Host "`nPort: $PortName, Baud: $BaudRate, Parity: $Parity, Data: $DataBits, StopBits: $StopBits, NewLine: $NewLine" -ForegroundColor Cyan
                    }
                    elseif ($line.StartsWith('/dump')) {
                        $parts = $line -split '\s+', 2
                        $dumpPath = if ($parts.Length -gt 1) { $parts[1] } else { '' }
                        Capture-ProfilerDump -serialPort $port -outputFile $dumpPath
                    }
                    elseif ($line.StartsWith('/send ')) {
                        $hexStr = $line.Substring(6).Trim() -replace '\s+', ''
                        if ($hexStr.Length % 2 -ne 0) {
                            Write-Host "[ERROR] Hex string must have an even number of characters." -ForegroundColor Red
                        }
                        else {
                            $bytesToSend = for ($i = 0; $i -lt $hexStr.Length; $i += 2) {
                                [Convert]::ToByte($hexStr.Substring($i, 2), 16)
                            }
                            $port.Write($bytesToSend, 0, $bytesToSend.Length)
                            Write-Host "[SENT] $($bytesToSend.Length) raw bytes." -ForegroundColor DarkGray
                        }
                    }
                    else {
                        # Send text command over serial
                        $sendData = $line + $lineEnding
                        $bytes = [System.Text.Encoding]::ASCII.GetBytes($sendData)
                        $port.Write($bytes, 0, $bytes.Length)
                    }
                }
            }
            elseif ($keyInfo.Key -eq [ConsoleKey]::Backspace) {
                if ($inputBuffer.Length -gt 0) {
                    $null = $inputBuffer.Remove($inputBuffer.Length - 1, 1)
                    [Console]::Write("`b `b")
                }
            }
            elseif ($keyInfo.KeyChar -ge 32) {
                $null = $inputBuffer.Append($keyInfo.KeyChar)
                [Console]::Write($keyInfo.KeyChar)
            }
        }

        [System.Threading.Thread]::Sleep(10)
    }
}
catch [System.Management.Automation.PipelineStoppedException] {
    # Clean exit on pipeline termination
}
catch {
    Write-Error "Serial communication error: $_"
}
finally {
    if ($null -ne $port) {
        if ($port.IsOpen) {
            $port.Close()
        }
        $port.Dispose()
        Write-Host "`n[DISCONNECTED] Serial port $PortName closed." -ForegroundColor Yellow
    }
    if ($null -ne $logStream) {
        $logStream.Dispose()
    }
}
