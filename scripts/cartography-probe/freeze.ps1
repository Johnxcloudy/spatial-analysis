param(
    [string]$Python = '.artifacts/cartography-probe-env/Scripts/python.exe',
    [string]$Destination = '.artifacts/cartography-probe-freeze'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$output = [IO.Path]::GetFullPath((Join-Path $root $Destination))
$artifactRoot = [IO.Path]::GetFullPath((Join-Path $root '.artifacts'))
if (-not $output.StartsWith($artifactRoot + [IO.Path]::DirectorySeparatorChar) -or (Test-Path -LiteralPath $output)) {
    throw 'The freeze output must be a new directory under this repository .artifacts.'
}
New-Item -ItemType Directory -Path $output | Out-Null
Push-Location $root
try {
    $arguments = @('-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--console',
        '--name', 'cartography-probe', '--distpath', (Join-Path $output 'dist'),
        '--workpath', (Join-Path $output 'build'), '--specpath', (Join-Path $output 'spec'),
        '--additional-hooks-dir', (Join-Path $root 'scripts/pyinstaller-hooks'),
        '--hidden-import', 'matplotlib.backends.backend_pdf', '--collect-data', 'pypdfium2',
        '--exclude-module', 'pytest', '--exclude-module', 'tkinter')
    foreach ($name in @('matplotlib', 'pyogrio', 'pyproj', 'rasterio', 'shapely', 'numpy',
                       'Pillow', 'pypdf', 'pypdfium2', 'fonttools', 'pyarrow', 'affine')) {
        $arguments += @('--copy-metadata', $name)
    }
    $arguments += (Join-Path $PSScriptRoot 'probe.py')
    & $Python @arguments 2>&1 | Tee-Object -FilePath (Join-Path $output 'build.log')
    if ($LASTEXITCODE -ne 0) { throw 'Isolated probe freeze failed; inspect build.log.' }
    $bundle = Join-Path $output 'dist/cartography-probe'
    $files = Get-ChildItem -LiteralPath $bundle -File -Recurse
    [pscustomobject]@{
        Bundle = $bundle
        Bytes = ($files | Measure-Object -Property Length -Sum).Sum
        Files = $files.Count
        IncludesTestInspectors = $true
        IncludesChineseFont = $false
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'freeze-report.json') -Encoding utf8
} finally {
    Pop-Location
}
