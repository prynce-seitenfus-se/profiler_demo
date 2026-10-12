<#
.SYNOPSIS
    Program an STM32G070 over ST-LINK/SWD using STM32CubeProgrammer.

.DESCRIPTION
    Programs and verifies an ELF, Intel HEX, or raw binary firmware image, then
    resets the target. Raw .bin files are written at the STM32G070 flash base.

.PARAMETER FirmwarePath
    Path to the firmware image to program.

.EXAMPLE
    .\burn.ps1 .\build\Debug\sertos_stm32g070.elf

.EXAMPLE
    .\burn.ps1 .\firmware.bin
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string] $FirmwarePath
)

$ErrorActionPreference = 'Stop'

$programmerPath = 'C:\ST\STM32CubeCLT_1.21.0\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe'
if (-not (Test-Path -LiteralPath $programmerPath -PathType Leaf)) {
    throw "STM32_Programmer_CLI.exe not found: $programmerPath"
}

if (-not (Test-Path -LiteralPath $FirmwarePath -PathType Leaf)) {
    throw "Firmware image not found: $FirmwarePath"
}
$resolvedFirmwarePath = (Resolve-Path -LiteralPath $FirmwarePath).Path

$programmerArgs = @('-c', 'port=SWD', '-w', $resolvedFirmwarePath)
if ([System.IO.Path]::GetExtension($resolvedFirmwarePath) -ieq '.bin') {
    $programmerArgs += '0x08000000'
}
$programmerArgs += @('-v', '-rst')

Write-Host "Programmer : $programmerPath"
Write-Host "Firmware   : $resolvedFirmwarePath"
Write-Host 'Interface  : ST-LINK / SWD'

& $programmerPath @programmerArgs
$programmerExitCode = $LASTEXITCODE
if ($programmerExitCode -ne 0) {
    throw "STM32CubeProgrammer failed with exit code $programmerExitCode."
}

Write-Host 'Programming, verification, and target reset completed successfully.'
