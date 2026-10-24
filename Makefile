# Probation: see CLAUDE.md. All targets run from the repo root.
PY = uv run --project backend python
.PHONY: setup seed test eval dev api web demo reset

setup:
	cd backend && uv sync --python 3.12
	@test -f .env || cp .env.example .env
	@test -d frontend/node_modules || (cd frontend && npm install)

seed:
	$(PY) data/generate/seed.py
	$(PY) -m probation.sync.job

test:
	cd backend && uv run pytest -q

eval:
	$(PY) eval/run_eval.py

api:
	cd backend && uv run uvicorn probation.api.app:app --port 8000 --reload

web:
	cd frontend && npm run dev -- --port 5173

dev:
	$(MAKE) -j2 api web

reset:
	$(PY) -m probation.sim.demo reset

demo: seed test
	$(PY) -m probation.sim.demo reset
	(sleep 3 && open http://localhost:5173) &
	$(MAKE) dev
