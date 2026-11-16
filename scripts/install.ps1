$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env. Add model keys there when you are ready to grade real essays."
}

$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($docker) {
  docker compose version | Out-Null
  docker compose up -d --build
  Write-Host ""
  Write-Host "Waiting for the app to become healthy..."
  for ($i = 0; $i -lt 60; $i++) {
    try {
      Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8000/healthz" | Out-Null
      Write-Host "Ready: http://127.0.0.1:8000"
      Write-Host "Demo login: teacher@demo.local / Demo2026!"
      $skillTarget = Join-Path $HOME ".agents\skills\deutsch-grader"
      New-Item -ItemType Directory -Force -Path $skillTarget | Out-Null
      Copy-Item -Recurse -Force ".\.agents\skills\deutsch-grader\*" $skillTarget
      Write-Host "Cindy Skill installed: $skillTarget"
      exit 0
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  Write-Host "Container started but health check is still pending. Run: docker compose logs -f"
  exit 1
}

Write-Host "Docker not found. Falling back to a local Python environment."
$python = Get-Command py -ErrorAction SilentlyContinue
if (-not $python) {
  $python = Get-Command python -ErrorAction SilentlyContinue
}
if (-not $python) {
  Write-Host "Python not found. Install Python 3.12 from python.org, then rerun."
  exit 1
}

& $python.Source -m venv .venv
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -e ".[dev]"
& ".venv\Scripts\python.exe" -m app.bootstrap

Start-Process -FilePath ".venv\Scripts\uvicorn.exe" -ArgumentList "app.main:app --host 127.0.0.1 --port 8000" -RedirectStandardOutput "server.log" -RedirectStandardError "server.err.log"
Start-Process -FilePath ".venv\Scripts\grading-worker.exe" -RedirectStandardOutput "worker.log" -RedirectStandardError "worker.err.log"
Start-Sleep -Seconds 2
Write-Host "Ready: http://127.0.0.1:8000"
Write-Host "Demo login: teacher@demo.local / Demo2026!"
$skillTarget = Join-Path $HOME ".agents\skills\deutsch-grader"
New-Item -ItemType Directory -Force -Path $skillTarget | Out-Null
Copy-Item -Recurse -Force ".\.agents\skills\deutsch-grader\*" $skillTarget
Write-Host "Cindy Skill installed: $skillTarget"
