#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Codex OAuth end-to-end smoke test.
# Run from WSL (bash), because it reads ~/.codex/auth.json and the backend
# is reachable via the Docker network.
# ─────────────────────────────────────────────────────────────────────
set -e

BACKEND="${BACKEND:-http://localhost:3008}"   # frontend proxies /api to backend:8000
EMAIL="${CLAWITH_EMAIL:-}"
PASSWORD="${CLAWITH_PASSWORD:-}"
MODEL_NAME="${MODEL:-gpt-5.1-codex}"
LABEL="${LABEL:-Codex OAuth (test)}"
AUTH_JSON="${AUTH_JSON:-$HOME/.codex/auth.json}"

if [[ -z "$EMAIL" || -z "$PASSWORD" ]]; then
    cat <<EOF
Usage:  CLAWITH_EMAIL=... CLAWITH_PASSWORD=...  bash test_codex_oauth.sh
Optional:
  BACKEND     (default: http://localhost:8000 — change if you exposed a different port)
  MODEL       (default: gpt-5.1-codex; must be in CODEX_OAUTH_MODELS)
  LABEL       (default: "Codex OAuth (test)")
  AUTH_JSON   (default: ~/.codex/auth.json)
EOF
    exit 1
fi

if [[ ! -r "$AUTH_JSON" ]]; then
    echo "Cannot read $AUTH_JSON — did you run 'codex login' locally?"
    exit 2
fi

jq_or_py() {
    if command -v jq >/dev/null; then
        jq -r "$1"
    else
        python3 -c "import json, sys; d=json.load(sys.stdin); keys='$1'.split('.'); v=d
for k in keys:
    if k: v = v.get(k) if isinstance(v, dict) else v
print(v if v is not None else '')"
    fi
}

echo "━━━ 1. Login to Clawith → get JWT ━━━"
LOGIN_BODY=$(curl -sf -X POST "$BACKEND/api/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")
JWT=$(echo "$LOGIN_BODY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('access_token',''))")
if [[ -z "$JWT" ]]; then
    echo "Login failed: $LOGIN_BODY"
    exit 3
fi
echo "  → JWT acquired (len=${#JWT})"

echo
echo "━━━ 2. Read Codex CLI tokens from $AUTH_JSON ━━━"
ACCESS=$(python3 -c "import json; d=json.load(open('$AUTH_JSON')); print(d.get('tokens',{}).get('access_token') or d.get('access_token',''))")
REFRESH=$(python3 -c "import json; d=json.load(open('$AUTH_JSON')); print(d.get('tokens',{}).get('refresh_token') or d.get('refresh_token',''))")
if [[ -z "$ACCESS" || -z "$REFRESH" ]]; then
    echo "Could not extract access_token / refresh_token from $AUTH_JSON"
    echo "Dump (first 200 chars):"
    head -c 200 "$AUTH_JSON"
    exit 4
fi
echo "  → access token len=${#ACCESS}, refresh token len=${#REFRESH}"

echo
echo "━━━ 3. POST /paste-creds → create Codex OAuth model ━━━"
CREATE_RESP=$(curl -sf -X POST "$BACKEND/api/llm-models/codex-oauth/paste-creds" \
    -H "Authorization: Bearer $JWT" \
    -H "Content-Type: application/json" \
    -d "$(python3 -c "import json,os; print(json.dumps({
        'access_token': os.environ['ACCESS'],
        'refresh_token': os.environ['REFRESH'],
        'expires_in_seconds': 3600,
        'label': os.environ['LABEL'],
        'model': os.environ['MODEL_NAME'],
    }))" 2>&1)" ACCESS="$ACCESS" REFRESH="$REFRESH" LABEL="$LABEL" MODEL_NAME="$MODEL_NAME")
echo "  → $CREATE_RESP"
MODEL_ID=$(echo "$CREATE_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('id',''))")
if [[ -z "$MODEL_ID" ]]; then
    echo "Failed to create model"
    exit 5
fi

echo
echo "━━━ 4. Verify the model row via /start flow sanity ━━━"
START_RESP=$(curl -sf -X POST "$BACKEND/api/llm-models/codex-oauth/start" \
    -H "Authorization: Bearer $JWT" \
    -H "Content-Type: application/json" -d '{}')
echo "  → authorize_url: $(echo "$START_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('authorize_url','')[:120])")..."
echo "  → loopback_ready: $(echo "$START_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('loopback_ready'))")"

cat <<EOF

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Codex OAuth model created and wired.
  id=$MODEL_ID
  label=$LABEL
  model=$MODEL_NAME

Next step — exercise inference:
  1. Go to Clawith UI, edit an agent, set its primary model to "$LABEL".
  2. Send the agent a message; observe backend logs for a call to
     https://chatgpt.com/backend-api/responses.
  3. To watch token refresh: update oauth_expires_at in the DB to a past
     timestamp, send another message, logs should show refresh fired.

Backend logs:   docker compose logs -f backend
DB tail:        docker compose exec postgres psql -U clawith -c \\
                "SELECT id, label, model, auth_type, oauth_expires_at FROM llm_models WHERE auth_type='codex_oauth';"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EOF
