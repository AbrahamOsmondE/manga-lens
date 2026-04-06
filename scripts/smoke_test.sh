#!/bin/bash
# Verifies the deployed proxy is working correctly.
# Run via: make smoke-test
# Requires: GCE_IP, MANGA_API_KEY set in backend/.env
set -euo pipefail

: "${GCE_IP:?GCE_IP is not set. Add it to backend/.env}"
: "${MANGA_API_KEY:?MANGA_API_KEY is not set. Add it to backend/.env}"

BASE="http://${GCE_IP}:8080"
PASS=0
FAIL=0

check() {
    local desc="$1" expected="$2" actual="$3"
    if [ "$actual" = "$expected" ]; then
        echo "  PASS  ${desc}"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  ${desc}  (expected: ${expected}, got: ${actual})"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== Smoke test: ${BASE} ==="
echo ""

# Wrong key → 401
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST -H "X-API-Key: wrongkey" "${BASE}/translate/image")
check "401 on wrong API key" "401" "${STATUS}"

# Valid key, empty body → 422 (FastAPI validation — proves request reached the proxy)
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST \
    -H "X-API-Key: ${MANGA_API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{}' \
    "${BASE}/translate/image")
check "valid key reaches proxy (expect 422 from FastAPI validation)" "422" "${STATUS}"

# Port 5003 must be unreachable from the outside
STATUS=$(curl --connect-timeout 5 -s -o /dev/null -w "%{http_code}" \
    "http://${GCE_IP}:5003/docs" 2>/dev/null) || STATUS="BLOCKED"
check "port 5003 is not publicly reachable" "BLOCKED" "${STATUS}"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
[ "${FAIL}" -eq 0 ] || exit 1
