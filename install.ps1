# ClawSmith installer for Windows PowerShell
# Usage:
#   irm https://raw.githubusercontent.com/Cool-Coder174/ClawSmith/main/install.ps1 | iex
#   or: .\install.ps1

$ErrorActionPreference = "Stop"

$Repo = "https://github.com/Cool-Coder174/ClawSmith.git"
$MinPython = [version]"3.11"
$InstallDir = if ($env:CLAWSMITH_DIR) { $env:CLAWSMITH_DIR } else { Join-Path $HOME "ClawSmith" }

function Write-Info($msg)  { Write-Host "[clawsmith] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "[clawsmith] $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "[clawsmith] $msg" -ForegroundColor Yellow }
function Write-Fail($msg)  { Write-Host "[clawsmith] $msg" -ForegroundColor Red; exit 1 }

# -- detect python ---------------------------------------------------------

$PythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver -and ([version]$ver -ge $MinPython)) {
            $PythonCmd = $cmd
            $PythonVer = $ver
            break
        }
    } catch { }
}

# -- main ------------------------------------------------------------------

Write-Info "ClawSmith installer"
Write-Host ""

if (-not $PythonCmd) {
    Write-Fail "Python $MinPython+ is required. Install from https://python.org"
}
Write-Ok "Python $PythonVer found ($PythonCmd)"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Fail "git is required but not found."
}
Write-Ok "git found"

# Clone or update
if (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Info "Updating existing install at $InstallDir"
    git -C $InstallDir pull --ff-only 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Warn "git pull failed; continuing with existing code" }
} else {
    Write-Info "Cloning ClawSmith to $InstallDir"
    git clone $Repo $InstallDir
    if ($LASTEXITCODE -ne 0) { Write-Fail "git clone failed" }
}

Set-Location $InstallDir

# Install via pip
Write-Info "Installing ClawSmith..."
& $PythonCmd -m pip install -e ".[dev]" --quiet
if ($LASTEXITCODE -ne 0) { Write-Fail "pip install failed" }
Write-Ok "Installed via pip"

# Ensure the Scripts directory is on PATH
if (-not (Get-Command clawsmith -ErrorAction SilentlyContinue)) {
    $ScriptsDir = & $PythonCmd -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))" 2>$null
    if (-not $ScriptsDir) {
        $UserBase = & $PythonCmd -m site --user-base 2>$null
        if ($UserBase) { $ScriptsDir = Join-Path $UserBase "Scripts" }
    }

    if ($ScriptsDir -and (Test-Path (Join-Path $ScriptsDir "clawsmith.exe"))) {
        $CurrentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($CurrentUserPath -notlike "*$ScriptsDir*") {
            Write-Info "Adding $ScriptsDir to user PATH..."
            [Environment]::SetEnvironmentVariable("Path", "$CurrentUserPath;$ScriptsDir", "User")
        }
        $env:PATH = "$env:PATH;$ScriptsDir"
        Write-Ok "clawsmith CLI added to PATH"
    } else {
        Write-Warn "clawsmith installed but could not locate the Scripts directory."
        Write-Warn "You may need to manually add the Python Scripts directory to your PATH."
    }
} else {
    Write-Ok "clawsmith CLI is on PATH"
}

Write-Host ""
Write-Ok "Installation complete!"
Write-Host ""
Write-Info "Next steps:"
Write-Host "  cd $InstallDir"
Write-Host "  clawsmith onboard       # guided first-run setup"
Write-Host "  clawsmith doctor        # verify environment"
Write-Host "  clawsmith smoke-test    # quick integration check"
Write-Host ""
