param(
    [string]$Key = 'C:\Users\ASUS\Documents\Apps\oracle\ssh-key-2026-03-25.key',
    [string]$Server = 'ubuntu@158.180.17.172',
    [string]$DatabaseName = 'tiktok_video_analytics',
    [string]$AccountId = 'film_box_official',
    [int]$IntervalMinutes = 10
)

$ErrorActionPreference = 'Stop'
$ssh = 'C:\Windows\System32\OpenSSH\ssh.exe'
$scp = 'C:\Windows\System32\OpenSSH\scp.exe'
$deployRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $deployRoot
$syncScript = Join-Path $repoRoot 'tiktok_mongo_sync.py'

if (-not (Test-Path $syncScript)) {
    throw "Missing sync script: $syncScript"
}

$uriSecure = Read-Host "Paste MongoDB Atlas URI for TikTok analytics (input hidden)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($uriSecure)
try {
    $mongoUri = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

if (-not $mongoUri -or -not $mongoUri.StartsWith('mongodb+srv://')) {
    throw 'MongoDB URI must start with mongodb+srv://'
}

Write-Host "Uploading TikTok MongoDB analytics sync script..."
& $scp -i $Key $syncScript "${Server}:/tmp/tiktok_mongo_sync.py"

$payload = @{
    mongo_uri = $mongoUri
    database_name = $DatabaseName
    account_id = $AccountId
    interval_minutes = [Math]::Max(1, $IntervalMinutes)
} | ConvertTo-Json -Compress
$payloadB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($payload))

$remoteScript = @'
set -euo pipefail

payload_json=$(echo '__PAYLOAD__' | base64 -d)

cd /home/ubuntu/tik_tok_automation
install -o ubuntu -g ubuntu -m 0755 /tmp/tiktok_mongo_sync.py /home/ubuntu/tik_tok_automation/tiktok_mongo_sync.py

echo 'Installing MongoDB Python driver into TikTok virtualenv...'
/home/ubuntu/tik_tok_automation/.venv/bin/python -m pip install --no-cache-dir 'pymongo[srv]>=4.7,<5'

echo 'Writing TikTok-only Mongo config without printing secrets...'
python3 - <<'PY' "$payload_json"
import json
import shlex
import sys
from pathlib import Path

payload = json.loads(sys.argv[1])
path = Path('/home/ubuntu/tik_tok_automation/.secrets/mongo.env')
path.parent.mkdir(parents=True, exist_ok=True)
updates = {
    'TIKTOK_MONGODB_URI': payload['mongo_uri'],
    'TIKTOK_MONGO_DB_NAME': payload['database_name'],
    'TIKTOK_ACCOUNT_ID': payload['account_id'],
    'TIKTOK_MONGO_HEALTH_RETENTION_DAYS': '90',
}
existing = {}
if path.exists():
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        existing[key.strip()] = value.strip()
existing.update({key: shlex.quote(str(value)) for key, value in updates.items()})
lines = [f'{key}={value}' for key, value in existing.items()]
path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
print('tiktok_mongo_uri_present=', bool(updates['TIKTOK_MONGODB_URI']))
print('tiktok_mongo_db_name=', updates['TIKTOK_MONGO_DB_NAME'])
print('tiktok_account_id=', updates['TIKTOK_ACCOUNT_ID'])
PY

chmod 600 /home/ubuntu/tik_tok_automation/.secrets/mongo.env
chown ubuntu:ubuntu /home/ubuntu/tik_tok_automation/.secrets/mongo.env

interval_minutes=$(python3 - <<'PY' "$payload_json"
import json, sys
payload = json.loads(sys.argv[1])
print(max(1, int(payload.get('interval_minutes') or 10)))
PY
)

cat >/etc/systemd/system/tik-tok-mongo-sync.service <<'UNIT'
[Unit]
Description=TikTok Automation MongoDB Analytics Sync
After=network-online.target tik-tok-automation.service
Wants=network-online.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/tik_tok_automation
EnvironmentFile=/home/ubuntu/tik_tok_automation/.secrets/server.env
EnvironmentFile=/home/ubuntu/tik_tok_automation/.secrets/mongo.env
ExecStart=/home/ubuntu/tik_tok_automation/.venv/bin/python /home/ubuntu/tik_tok_automation/tiktok_mongo_sync.py --counts
UNIT

cat >/etc/systemd/system/tik-tok-mongo-sync.timer <<UNIT
[Unit]
Description=Run TikTok MongoDB Analytics Sync

[Timer]
OnBootSec=2min
OnUnitActiveSec=${interval_minutes}min
AccuracySec=30s
Unit=tik-tok-mongo-sync.service

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now tik-tok-mongo-sync.timer

echo 'Running first TikTok MongoDB analytics sync...'
systemctl start tik-tok-mongo-sync.service
sleep 5

echo '---TIKTOK MONGO TIMER---'
systemctl status tik-tok-mongo-sync.timer --no-pager -l
echo '---TIKTOK MONGO LAST RUN---'
systemctl status tik-tok-mongo-sync.service --no-pager -l
echo '---TIKTOK MONGO LOGS---'
journalctl -u tik-tok-mongo-sync.service -n 120 --no-pager
'@

$remoteScript = $remoteScript.Replace('__PAYLOAD__', $payloadB64)
$remoteB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($remoteScript -replace "`r", '')))

Write-Host "Installing/configuring TikTok MongoDB analytics sync on $Server..."
& $ssh -o BatchMode=yes -o ConnectTimeout=20 -i $Key $Server "echo $remoteB64 | base64 -d | bash"
