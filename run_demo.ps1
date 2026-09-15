$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$app = Join-Path $projectRoot "citypercept_app.py"

$python = $null
$pythonCandidates = @(
    (Join-Path $projectRoot ".demo_venv\Scripts\python.exe"),
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    "D:\conda\python.exe"
)

foreach ($candidate in $pythonCandidates) {
    if (Test-Path $candidate) {
        $python = $candidate
        break
    }
}

if (-not $python) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $python = $pythonCommand.Source
    }
}

if (-not $python) {
    throw "Python was not found. Create .demo_venv or install Python and add it to PATH."
}

& $python -c "import streamlit, PIL, httpx"
if ($LASTEXITCODE -ne 0) {
    throw "Demo dependencies are missing. Run: $python -m pip install -r requirements.txt"
}

$labelerScript = Get-ChildItem -Path $projectRoot -Recurse -Filter "openai_image_labeler.py" -File |
    Where-Object { $_.Directory.Name -eq "scripts" } |
    Select-Object -First 1

if (-not $labelerScript) {
    throw "Required demo file is missing: openai_image_labeler.py"
}

$labelerRoot = Split-Path -Parent $labelerScript.Directory.FullName
$requiredFiles = @(
    $app,
    $labelerScript.FullName,
    (Join-Path $labelerRoot "prompt.md"),
    (Join-Path $labelerRoot "schemas\annotation_schema.json")
)

$missingFiles = $requiredFiles | Where-Object { -not (Test-Path $_) }
if ($missingFiles) {
    throw "Required demo files are missing: $($missingFiles -join ', ')"
}

$port = 8501
while ($true) {
    try {
        $probe = [System.Net.Sockets.TcpListener]::new(
            [System.Net.IPAddress]::Loopback,
            $port
        )
        $probe.Start()
        $probe.Stop()
        break
    }
    catch {
        $port += 1
    }
}

Write-Host "Starting CityPercept AI at http://127.0.0.1:$port"

& $python -m streamlit run $app `
    --server.address 127.0.0.1 `
    --server.port $port `
    --server.headless true `
    --browser.gatherUsageStats false
