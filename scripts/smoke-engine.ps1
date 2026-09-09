#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Executable,
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not [IO.Path]::IsPathFullyQualified($Executable)) {
    throw 'Executable must be an absolute path to the packaged spatial-engine.exe.'
}
$engineExecutable = (Get-Item -LiteralPath $Executable -ErrorAction Stop).FullName
if (-not (Test-Path -LiteralPath $engineExecutable -PathType Leaf) -or
    [IO.Path]::GetFileName($engineExecutable) -ine 'spatial-engine.exe') {
    throw 'Executable must identify a packaged spatial-engine.exe file.'
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $runName = (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    $OutputDirectory = Join-Path $root (Join-Path '.artifacts\frozen-engine' $runName)
}
$outputPath = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $outputPath) {
    throw "Output directory already exists; choose an unused path: $outputPath"
}
New-Item -ItemType Directory -Path $outputPath | Out-Null

$projectDirectory = Join-Path $outputPath 'project'
$projectPath = Join-Path $projectDirectory 'project.spa'
$diagnosticsDirectory = Join-Path $outputPath 'diagnostics'
$reportPath = Join-Path $outputPath 'frozen-engine-smoke.json'
$stderrPath = Join-Path $outputPath 'engine-stderr.log'
$savedFields = [ordered]@{
    path = $projectPath
    name = 'Frozen engine smoke project saved'
    description = 'Saved and reopened by the frozen engine smoke test.'
    analysisCrs = 'EPSG:4547'
    displayCrs = 'EPSG:3857'
    viewState = @{ center = @(114.125, 27.25); zoom = 8.5 }
}
$requests = @(
    @{ jsonrpc = '2.0'; id = 1; method = 'runtime.info'; params = @{} }
    @{ jsonrpc = '2.0'; id = 2; method = 'project.create'; params = @{ directory = $projectDirectory; name = 'Frozen engine smoke project' } }
    @{ jsonrpc = '2.0'; id = 3; method = 'project.save'; params = $savedFields }
    @{ jsonrpc = '2.0'; id = 4; method = 'project.close'; params = @{} }
    @{ jsonrpc = '2.0'; id = 5; method = 'project.open'; params = @{ path = $projectPath } }
    @{ jsonrpc = '2.0'; id = 6; method = 'diagnostics.run'; params = @{ directory = $diagnosticsDirectory } }
    @{ jsonrpc = '2.0'; id = 7; method = 'project.close'; params = @{} }
)
$checks = [Collections.Generic.List[object]]::new()
$responses = [Collections.Generic.List[object]]::new()
$environmentNames = @('PYTHONHOME', 'PYTHONPATH', 'GDAL_DATA', 'PROJ_LIB', 'PROJ_DATA', 'PATH')
$originalEnvironment = @{}
foreach ($name in $environmentNames) {
    $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$originalOutputEncoding = $OutputEncoding
$originalConsoleEncoding = [Console]::OutputEncoding
$report = [ordered]@{
    ok = $false
    startedAt = [DateTime]::UtcNow.ToString('o')
    completedAt = $null
    executable = $engineExecutable
    outputDirectory = $outputPath
    sanitizedEnvironment = $environmentNames
    exitCode = $null
    requests = $requests
    responses = $responses
    rawStdout = @()
    checks = $checks
    stderrPath = $stderrPath
    error = $null
}

function Assert-SmokeCheck {
    param([string]$Name, [bool]$Condition, [string]$Detail)
    $checks.Add([ordered]@{ name = $Name; passed = $Condition; detail = $Detail })
    if (-not $Condition) { throw "${Name}: $Detail" }
}

try {
    foreach ($name in $environmentNames) {
        if ($name -ne 'PATH') { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
    }
    $systemRoot = [Environment]::GetEnvironmentVariable('SystemRoot', 'Process')
    if ([string]::IsNullOrWhiteSpace($systemRoot)) { throw 'SystemRoot is unavailable.' }
    $env:PATH = @((Join-Path $systemRoot 'System32'), $systemRoot, (Split-Path -Parent $engineExecutable)) -join ';'
    $OutputEncoding = [Text.UTF8Encoding]::new($false)
    [Console]::OutputEncoding = $OutputEncoding

    # One pipeline keeps the project lock and active-project state in one process.
    $requestLines = @($requests | ForEach-Object { ConvertTo-Json -InputObject $_ -Depth 10 -Compress })
    $responseLines = @($requestLines | & $engineExecutable 2> $stderrPath)
    $report.exitCode = $LASTEXITCODE
    $report.rawStdout = @($responseLines | ForEach-Object { [string]$_ })
    Assert-SmokeCheck 'process.exit' ($report.exitCode -eq 0) "Engine exit code: $($report.exitCode)"
    Assert-SmokeCheck 'rpc.responseCount' ($responseLines.Count -eq $requests.Count) "Expected $($requests.Count) responses; received $($responseLines.Count)."
    for ($index = 0; $index -lt $responseLines.Count; $index++) {
        $response = ConvertFrom-Json -InputObject ([string]$responseLines[$index]) -ErrorAction Stop
        $responses.Add($response)
        $request = $requests[$index]
        $prefix = "rpc.$($request.id).$($request.method)"
        Assert-SmokeCheck "$prefix.version" ($response.jsonrpc -ceq '2.0') 'Response must use JSON-RPC 2.0.'
        Assert-SmokeCheck "$prefix.id" (($response.id -is [long] -or $response.id -is [int]) -and $response.id -eq $request.id) "Response ID must equal $($request.id)."
        Assert-SmokeCheck "$prefix.noError" (-not ($response.PSObject.Properties.Name -contains 'error')) 'Response must not contain an error.'
        Assert-SmokeCheck "$prefix.result" ($response.PSObject.Properties.Name -contains 'result') 'Response must contain a result.'
    }

    $runtime = $responses[0].result
    $created = $responses[1].result
    $saved = $responses[2].result
    $reopened = $responses[4].result
    $diagnostics = $responses[5].result
    Assert-SmokeCheck 'runtime.packaged' ($runtime.packaged -is [bool] -and $runtime.packaged) 'The engine must report packaged=true.'
    Assert-SmokeCheck 'runtime.protocolVersion' ($runtime.protocolVersion -eq 4) 'The engine must report protocolVersion=4.'
    Assert-SmokeCheck 'project.identity' (-not [string]::IsNullOrWhiteSpace($created.id)) 'Created project must have a nonempty identity.'
    foreach ($entry in @{ saved = $saved; reopened = $reopened }.GetEnumerator()) {
        $project = $entry.Value
        $prefix = "project.$($entry.Key)"
        Assert-SmokeCheck "$prefix.identity" ($project.id -ceq $created.id) 'Project identity must survive save and reopen.'
        Assert-SmokeCheck "$prefix.createdAt" ($project.createdAt -ceq $created.createdAt) 'Creation time must survive save and reopen.'
        Assert-SmokeCheck "$prefix.schemaVersion" ($project.schemaVersion -eq 4) 'Project schema must be version 4.'
        Assert-SmokeCheck "$prefix.path" ([string]::Equals($project.projectPath, $projectPath, [StringComparison]::OrdinalIgnoreCase)) 'Project must remain at the requested project.spa path.'
        foreach ($field in @('name', 'description', 'analysisCrs', 'displayCrs')) {
            Assert-SmokeCheck "$prefix.$field" ($project.$field -ceq $savedFields[$field]) "Saved field '$field' must match the requested value."
        }
        Assert-SmokeCheck "$prefix.viewState" (@($project.viewState.center).Count -eq 2 -and $project.viewState.center[0] -eq 114.125 -and $project.viewState.center[1] -eq 27.25 -and $project.viewState.zoom -eq 8.5) 'Saved center and zoom must match the requested values.'
    }
    Assert-SmokeCheck 'project.firstClose' ($responses[3].result.closed -eq $true) 'First close must release the project.'
    Assert-SmokeCheck 'project.finalClose' ($responses[6].result.closed -eq $true) 'Final close must release the project.'
    Assert-SmokeCheck 'project.file' (Test-Path -LiteralPath $projectPath -PathType Leaf) 'The saved project.spa must exist.'
    Assert-SmokeCheck 'diagnostics.ok' ($diagnostics.ok -is [bool] -and $diagnostics.ok) 'GIS diagnostics must report ok=true.'
    Assert-SmokeCheck 'diagnostics.area' ([Math]::Abs([double]$diagnostics.measuredAreaM2 - 10000.0) -le 0.000001) 'Measured rectangle area must equal 10000 m2 within 0.000001 m2.'
    Assert-SmokeCheck 'diagnostics.intersection' ([Math]::Abs([double]$diagnostics.intersectionAreaM2 - 5000.0) -le 0.000001) 'Measured intersection area must equal 5000 m2 within 0.000001 m2.'
    Assert-SmokeCheck 'diagnostics.report' (Test-Path -LiteralPath $diagnostics.reportPath -PathType Leaf) 'The diagnostics JSON report must exist.'
    Assert-SmokeCheck 'diagnostics.geopackage' (Test-Path -LiteralPath $diagnostics.geopackagePath -PathType Leaf) 'The generated GeoPackage must exist.'
    $report.ok = $true
} catch {
    $report.error = $_.Exception.Message
    throw
} finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], 'Process')
    }
    $OutputEncoding = $originalOutputEncoding
    [Console]::OutputEncoding = $originalConsoleEncoding
    $report.completedAt = [DateTime]::UtcNow.ToString('o')
    ConvertTo-Json -InputObject $report -Depth 30 | Set-Content -LiteralPath $reportPath -Encoding utf8
}
Write-Output "Frozen engine smoke passed: $reportPath"
