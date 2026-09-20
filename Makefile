PYTHON ?= python
COMPOSE_EXTRA ?= -f deploy/docker-compose.local.yml
COMPOSE = docker compose --env-file deploy/.env -f deploy/docker-compose.yml $(COMPOSE_EXTRA)
.PHONY: up down test eval eval-groq seed
up:
	$(COMPOSE) up -d --build --wait
down:
	$(COMPOSE) down
test:
	$(PYTHON) -m pytest
eval:
	$(COMPOSE) exec -T -e ACBI_REPORT_DIR=/tmp/acbi-phase0 backend python scripts/verify_phase0.py
	docker cp acbi-backend-1:/tmp/acbi-phase0/docs/phase0-verification.json docs/phase0-verification.json
	docker cp acbi-backend-1:/tmp/acbi-phase0/data/eval/results.local.json data/eval/results.local.json
seed:
	$(PYTHON) scripts/prepare_warehouse.py

eval-groq:
	$(COMPOSE) exec -T backend python scripts/eval_groq.py --suite all < deploy/seed-credentials.txt
