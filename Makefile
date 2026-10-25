# Probation: see CLAUDE.md. All targets run from the repo root.
PY = uv run --project backend python
.PHONY: setup seed test eval dev api web demo reset erpnext-up erpnext-seed

setup:
	git config core.hooksPath .githooks   # pre-commit hook refuses secrets and local data
	cd backend && uv sync --python 3.12
	@test -f .env || cp .env.example .env
	@test -d frontend/node_modules || (cd frontend && npm install)

seed:
	$(PY) data/generate/seed.py
	RECORD_BACKEND=sqlite $(PY) -m probation.sync.job
	RECORD_BACKEND=sqlite $(PY) -m probation.sim.demo snapshot

test:
	cd backend && RECORD_BACKEND=sqlite uv run pytest -q

eval:
	RECORD_BACKEND=sqlite $(PY) eval/run_eval.py

api:
	cd backend && uv run uvicorn probation.api.app:app --port 8000 --reload

web:
	cd frontend && npm run dev -- --port 5173

dev:
	$(MAKE) -j2 api web

reset:
	$(PY) -m probation.sim.demo reset

demo: seed test
	@grep -q '^RECORD_BACKEND=erpnext' .env && $(MAKE) erpnext-up || true
	$(PY) -m probation.sim.demo reset
	(sleep 3 && open http://localhost:5173) &
	$(MAKE) dev

# ERPNext as the system of record (set RECORD_BACKEND=erpnext in .env). Needs Docker and `make seed` first.
erpnext-up:
	docker compose -p erpnext -f docker/erpnext-pwd.yml up -d

erpnext-seed: erpnext-up
	$(PY) docker/erpnext_load.py --fresh
	rm -f var/golden_erpnext.db var/golden_erpnext.db-wal var/golden_erpnext.db-shm
	RECORD_BACKEND=erpnext $(PY) -m probation.sync.job
	RECORD_BACKEND=erpnext $(PY) -m probation.sim.demo erpnext-snapshot
