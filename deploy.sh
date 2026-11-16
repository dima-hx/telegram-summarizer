#!/usr/bin/env bash
# Deploy to Azure App Service (Linux, Python). Requires: az login, config.ini, azure_session.txt (python make_session.py).
#   bash deploy.sh            # create app if missing, push settings, deploy code
#   bash deploy.sh --code     # only deploy code (keep settings as they are)
set -euo pipefail
cd "$(dirname "$0")"

RG=RezOn
PLAN=RezOn-Linux-ServicePlan
APP=evromisto-summarizer
RUNTIME="PYTHON:3.12"

ini() { python -I -c "import configparser,sys; c=configparser.ConfigParser(); c.read('config.ini'); print(c.get(sys.argv[1], sys.argv[2]))" "$1" "$2"; }

if [[ "${1:-}" != "--code" ]]; then
  if ! az webapp show -g "$RG" -n "$APP" -o none 2>/dev/null; then
    echo "Creating web app $APP on $PLAN..."
    az webapp create -g "$RG" -p "$PLAN" -n "$APP" --runtime "$RUNTIME" -o none
  fi

  echo "Configuring Always On + startup command..."
  az webapp config set -g "$RG" -n "$APP" --always-on true --startup-file "python app.py" \
    --ftps-state Disabled --http20-enabled true -o none
  az webapp update -g "$RG" -n "$APP" --https-only true -o none

  [[ -s azure_session.txt ]] || { echo "azure_session.txt missing: run python make_session.py first" >&2; exit 1; }

  echo "Setting app settings (values not printed)..."
  # ALERTS_LIVE is only set on first deploy so a redeploy never flips shadow mode back on/off
  existing_live=$(az webapp config appsettings list -g "$RG" -n "$APP" --query "[?name=='ALERTS_LIVE'].value | [0]" -o tsv)
  az webapp config appsettings set -g "$RG" -n "$APP" -o none --settings \
    API_ID="$(ini telegram api_id)" \
    API_HASH="$(ini telegram api_hash)" \
    CHAT_NAME="$(ini telegram chat_name)" \
    BOT_CHANNEL="$(ini telegram bot_channel)" \
    OPENAI_API_KEY="$(ini openai api_key)" \
    SESSION_VALUE="$(cat azure_session.txt)" \
    ALERTS_LIVE="${existing_live:-false}" \
    STATE_DIR=/home/data \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    WEBSITES_ENABLE_APP_SERVICE_STORAGE=true
fi

echo "Packaging code..."
PKG="$(mktemp -d)/app.zip"
python -I - "$PKG" <<'EOF'
import sys, zipfile
files = ['app.py', 'alerts.py', 'config.py', 'daily.py', 'messages_functions.py', 'state.py', 'summarizer.py', 'requirements.txt']
with zipfile.ZipFile(sys.argv[1], 'w', zipfile.ZIP_DEFLATED) as z:
    for f in files:
        z.write(f)
print('packaged', len(files), 'files')
EOF

echo "Deploying..."
az webapp deploy -g "$RG" -n "$APP" --src-path "$PKG" --type zip -o none
rm -f "$PKG"
echo "Done. Health: https://$APP.azurewebsites.net/   Logs: az webapp log tail -g $RG -n $APP"
