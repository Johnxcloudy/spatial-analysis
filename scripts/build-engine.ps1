param([switch]$SkipSync)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$engineRoot = Join-Path $root 'gis-engine'
Push-Location $root
try {
    if (-not $SkipSync) {
        & uv sync --project $engineRoot --frozen --group dev
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    }
    & uv run --project $engineRoot --frozen --group dev python -m PyInstaller --noconfirm --clean --onedir --console --name spatial-engine --distpath (Join-Path $engineRoot 'dist') --workpath (Join-Path $engineRoot 'build') --specpath (Join-Path $engineRoot 'build') --paths (Join-Path $engineRoot 'src') --additional-hooks-dir (Join-Path $PSScriptRoot 'pyinstaller-hooks') --copy-metadata numpy --copy-metadata pyarrow --copy-metadata affine --exclude-module pytest (Join-Path $PSScriptRoot 'engine-entry.py')
    if ($LASTEXITCODE -ne 0) { throw 'GIS engine packaging failed.' }
    $source = Join-Path $engineRoot 'dist\spatial-engine'
    $destination = Join-Path $root 'apps\desktop\src-tauri\resources\engine'
    $expectedDestination = [System.IO.Path]::GetFullPath((Join-Path $root 'apps\desktop\src-tauri\resources\engine'))
    if ([System.IO.Path]::GetFullPath($destination) -ne $expectedDestination -or -not $expectedDestination.StartsWith([System.IO.Path]::GetFullPath($root) + [System.IO.Path]::DirectorySeparatorChar)) {
        throw 'Refusing to clean an engine resource directory outside this checkout.'
    }
    $artifactRoot = [IO.Path]::GetFullPath((Join-Path $root '.artifacts'))
    New-Item -ItemType Directory -Path $artifactRoot -Force | Out-Null
    if ((Get-Item -LiteralPath $artifactRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Refusing to stage engine resources in a linked artifact directory.'
    }
    $runId = [guid]::NewGuid().ToString('N')
    $staged = Join-Path $artifactRoot "engine-new-$runId"
    $previous = Join-Path $artifactRoot "engine-old-$runId"
    New-Item -ItemType Directory -Path $staged | Out-Null
    Copy-Item -Path (Join-Path $source '*') -Destination $staged -Recurse -Force
    if (-not (Test-Path -LiteralPath (Join-Path $staged 'spatial-engine.exe') -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $staged '_internal') -PathType Container)) {
        throw 'The staged engine is incomplete.'
    }
    $hadPrevious = Test-Path -LiteralPath $destination
    if ($hadPrevious) {
        if ((Get-Item -LiteralPath $destination -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Refusing to replace a linked engine resource directory.'
        }
        $readme = Join-Path $destination 'README.txt'
        if (Test-Path -LiteralPath $readme) { Copy-Item -LiteralPath $readme -Destination $staged }
        [IO.Directory]::Move($destination, $previous)
    }
    try {
        [IO.Directory]::Move($staged, $destination)
    } catch {
        if ($hadPrevious -and -not (Test-Path -LiteralPath $destination)) {
            [IO.Directory]::Move($previous, $destination)
        }
        throw
    }
    # A locked old DLL must not contaminate or block the new resource tree.
    if ($hadPrevious) {
        try { Remove-Item -LiteralPath $previous -Recurse -Force }
        catch { Write-Warning "Previous engine resources remain in ${previous}: $($_.Exception.Message)" }
    }
    Write-Output "Bundled engine: $destination"
} finally { Pop-Location }
