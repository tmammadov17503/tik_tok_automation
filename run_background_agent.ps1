$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeRoot = Join-Path $projectRoot ".runtime"
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null

$existing = Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -like "*Video_Generator_Agent*app.py*"
}

if ($existing) {
  Write-Output "Video Generator Agent is already running in the background."
  exit 0
}

$pythonCommand = (Get-Command python -ErrorAction Stop).Source
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stdoutLog = Join-Path $runtimeRoot "agent-$timestamp.out.log"
$stderrLog = Join-Path $runtimeRoot "agent-$timestamp.err.log"

Start-Process `
  -FilePath $pythonCommand `
  -ArgumentList "app.py" `
  -WorkingDirectory $projectRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdoutLog `
  -RedirectStandardError $stderrLog

Write-Output "Video Generator Agent started in the background."
