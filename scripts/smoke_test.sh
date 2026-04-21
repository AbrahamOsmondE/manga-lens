#!/bin/bash
# Verifies the deployed proxy is working correctly (Phase 2 — Google OAuth).
# Run via: make smoke-test
set -euo pipefail

: "${GCE_IP:?GCE_IP is not set. Add it to backend/.env}"

HTTPS_BASE="https://api.manga-lens.com"
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

echo "=== Smoke test: ${HTTPS_BASE} ==="
echo ""

# No auth header → 401
STATUS=$(curl -s --ssl-no-revoke -o /dev/null -w "%{http_code}" \
    -X POST "${HTTPS_BASE}/translate/image")
check "401 with no Authorization header" "401" "${STATUS}"

# Invalid Bearer token → 401
STATUS=$(curl -s --ssl-no-revoke -o /dev/null -w "%{http_code}" \
    -X POST \
    -H "Authorization: Bearer not-a-real-token" \
    -H "Content-Type: application/json" \
    -d '{}' \
    "${HTTPS_BASE}/translate/image")
check "401 with invalid Bearer token" "401" "${STATUS}"

# Port 5003 must be unreachable from outside
STATUS=$(curl --connect-timeout 5 -s -o /dev/null -w "%{http_code}" \
    "http://${GCE_IP}:5003/docs" 2>/dev/null) || STATUS="BLOCKED"
check "port 5003 is not publicly reachable" "BLOCKED" "${STATUS}"

# Port 8080 must be unreachable from outside
STATUS=$(curl --connect-timeout 5 -s -o /dev/null -w "%{http_code}" \
    "http://${GCE_IP}:8080/translate/image" 2>/dev/null) || STATUS="BLOCKED"
check "port 8080 is not publicly reachable" "BLOCKED" "${STATUS}"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
[ "${FAIL}" -eq 0 ] || exit 1
