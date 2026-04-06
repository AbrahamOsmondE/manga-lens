# ── Load backend/.env ─────────────────────────────────────────────────────────
ifneq (,$(wildcard backend/.env))
  include backend/.env
  export
endif

# ── GCP defaults (override in backend/.env) ───────────────────────────────────
GCE_INSTANCE ?= manga-lens-server
GCE_ZONE     ?= us-central1-a

# ── Local development ─────────────────────────────────────────────────────────

.PHONY: up down logs test

## Start the local translator container
up:
	cd backend && docker-compose up -d

## Stop the local translator container
down:
	cd backend && docker-compose down

## Follow local container logs
logs:
	cd backend && docker-compose logs -f

## Run integration test against the local backend (port 5003)
test:
	pytest tests/test_translation.py -v -s

# ── GCP deployment ────────────────────────────────────────────────────────────

.PHONY: generate-key install-docker deploy logs-remote smoke-test check-ports ssh test-remote

## Generate a random MANGA_API_KEY value (copy into backend/.env)
generate-key:
	@openssl rand -hex 32

## Install Docker on the GCE instance (run once after VM creation)
install-docker:
	gcloud compute ssh $(GCE_INSTANCE) --zone=$(GCE_ZONE) -- 'bash -s' < scripts/install_docker.sh

## Copy files to GCE and start containers
deploy:
	@bash scripts/deploy.sh

## Follow container logs on the remote instance
logs-remote:
	gcloud compute ssh $(GCE_INSTANCE) --zone=$(GCE_ZONE) -- "docker-compose logs -f"

## Verify proxy auth and firewall rules on the live instance
smoke-test:
	@bash scripts/smoke_test.sh

## Quick manual check of port 8080 and 5003 reachability
check-ports:
	@echo "--- Port 8080 (proxy — expect 401) ---"
	@curl -s -o /dev/null -w "HTTP %{http_code}\n" \
	    -X POST -H "X-API-Key: wrongkey" http://$(GCE_IP):8080/translate/image
	@echo "--- Port 5003 (translator — expect BLOCKED) ---"
	@curl --connect-timeout 5 -s -o /dev/null -w "HTTP %{http_code}\n" \
	    http://$(GCE_IP):5003/docs 2>/dev/null || echo "BLOCKED (expected)"

## SSH into the GCE instance
ssh:
	gcloud compute ssh $(GCE_INSTANCE) --zone=$(GCE_ZONE)

## Run integration test against the live GCE backend (port 8080)
test-remote:
	BACKEND_URL=http://$(GCE_IP):8080 pytest tests/test_translation.py -v -s
