#!/bin/bash
# Installs Docker and Docker Compose on the GCE instance.
# Run via: make install-docker  (executed remotely over SSH)
set -euo pipefail

echo "=== Installing Docker ==="
sudo apt-get update -q
sudo apt-get install -y docker.io docker-compose

sudo usermod -aG docker "$USER"

echo ""
echo "Docker:         $(docker --version)"
echo "Docker Compose: $(docker-compose --version)"
echo ""
echo "IMPORTANT: Log out and SSH back in (or run 'newgrp docker') before running 'make deploy'."
