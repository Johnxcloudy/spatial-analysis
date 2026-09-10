param([switch]$SkipEngine)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$cargoBin = Join-Path $env:USERPROFILE '.cargo\bin'
if (Test-Path -LiteralPath $cargoBin) { $env:Path = "$cargoBin;$env:Path" }
Push-Location $root
try {
    if (-not $SkipEngine) {
        & (Join-Path $PSScriptRoot 'build-engine.ps1')
    }
    & npm.cmd run tauri -- build
    if ($LASTEXITCODE -ne 0) { throw 'Windows desktop build failed.' }
} finally { Pop-Location }
