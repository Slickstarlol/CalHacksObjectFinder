# RecallAI dev helper (Windows PowerShell).
$Usage = @'
RecallAI dev helper (Windows PowerShell). Run from anywhere:

    .\scripts\dev.ps1 setup            # pip + npm installs, then migrate old data
    .\scripts\dev.ps1 server           # FastAPI on :8000 (search page, API, bridge)
    .\scripts\dev.ps1 camera --camera 1  # webcam loop (args go to the webcam script)
    .\scripts\dev.ps1 register --list  # register belongings (args go to register)
    .\scripts\dev.ps1 ask "where is my mouse?"
    .\scripts\dev.ps1 dashboard        # frontend/dashboard on :5173
    .\scripts\dev.ps1 live-dashboard   # frontend/live-dashboard on :5174
    .\scripts\dev.ps1 spacetime        # local SpacetimeDB server on :3000
    .\scripts\dev.ps1 publish          # publish spacetimedb/ as "recallai"
    .\scripts\dev.ps1 test             # every test suite (Python + 3 npm projects)
    .\scripts\dev.ps1 test-fast        # Python tests without model downloads
    .\scripts\dev.ps1 migrate          # move old root-level data into data/

If scripts are blocked:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
'@
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
# Plain $args (no param block) so flags like --list pass straight through.
$Command = if ($args.Count -gt 0) { [string]$args[0] } else { "help" }
$Rest = @(if ($args.Count -gt 1) { $args[1..($args.Count - 1)] | ForEach-Object { [string]$_ } })
# Makes `import recallai` work even without `pip install -e backend`.
$Backend = Join-Path $Root "backend"
$env:PYTHONPATH = if ($env:PYTHONPATH) { $Backend + [IO.Path]::PathSeparator + $env:PYTHONPATH } else { $Backend }
$Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }

function Run([string]$Dir, [string]$Exe, [string[]]$CmdArgs) {
    Push-Location (Join-Path $Root $Dir)
    try {
        & $Exe @CmdArgs
        if ($LASTEXITCODE -ne 0) { throw "'$Exe $($CmdArgs -join ' ')' failed in $Dir (exit $LASTEXITCODE)" }
    } finally { Pop-Location }
}

switch ($Command) {
    "setup" {
        Run "." $Python @("-m", "pip", "install", "-r", "backend/requirements.txt")
        Run "." $Python @("-m", "pip", "install", "-e", "backend")
        foreach ($d in "spacetimedb", "frontend/live-dashboard", "frontend/dashboard") { Run $d "npm" @("install") }
        Run "." $Python @("scripts/migrate_layout.py")
    }
    "server"         { Run "." $Python (@("-m", "uvicorn", "recallai.api.server:app", "--host", "0.0.0.0", "--port", "8000") + $Rest) }
    "camera"         { Run "." $Python (@("-m", "recallai.vision.webcam") + $Rest) }
    "register"       { Run "." $Python (@("-m", "recallai.vision.register") + $Rest) }
    "ask"            { Run "." $Python (@("-m", "recallai.ai.agent") + $Rest) }
    "check-grok"     { Run "." $Python (@("-m", "recallai.ai.check_grok") + $Rest) }
    "sync-once"      { Run "." $Python @("-m", "recallai.sync.spacetime_bridge", "--once") }
    "dashboard"      { Run "frontend/dashboard" "npm" @("run", "dev") }
    "live-dashboard" { Run "frontend/live-dashboard" "npm" @("run", "dev") }
    "spacetime"      { Run "." "spacetime" @("start") }
    "publish"        { Run "spacetimedb" "spacetime" (@("publish", "--server", "local", "recallai") + $Rest) }
    "generate"       { Run "frontend/live-dashboard" "npm" @("run", "spacetime:generate") }
    "migrate"        { Run "." $Python (@("scripts/migrate_layout.py") + $Rest) }
    "test-fast"      { Run "." $Python @("-m", "pytest", "-q", "-m", "not slow") }
    "test" {
        Run "." $Python @("-m", "pytest", "-q")
        Run "spacetimedb" "npx" @("tsc", "--noEmit", "-p", ".")
        Run "spacetimedb" "npx" @("vitest", "run")
        Run "frontend/live-dashboard" "npm" @("run", "build")
        Run "frontend/live-dashboard" "npx" @("vitest", "run")
        Run "frontend/dashboard" "npm" @("run", "build")
        Run "frontend/dashboard" "npm" @("run", "lint")
    }
    default {
        Write-Host $Usage
        if ($Command -ne "help") { Write-Host "Unknown command: $Command"; exit 1 }
    }
}
