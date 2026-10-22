<#
.SYNOPSIS
    Generic Unix-like Serial Terminal Console with Command History.

.DESCRIPTION
    Connects to a specified serial COM port and provides an interactive
    terminal console with a fixed '> ' prompt, command history recall,
    and built-in console utilities (help, clear, comm).
    Use prof-dump > <file>.bin inside the console to save raw profiler telemetry.
    All other commands are forwarded directly to the application/target.

.PARAMETER PortName
    Serial COM port (e.g. COM3). Mandatory input matching COM<X>.

.PARAMETER BaudRate
    Baud rate for serial communication. Default is 115200.

.PARAMETER Parity
    Parity setting (None, Odd, Even, Mark, Space). Default is None.

.PARAMETER DataBits
    Number of data bits (5 to 8). Default is 8.

.PARAMETER StopBits
    Stop bits (One, Two, OnePointFive). Default is One.

.PARAMETER NewLine
    Line ending format to send on Enter key: CRLF, LF, or CR. Default is CRLF.

.PARAMETER HistoryLimit
    Maximum number of commands saved in history buffer. Default is 50.

.PARAMETER LogFile
    Optional file path to log serial session.

.EXAMPLE
    .\serial-console.ps1 COM3

.EXAMPLE
    .\serial-console.ps1 COM3 -BaudRate 115200
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0, HelpMessage = 'Serial COM port (e.g. COM3)')]
    [ValidatePattern('^(?i)COM\d+$')]
    [string] $PortName,

    [int] $BaudRate = 115200,

    [System.IO.Ports.Parity] $Parity = [System.IO.Ports.Parity]::None,

    [int] $DataBits = 8,

    [System.IO.Ports.StopBits] $StopBits = [System.IO.Ports.StopBits]::One,

    [ValidateSet('CRLF', 'LF', 'CR')]
    [string] $NewLine = 'CRLF',

    [int] $HistoryLimit = 50,

    [string] $LogFile
)

$ErrorActionPreference = 'Stop'

# Normalize port name casing
$PortName = $PortName.ToUpper()

function Resolve-ScriptPath([string]$Path)
{
    if (-not $Path) { return $null }
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot $Path))
}

$availablePorts = [System.IO.Ports.SerialPort]::GetPortNames()
if ($availablePorts -notcontains $PortName) {
    Write-Error "Serial port $PortName not found. Available ports: $(if ($availablePorts.Count -gt 0) { $availablePorts -join ', ' } else { 'none' })"
    exit 1
}

$lineEnding = switch ($NewLine) {
    'CRLF' { "`r`n" }
    'LF'   { "`n" }
    'CR'   { "`r" }
}
$lineEndingBytes = switch ($NewLine) {
    'CRLF' { [byte[]]@(0x0D, 0x0A) }
    'LF'   { [byte[]]@(0x0A) }
    'CR'   { [byte[]]@(0x0D) }
}

$port = New-Object System.IO.Ports.SerialPort($PortName, $BaudRate, $Parity, $DataBits, $StopBits)
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
}

$parityShort = switch ($Parity) {
    'None'  { 'N' }
    'Odd'   { 'O' }
    'Even'  { 'E' }
    'Mark'  { 'M' }
    'Space' { 'S' }
    default { 'N' }
}

$stopShort = switch ($StopBits) {
    'One'          { '1' }
    'Two'          { '2' }
    'OnePointFive' { '1.5' }
    default        { '1' }
}

function Read-ProfilerBytes($SerialPort, [int]$Length, [DateTime]$Deadline)
{
    $buffer = New-Object byte[] $Length
    $offset = 0
    while ($offset -lt $Length -and [DateTime]::UtcNow -lt $Deadline) {
        try {
            $read = $SerialPort.Read($buffer, $offset, $Length - $offset)
            if ($read -gt 0) {
                $offset += $read
            }
        }
        catch [System.TimeoutException] {
            # Serial reads time out briefly while waiting for the rest of the packet.
        }
    }
    if ($offset -ne $Length) {
        throw "Incomplete profiler dump: received $offset of $Length expected bytes."
    }
    return ,$buffer
}

function Save-ProfilerDump($SerialPort, [string]$OutputFile, [byte[]]$CommandBytes)
{
    $outputPath = if ([System.IO.Path]::IsPathRooted($OutputFile)) {
        [System.IO.Path]::GetFullPath($OutputFile)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path (Get-Location).ProviderPath $OutputFile))
    }

    $SerialPort.DiscardInBuffer()
    $SerialPort.Write($CommandBytes, 0, $CommandBytes.Length)
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $magicWindow = New-Object 'System.Collections.Generic.Queue[byte]'
    $magicFound = $false
    while (-not $magicFound -and [DateTime]::UtcNow -lt $deadline) {
        try {
            $nextByte = $SerialPort.ReadByte()
            if ($nextByte -lt 0) {
                throw 'Serial connection closed before the profiler dump started.'
            }
            if ($magicWindow.Count -eq 4) {
                $null = $magicWindow.Dequeue()
            }
            $magicWindow.Enqueue([byte]$nextByte)
            if ($magicWindow.Count -eq 4) {
                $window = $magicWindow.ToArray()
                $magicFound = ($window[0] -eq 0x50) -and ($window[1] -eq 0x52) -and
                              ($window[2] -eq 0x4F) -and ($window[3] -eq 0x46)
            }
        }
        catch [System.TimeoutException] {
            # Ignore per-read timeouts until the overall response deadline.
        }
    }
    if (-not $magicFound) {
        throw 'Timeout waiting for the PROF-BIN header.'
    }

    $headerTail = Read-ProfilerBytes $SerialPort 12 $deadline
    $header = [byte[]](@(0x50, 0x52, 0x4F, 0x46) + $headerTail)
    $version = [System.BitConverter]::ToUInt16($header, 4)
    $recordCount = [System.BitConverter]::ToUInt16($header, 6)
    $headerLength = switch ($version) { 2 { 16 } 3 { 24 } 4 { 28 } default { 0 } }
    if ($headerLength -eq 0) {
        throw "Unsupported PROF-BIN version: $version."
    }
    $packetLength = $headerLength + (24 * $recordCount) + 4
    $deadline = [DateTime]::UtcNow.AddSeconds(30 + [Math]::Ceiling($packetLength * 12.0 / $SerialPort.BaudRate))
    $packetTail = Read-ProfilerBytes $SerialPort ($packetLength - 16) $deadline
    $packet = [byte[]]($header + $packetTail)

    [uint32]$crc = 4294967295
    for ($i = 0; $i -lt $packet.Length - 4; $i++) {
        $crc = $crc -bxor $packet[$i]
        for ($bit = 0; $bit -lt 8; $bit++) {
            if (($crc -band 1) -ne 0) {
                $crc = ($crc -shr 1) -bxor [uint32]0xEDB88320L
            } else {
                $crc = $crc -shr 1
            }
        }
    }
    $crc = $crc -bxor [uint32]4294967295
    if ($crc -ne [System.BitConverter]::ToUInt32($packet, $packet.Length - 4)) {
        throw 'Profiler dump CRC32 mismatch; no file was saved.'
    }

    [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($outputPath))
    $outputStream = [System.IO.File]::Open($outputPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
    try {
        $outputStream.Write($packet, 0, $packet.Length)
    }
    finally {
        $outputStream.Dispose()
    }
    Write-Host "Saved PROF-BIN v$version dump: $outputPath ($recordCount records, $packetLength bytes)" -ForegroundColor Green
}

function Show-BuiltinHelp
{
    Write-Host "`nBuilt-in console commands:" -ForegroundColor Cyan
    Write-Host "  help              Show this built-in help message"
    Write-Host "  clear, cls        Clear console screen"
    Write-Host "  comm, info        Show serial communication settings"
    Write-Host "  exit, quit        Disconnect and exit (or press Ctrl+C / Ctrl+Q)"
    Write-Host "  prof-dump > <file>.bin   Save binary telemetry (overwrites existing file)"
    Write-Host "`nTarget profiler commands:" -ForegroundColor Cyan
    Write-Host "  prof-help, prof-top, prof-dump, prof-reset, prof-status`n" -ForegroundColor DarkGray
}

$encoding = [System.Text.Encoding]::UTF8
$prompt = '> '

try {
    $port.Open()
    [Console]::OutputEncoding = $encoding
    [Console]::TreatControlCAsInput = $true

    Write-Host "[connected to $PortName @ $BaudRate $DataBits-$parityShort-$stopShort | escape: Ctrl+C or Ctrl+Q]" -ForegroundColor Cyan

    $history = New-Object System.Collections.Generic.List[string]
    $historyIndex = -1
    $inputBuffer = New-Object System.Text.StringBuilder
    $running = $true
    $promptVisible = $true
    $lastReceiveTime = [DateTime]::MinValue
    $sentCommandTime = [DateTime]::MinValue
    $hasReceivedData = $false
    $endsInNewline = $true

    # Display initial fixed prompt
    [Console]::Write($prompt)

    while ($running) {
        # 1. Drain incoming serial data and print directly to console
        if ($port.IsOpen -and $port.BytesToRead -gt 0) {
            $byteCount = $port.BytesToRead
            $rawBuffer = New-Object byte[] $byteCount
            $readBytes = $port.Read($rawBuffer, 0, $byteCount)
            if ($readBytes -gt 0) {
                $incomingText = $encoding.GetString($rawBuffer, 0, $readBytes)

                # If prompt is currently drawn on the line, clear it before rendering retrieved data
                if ($promptVisible) {
                    $currInput = $inputBuffer.ToString()
                    $clearLen = $prompt.Length + $currInput.Length
                    [Console]::Write("`r" + (' ' * $clearLen) + "`r")
                    $promptVisible = $false
                }

                # Output retrieved data directly without prompt interference
                [Console]::Write($incomingText)

                if ($null -ne $logStream) {
                    $logStream.Write($incomingText)
                    $logStream.Flush()
                }

                $hasReceivedData = $true
                $endsInNewline = ($incomingText.EndsWith("`n") -or $incomingText.EndsWith("`r"))
                $lastReceiveTime = [DateTime]::UtcNow
            }
        }

        # 2. When incoming data has settled, re-render prompt
        if (-not $promptVisible) {
            $now = [DateTime]::UtcNow
            $shouldRestore = ($hasReceivedData -and ($now - $lastReceiveTime).TotalMilliseconds -gt 80) -or
                             (-not $hasReceivedData -and ($now - $sentCommandTime).TotalMilliseconds -gt 350)

            if ($shouldRestore) {
                if ($hasReceivedData -and -not $endsInNewline) {
                    [Console]::WriteLine()
                }
                [Console]::Write($prompt + $inputBuffer.ToString())
                $promptVisible = $true
            }
        }

        # 3. Handle interactive keyboard input
        if ([Console]::KeyAvailable) {
            $keyInfo = [Console]::ReadKey($true)

            # Exit escape shortcuts: Ctrl+C (ASCII 3) or Ctrl+Q (ASCII 17)
            if (($keyInfo.KeyChar -eq [char]3) -or
                ($keyInfo.KeyChar -eq [char]17) -or
                ($keyInfo.Key -eq [ConsoleKey]::C -and ($keyInfo.Modifiers -band [ConsoleModifiers]::Control)) -or
                ($keyInfo.Key -eq [ConsoleKey]::Q -and ($keyInfo.Modifiers -band [ConsoleModifiers]::Control))) {
                $running = $false
                break
            }

            # If prompt was hidden, restore it before processing keystroke
            if (-not $promptVisible) {
                [Console]::Write($prompt + $inputBuffer.ToString())
                $promptVisible = $true
            }

            # Enter
            if ($keyInfo.Key -eq [ConsoleKey]::Enter) {
                [Console]::WriteLine()
                $command = $inputBuffer.ToString().Trim()
                $null = $inputBuffer.Clear()
                $promptVisible = $false
                $hasReceivedData = $false
                $sentCommandTime = [DateTime]::UtcNow
                $lastReceiveTime = [DateTime]::MinValue

                if ($command.Length -gt 0) {
                    # Add to command history (avoid immediate duplicates)
                    if ($history.Count -eq 0 -or $history[$history.Count - 1] -ne $command) {
                        $history.Add($command)
                        if ($history.Count -gt $HistoryLimit) {
                            $history.RemoveAt(0)
                        }
                    }
                    $historyIndex = $history.Count

                    # Built-in host commands (help, clear, comm, exit)
                    if ($command -eq 'help') {
                        Show-BuiltinHelp
                        [Console]::Write($prompt)
                        $promptVisible = $true
                    }
                    elseif ($command -in @('clear', 'cls', '/clear', '/cls')) {
                        [Console]::Clear()
                        [Console]::Write($prompt)
                        $promptVisible = $true
                    }
                    elseif ($command -in @('comm', 'info', '/comm', '/info')) {
                        Write-Host "Port: $PortName | Baud: $BaudRate | Parity: $Parity | DataBits: $DataBits | StopBits: $StopBits | NewLine: $NewLine" -ForegroundColor Cyan
                        [Console]::Write($prompt)
                        $promptVisible = $true
                    }
                    elseif ($command -in @('exit', 'quit', '/exit', '/quit')) {
                        $running = $false
                        break
                    }
                    elseif ($command -match '^prof-dump\s*>') {
                        try {
                            if ($command -notmatch '^prof-dump\s*>\s*(?:"([^"]+)"|''([^'']+)''|([^"''<>\s]+))\s*$') {
                                throw 'Usage: prof-dump > <file>.bin (quote paths containing spaces).'
                            }
                            $outputFile = if ($Matches[1]) { $Matches[1] } elseif ($Matches[2]) { $Matches[2] } else { $Matches[3] }
                            if ($null -ne $logStream) {
                                $logStream.WriteLine(">> " + $command)
                                $logStream.Flush()
                            }
                            $cmdBytes = $encoding.GetBytes('prof-dump' + $lineEnding)
                            Save-ProfilerDump $port $outputFile $cmdBytes
                        }
                        catch {
                            Write-Host "Dump export error: $_" -ForegroundColor Red
                        }
                        [Console]::Write($prompt)
                        $promptVisible = $true
                    }
                    else {
                        # All other commands are forwarded directly to the application
                        $cmdBytes = $encoding.GetBytes($command + $lineEnding)
                        $port.Write($cmdBytes, 0, $cmdBytes.Length)

                        if ($null -ne $logStream) {
                            $logStream.WriteLine(">> " + $command)
                            $logStream.Flush()
                        }
                    }
                } else {
                    # Empty enter sends newline to target
                    $port.Write($lineEndingBytes, 0, $lineEndingBytes.Length)
                }
            }
            # Backspace
            elseif ($keyInfo.Key -eq [ConsoleKey]::Backspace) {
                if ($inputBuffer.Length -gt 0) {
                    $null = $inputBuffer.Remove($inputBuffer.Length - 1, 1)
                    [Console]::Write("`b `b")
                }
            }
            # UpArrow: Navigate backwards in history
            elseif ($keyInfo.Key -eq [ConsoleKey]::UpArrow) {
                if ($history.Count -gt 0) {
                    if ($historyIndex -gt 0) {
                        $historyIndex--
                    } elseif ($historyIndex -eq -1) {
                        $historyIndex = $history.Count - 1
                    }

                    # Clear current line on screen
                    $oldLen = $prompt.Length + $inputBuffer.Length
                    [Console]::Write("`r" + (' ' * $oldLen) + "`r")

                    # Replace with history command
                    $null = $inputBuffer.Clear()
                    $null = $inputBuffer.Append($history[$historyIndex])

                    # Redraw prompt and new command
                    [Console]::Write($prompt + $inputBuffer.ToString())
                }
            }
            # DownArrow: Navigate forwards in history
            elseif ($keyInfo.Key -eq [ConsoleKey]::DownArrow) {
                if ($history.Count -gt 0 -and $historyIndex -lt $history.Count) {
                    $historyIndex++

                    # Clear current line on screen
                    $oldLen = $prompt.Length + $inputBuffer.Length
                    [Console]::Write("`r" + (' ' * $oldLen) + "`r")
                    $null = $inputBuffer.Clear()

                    if ($historyIndex -lt $history.Count) {
                        $null = $inputBuffer.Append($history[$historyIndex])
                    }

                    # Redraw prompt and command
                    [Console]::Write($prompt + $inputBuffer.ToString())
                }
            }
            # Tab
            elseif ($keyInfo.Key -eq [ConsoleKey]::Tab) {
                $tabByte = [byte[]]@(0x09)
                $port.Write($tabByte, 0, 1)
            }
            # Printable characters
            elseif (-not [char]::IsControl($keyInfo.KeyChar)) {
                $null = $inputBuffer.Append($keyInfo.KeyChar)
                [Console]::Write($keyInfo.KeyChar)
            }
        }

        [System.Threading.Thread]::Sleep(1)
    }
}
catch [System.Management.Automation.PipelineStoppedException] {
    # Clean exit on pipeline termination
}
catch {
    Write-Error "Serial error: $_"
}
finally {
    [Console]::TreatControlCAsInput = $false
    if ($null -ne $port) {
        if ($port.IsOpen) {
            $port.Close()
        }
        $port.Dispose()
        Write-Host "`n[disconnected]" -ForegroundColor Yellow
    }
    if ($null -ne $logStream) {
        $logStream.Dispose()
    }
}
