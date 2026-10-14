<#
.SYNOPSIS
    Runs the sewcet WCET extraction or CSV comparison.
.DESCRIPTION
    Thin wrapper around <sewcet>/wcet/run_wcet.ps1.
    - Pass -Elf (-e) to generate the static WCET CSV (and optionally -Compare/-cmp to compare).
    - Pass -Compare (-cmp) with two CSV files (without -Elf) to only run the comparison.
.EXAMPLE
    .\wcet\run_wcet.ps1 -Elf .\build\Debug\<project>.elf
    .\wcet\run_wcet.ps1 -e .\build\Debug\<project>.elf -o .\wcet\<wcet>.csv
    .\wcet\run_wcet.ps1 -cmp .\<measured>.csv .\wcet\<wcet>.csv
    .\wcet\run_wcet.ps1 -cmp .\<measured>.csv .\wcet\<wcet>.csv -o .\wcet\comparison.csv
#>
[CmdletBinding()]
param(
    [Alias('e')]
    [string]$Elf,

    [Alias('o', 'Output')]
    [string]$Out,

    [Alias('cmp', 'c')]
    [string[]]$Compare,

    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = 'Stop'

function Resolve-CallerPath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $Path))
}

if ($ExtraArgs) {
    $remArgs = @()
    foreach ($arg in $ExtraArgs) {
        if (-not $Elf -and $arg -match '\.elf$') {
            $Elf = $arg
        } elseif ($Compare) {
            $Compare = @($Compare) + @($arg)
        } elseif (-not $Elf) {
            $Elf = $arg
        } else {
            $remArgs += $arg
        }
    }
    $ExtraArgs = $remArgs
}

if (-not $Elf -and (-not $Compare -or $Compare.Count -eq 0)) {
    throw "Specify -Elf <firmware.elf> to generate WCET, or -Compare <measured.csv> <wcet.csv> to compare two CSV files."
}

if ($Elf -and -not (Test-Path -LiteralPath $Elf)) {
    throw "ELF file not found: $Elf"
}

$resolvedCompare = @()
if ($Compare) {
    foreach ($csv in $Compare) {
        foreach ($part in ($csv -split ',')) {
            $trimmed = $part.Trim()
            if ($trimmed) {
                $fullCsv = Resolve-CallerPath $trimmed
                if (-not (Test-Path -LiteralPath $fullCsv)) {
                    throw "Comparison CSV file not found: $trimmed"
                }
                $resolvedCompare += $fullCsv
            }
        }
    }
}

$sewcet = if ($env:SEWCET_DIR) { $env:SEWCET_DIR } else { Join-Path $PSScriptRoot '..\..\sewcet' }
$tool = Join-Path $sewcet 'wcet\run_wcet.ps1'
if (-not (Test-Path -LiteralPath $tool)) {
    $tool = Join-Path $sewcet 'tools\run_wcet.ps1'
}
if (-not (Test-Path -LiteralPath $tool)) {
    throw "sewcet tools not found: $tool (set SEWCET_DIR)"
}

$configPath = Join-Path $PSScriptRoot 'wcet_targets.json'
$toolArgs = @{
    Config   = $configPath
    WcetOnly = ([bool]$Elf -and ($resolvedCompare.Count -eq 0))
}
if ($Elf) {
    $toolArgs.Elf = Resolve-CallerPath $Elf
}
if ($Out) {
    $toolArgs.Out = Resolve-CallerPath $Out
}
if ($resolvedCompare.Count -gt 0) {
    $toolArgs.Compare = $resolvedCompare
}

& $tool @toolArgs
exit $LASTEXITCODE
