#!/usr/bin/env bash
# Verify a fine-grained PAT before pasting it into a third-party cron service.
# Usage: ./scripts/verify-pinger-token.sh github_pat_xxxxx
set -euo pipefail

TOKEN="${1:?usage: $0 <github_pat_...>}"
REPO="AmroAbujabal/mbp-refurb-monitor"
API="https://api.github.com"
H_ACCEPT="Accept: application/vnd.github+json"
H_VER="X-GitHub-Api-Version: 2022-11-28"
AUTH="Authorization: Bearer $TOKEN"

echo "1. How many repos can this token see?"
COUNT=$(curl -s -H "$H_ACCEPT" -H "$AUTH" -H "$H_VER" "$API/user/repos?per_page=100" \
        | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo "?")
if [ "$COUNT" = "1" ]; then
  echo "   OK: 1 repo only - correctly scoped."
else
  echo "   WARNING: sees $COUNT repos. A pinger token should reach exactly 1."
  echo "   Do NOT paste this into a third-party service. Re-scope it."
fi

echo "2. Can it write code? (it must NOT)"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X PUT \
  -H "$H_ACCEPT" -H "$AUTH" -H "$H_VER" \
  -d '{"message":"scope probe","content":"cHJvYmU="}' \
  "$API/repos/$REPO/contents/.scope-probe")
if [ "$CODE" = "403" ] || [ "$CODE" = "404" ]; then
  echo "   OK: content write refused (HTTP $CODE)."
else
  echo "   WARNING: content write returned HTTP $CODE - token has more power than needed."
fi

echo "3. Can it trigger the workflow? (it must)"
WID=$(curl -s -H "$H_ACCEPT" -H "$AUTH" -H "$H_VER" "$API/repos/$REPO/actions/workflows" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["workflows"][0]["id"])')
RESP=$(curl -s -D - -o /dev/null -X POST \
  -H "$H_ACCEPT" -H "$AUTH" -H "$H_VER" -d '{"ref":"main"}' \
  "$API/repos/$REPO/actions/workflows/$WID/dispatches")
CODE=$(printf '%s' "$RESP" | awk '/^HTTP/{c=$2} END{print c}')
NEEDS=$(printf '%s' "$RESP" | tr -d '\r' | awk -F': ' 'tolower($1)=="x-accepted-github-permissions"{print $2}')
if [ "$CODE" = "204" ]; then
  echo "   OK: dispatch accepted (HTTP 204). A run should appear shortly."
else
  echo "   FAIL: dispatch returned HTTP $CODE - the pinger will not work."
  if [ "$CODE" = "403" ] || [ "$CODE" = "404" ]; then
    [ -n "$NEEDS" ] && echo "   GitHub says this endpoint requires: $NEEDS"
    echo "   Almost always: Actions is set to 'Read-only'. It must be 'Read and write'."
    echo "   (Read-only can list workflows but not trigger them, so step 3 is the"
    echo "    only check that catches it.)"
  fi
  exit 1
fi
echo
echo "Token looks correctly scoped. Workflow id for cron-job.org: $WID"
