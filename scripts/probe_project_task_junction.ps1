# Pure Windows resolver probe for a managed project-task workspace.
# Run from PowerShell with: pwsh -File scripts/probe_project_task_junction.ps1
# This does not exercise the authenticated HTTP route or prove gate-ON safety.
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$modulePath = Join-Path $repoRoot 'src\octop\infra\backend\project_task_file_paths.py'
if (-not (Test-Path -LiteralPath $modulePath -PathType Leaf)) {
    throw "Project-task path module not found: $modulePath"
}

$tempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\')
$probeRoot = Join-Path $tempRoot ('xiongbao-030-junction-probe-' + [guid]::NewGuid().ToString('N'))
$probeFull = [IO.Path]::GetFullPath($probeRoot)
$tempPrefix = $tempRoot + '\'
if (-not $probeFull.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Probe root escaped the intended temporary directory.'
}
if (Test-Path -LiteralPath $probeFull) {
    throw 'Probe root already exists; refusing to touch it.'
}

$managed = Join-Path $probeFull 'managed'
$outside = Join-Path $probeFull 'outside'
$junction = Join-Path $managed 'link'
$created = $false
try {
    New-Item -ItemType Directory -Path $managed -Force | Out-Null
    $created = $true
    New-Item -ItemType Directory -Path $outside -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $managed 'seed.txt') -Value 'managed' -NoNewline
    Set-Content -LiteralPath (Join-Path $outside 'secret.txt') -Value 'outside' -NoNewline
    New-Item -ItemType Junction -Path $junction -Target $outside | Out-Null

    $pythonCode = @'
import pathlib, runpy, sys
module = runpy.run_path(sys.argv[1])
root = pathlib.Path(sys.argv[2])
resolve = module['resolve_project_task_workspace_path']
error = module['ProjectTaskPathError']
positive = resolve(root, 'seed.txt')
assert positive == (root / 'seed.txt').resolve(), positive
print('managed positive: PASS')
for relative in ('link/secret.txt', 'link/new.txt'):
    try:
        resolve(root, relative)
    except error:
        print('junction escape refused: PASS', relative)
    else:
        raise SystemExit('UNSAFE: junction escape accepted: ' + relative)
'@
    # -S avoids unrelated local site-package startup failures in a bare Python probe.
    py -3.11 -S -c $pythonCode $modulePath $managed
    if ($LASTEXITCODE -ne 0) { throw "Windows junction probe failed: $LASTEXITCODE" }
}
finally {
    if ($created -and (Test-Path -LiteralPath $probeFull)) {
        $resolvedRoot = (Resolve-Path -LiteralPath $probeFull).ProviderPath
        if ($resolvedRoot -ne $probeFull -or -not $resolvedRoot.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Resolved cleanup path is not the newly created probe root; refusing deletion.'
        }
        $junctionFull = [IO.Path]::GetFullPath($junction)
        if (-not $junctionFull.StartsWith($probeFull + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Junction cleanup path escaped the probe root; refusing deletion.'
        }
        if (Test-Path -LiteralPath $junctionFull) {
            # Windows PowerShell 5.1 Remove-Item can throw NullReferenceException
            # on junctions. Delete only the link entry, never its target tree.
            [IO.Directory]::Delete($junctionFull, $false)
            if (Test-Path -LiteralPath $junctionFull) {
                throw 'Junction link still exists after non-recursive deletion.'
            }
        }
        Remove-Item -LiteralPath $probeFull -Recurse -Force
        Write-Output 'probe cleanup: PASS'
    }
}
