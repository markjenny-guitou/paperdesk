param(
    [ValidateSet('Start', 'Stop')]
    [string]$Action = 'Start',
    [ValidateRange(1, 65535)]
    [int]$Port = 8766,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$appPath = Join-Path $projectRoot 'app.py'
$cachePath = Join-Path $projectRoot '.cache'
$statePath = Join-Path $cachePath "launcher-$Port.json"
$stdoutPath = Join-Path $cachePath "paperdesk-$Port.stdout.log"
$stderrPath = Join-Path $cachePath "paperdesk-$Port.stderr.log"
$baseUrl = "http://127.0.0.1:$Port"

function Test-LocalPort {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $Port)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Get-LocalStatus {
    try {
        $request = [System.Net.HttpWebRequest]::Create("$baseUrl/api/health")
        $request.Proxy = $null
        $request.Timeout = 3000
        $request.ReadWriteTimeout = 3000
        $response = $request.GetResponse()
        try {
            if ($response.Headers['Server'] -notlike 'PaperDesk/*') { return $null }
            $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
            try { return ($reader.ReadToEnd() | ConvertFrom-Json) }
            finally { $reader.Dispose() }
        } finally {
            $response.Dispose()
        }
    } catch {
        return $null
    }
}

try {
    if ($Action -eq 'Stop') {
        if (-not (Test-Path -LiteralPath $statePath)) {
            Write-Host "No launcher-managed Haoge Papers process on port $Port."
            exit 0
        }
        $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $serverId = [int]$state.ProcessId
        $server = Get-Process -Id $serverId -ErrorAction SilentlyContinue
        if ($null -eq $server) {
            Remove-Item -LiteralPath $statePath
            Write-Host 'Haoge Papers is already stopped.'
            exit 0
        }
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $serverId"
        $expectedCommand = '"' + $appPath + '"'
        # PowerShell 7 can deserialize ISO timestamps as DateTime; compare instants.
        $savedStart = [DateTimeOffset]$state.StartedAtUtc
        if ($server.StartTime.ToUniversalTime().Ticks -ne $savedStart.UtcDateTime.Ticks -or
            $processInfo.ExecutablePath -ine $pythonPath -or
            -not ([string]$processInfo.CommandLine).Contains($expectedCommand)) {
            throw 'Saved process identity no longer matches. No process was stopped.'
        }
        # On Windows, the virtual-environment executable can be a redirector.
        # Stop its verified app child first so the actual HTTP server exits too.
        $appChildren = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $serverId" |
            Where-Object {
                $_.Name -in @('python.exe', 'pythonw.exe') -and
                ([string]$_.CommandLine).Contains($expectedCommand)
            })
        foreach ($appChild in $appChildren) {
            Stop-Process -Id $appChild.ProcessId -ErrorAction Stop
        }
        if ($appChildren.Count -gt 0) { $server.WaitForExit(5000) | Out-Null }
        $server.Refresh()
        if (-not $server.HasExited) { Stop-Process -Id $serverId -ErrorAction Stop }
        $server.WaitForExit(5000) | Out-Null
        Remove-Item -LiteralPath $statePath
        Write-Host "Haoge Papers stopped (port $Port)."
        exit 0
    }

    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw 'Missing .venv. See README.md for dependency installation.'
    }
    & $pythonPath -c 'import requests' 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'Missing Python dependencies. See README.md.'
    }
    if (Test-LocalPort) {
        $status = Get-LocalStatus
        $expectedProjectRoot = $projectRoot
        if ($null -eq $status -or -not $status.ok -or $status.root -ine $expectedProjectRoot) {
            throw "Port $Port is occupied by another service. Use -Port 8767 to choose another port."
        }
        Write-Host "Haoge Papers is already running: $baseUrl"
        if (-not $NoBrowser) { Start-Process $baseUrl }
        exit 0
    }

    New-Item -ItemType Directory -Path $cachePath -Force | Out-Null
    $env:PAPERDESK_PORT = [string]$Port
    $env:PAPERDESK_DATA = Join-Path $projectRoot 'data'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $server = Start-Process -FilePath $pythonPath -ArgumentList @('-u', ('"{0}"' -f $appPath)) `
        -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    @{
        ProcessId = $server.Id
        StartedAtUtc = $server.StartTime.ToUniversalTime().ToString('o')
        Port = $Port
        ProjectRoot = $projectRoot
    } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8

    $deadline = [DateTime]::UtcNow.AddSeconds(40)
    do {
        $server.Refresh()
        if ($server.HasExited) { throw "Haoge Papers exited. Check $stderrPath" }
        $status = Get-LocalStatus
        if ($null -ne $status -and $status.ok) {
            Write-Host "Haoge Papers is ready: $baseUrl"
            Write-Host 'Use stop.bat to stop the background server.'
            if (-not $NoBrowser) { Start-Process $baseUrl }
            exit 0
        }
        Start-Sleep -Milliseconds 400
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Startup timed out. Check $stderrPath and $stdoutPath; use stop.bat before retrying."
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
