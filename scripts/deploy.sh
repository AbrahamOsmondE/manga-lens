#!/bin/bash
# Deploys to the GCE instance via SSH (no scp required).
# Run via: make deploy
set -euo pipefail

: "${GCE_INSTANCE:?GCE_INSTANCE is not set. Add it to backend/.env}"
: "${GCE_ZONE:?GCE_ZONE is not set. Add it to backend/.env}"

echo "=== Deploying to ${GCE_INSTANCE} (${GCE_ZONE}) ==="

echo "[1/3] Pushing .env to server..."
ENV_CONTENTS=$(cat backend/.env)
gcloud compute ssh "${GCE_INSTANCE}" --zone="${GCE_ZONE}" -- \
    "cat > ~/manga-lens/backend/.env << 'ENVEOF'
${ENV_CONTENTS}
ENVEOF"

echo "[2/3] Building and starting containers..."
gcloud compute ssh "${GCE_INSTANCE}" --zone="${GCE_ZONE}" -- \
    "cd ~/manga-lens && git pull origin main && cd backend && docker-compose up -d --build"

echo "[3/3] Container status:"
gcloud compute ssh "${GCE_INSTANCE}" --zone="${GCE_ZONE}" -- \
    "cd ~/manga-lens/backend && docker-compose ps"

echo ""
echo "Deploy complete."
echo "Run 'make logs-remote' to follow startup logs."
echo "The translator image is ~15 GB — first pull will take several minutes."
