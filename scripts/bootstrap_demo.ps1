[CmdletBinding()]
param(
    [switch]$PullModel
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

if (-not (Test-Path -LiteralPath ".env" -PathType Leaf)) {
    [Console]::Error.WriteLine("Create .env from .env.example and replace every placeholder first.")
    exit 2
}

$envLines = [System.IO.File]::ReadAllLines((Join-Path $repoRoot ".env"))
$required = @(
    "AI_SUPPORT_SESSION_SECRET",
    "AI_SUPPORT_DEMO_LOGIN_CODE",
    "AI_SUPPORT_POSTGRES_PASSWORD",
    "AI_SUPPORT_MINIO_USER",
    "AI_SUPPORT_MINIO_PASSWORD",
    "AI_SUPPORT_GRAFANA_USER",
    "AI_SUPPORT_GRAFANA_PASSWORD"
)

foreach ($name in $required) {
    $pattern = "^$([regex]::Escape($name))="
    if (-not ($envLines | Where-Object { $_ -match $pattern })) {
        [Console]::Error.WriteLine("Missing required setting $name in .env.")
        exit 2
    }
}

if ($envLines | Where-Object { $_ -cmatch "=(change-me|replace-)" }) {
    [Console]::Error.WriteLine("Replace every change-me/replace-* placeholder in .env.")
    exit 2
}

$model = $null
foreach ($line in $envLines) {
    if ($line -match "^AI_SUPPORT_OLLAMA_MODEL=(.*)$") {
        $model = $Matches[1]
    }
}
if (-not $model) {
    $model = "llama3.1:latest"
}

$gitCommit = & git rev-parse HEAD
if ($LASTEXITCODE -ne 0) {
    throw "git rev-parse HEAD failed with exit code $LASTEXITCODE."
}
$gitStatus = & git status --short
if ($LASTEXITCODE -ne 0) {
    throw "git status --short failed with exit code $LASTEXITCODE."
}
$env:AI_SUPPORT_GIT_COMMIT = $gitCommit
$env:AI_SUPPORT_GIT_DIRTY = if ($gitStatus) { "true" } else { "false" }

try {
    Invoke-CheckedCommand -FilePath "docker" -Arguments @("compose", "config", "--quiet")
    Invoke-CheckedCommand -FilePath "docker" -Arguments @("compose", "build", "api", "minio")
    Invoke-CheckedCommand -FilePath "docker" -Arguments @(
        "compose", "up", "-d", "postgres", "minio", "mailpit", "mlflow", "ollama"
    )

    if ($PullModel) {
        Invoke-CheckedCommand -FilePath "docker" -Arguments @(
            "compose", "exec", "-T", "ollama", "ollama", "pull", $model
        )
    }
    else {
        & docker compose exec -T ollama ollama show $model 1>$null 2>$null
        if ($LASTEXITCODE -ne 0) {
            [Console]::Error.WriteLine(
                "Ollama model '$model' is absent. Re-run with -PullModel to explicitly download it."
            )
            exit 2
        }
    }

    Invoke-CheckedCommand -FilePath "docker" -Arguments @(
        "compose", "run", "--rm", "--no-deps", "api",
        "ai-support", "bootstrap-demo", "--rows", "600"
    )
    Invoke-CheckedCommand -FilePath "docker" -Arguments @(
        "compose", "up", "-d", "api", "streamlit", "prometheus", "grafana"
    )
    Invoke-CheckedCommand -FilePath "docker" -Arguments @(
        "compose", "run", "--rm", "--no-deps", "api",
        "python", "-m", "ai_support.deployment.verify", "--base-url", "http://api:8000"
    )

    Write-Output "Bootstrap and authenticated acceptance verification completed successfully."
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
