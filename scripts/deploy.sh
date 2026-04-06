#!/bin/bash
# Copies backend files to the GCE instance and starts the containers.
# Run via: make deploy
# Requires: GCE_INSTANCE, GCE_ZONE set in backend/.env
set -euo pipefail

: "${GCE_INSTANCE:?GCE_INSTANCE is not set. Add it to backend/.env}"
: "${GCE_ZONE:?GCE_ZONE is not set. Add it to backend/.env}"

echo "=== Deploying to ${GCE_INSTANCE} (${GCE_ZONE}) ==="

echo "[1/3] Copying files..."
gcloud compute scp backend/docker-compose.yml "${GCE_INSTANCE}":~/ --zone="${GCE_ZONE}"
gcloud compute scp backend/.env              "${GCE_INSTANCE}":~/ --zone="${GCE_ZONE}"
gcloud compute scp -r backend/proxy          "${GCE_INSTANCE}":~/proxy/ --zone="${GCE_ZONE}"

echo "[2/3] Building and starting containers..."
gcloud compute ssh "${GCE_INSTANCE}" --zone="${GCE_ZONE}" -- \
    "docker-compose up -d --build"

echo "[3/3] Container status:"
gcloud compute ssh "${GCE_INSTANCE}" --zone="${GCE_ZONE}" -- \
    "docker-compose ps"

echo ""
echo "Deploy complete."
echo "Run 'make logs-remote' to follow startup logs."
echo "The translator image is ~15 GB — first pull will take several minutes."
