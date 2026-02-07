<!-- Copilot / AI coding agent instructions for the Garden Planner repo -->

Purpose
- Help an AI coding agent become productive quickly in this Django + React repository.

Quick orientation
- This is a Django project (backend) and a small React-style frontend in `frontend/js/`.
- Core Django apps: `plants`, `seeds`, `plantings`, `seedtrays`, `garden`, `supplies`.
- REST endpoints are implemented with Django REST Framework; most apps expose routers in `*.rest.py` and wire them via each app's `urls.py` and `gp/urls.py`.

What to read first (priority)
- `README.md` — high-level overview and local setup commands.
- `setup-venv.sh` and `setup-db.sh` — canonical dev bootstrap (creates venv, installs deps, builds frontend, initializes DB).
- `gp/urls.py` — shows how apps are mounted (see `path("plantings/", include('plantings.urls'))`).
- Example REST file: `seeds/rest.py` — shows router registrations (`router.register(r'seeds', SeedsViewSet)`).
- Frontend entry: `frontend/js/main.tsx` and other `frontend/js/*.tsx` components.

Developer workflows & commands (explicit)
- Full dev bootstrap (recommended):
  - `./setup-venv.sh`
  - Activate venv: `source venv/bin/activate`
- Run Django dev server: `python manage.py runserver`.
- Run tests: `python manage.py test` (each app has `tests.py`).
- Lint / checks: `./check-code.sh` (invokes style/lint tools configured for the venv).
- Frontend build (if editing JS/TSX):
  - `./build-frontend.sh` (uses esbuild configured in `esbuild.config.json` to bundle to `static/`).

Project-specific conventions
- API routers are declared in `*.rest.py` inside each app, then surfaced via that app's `urls.py`. Look for `router = routers.DefaultRouter()` and `router.register(...)`.
- Models provide small helper serializers (e.g., `as_json()` on `GardenSquare`). Use these helpers rather than re-deriving geometry logic.
- Frontend components are small React/TSX files under `frontend/js/` — changes to these require the esbuild-based build step above.
- Local secrets: `gp/local_settings.py` is generated from `gp/local_settings.py.template` by setup scripts; do not commit `local_settings.py` or `gp/secretkey.txt`.

Integration points & external dependencies
- Python dependencies: `requirements.txt` (Django, DRF, others) — installed by `setup-venv.sh`.
- Node/npm for frontend; esbuild bundles frontend to `static/` via `esbuild.config.json`.
- SQLite is used by default (`db.sqlite3` for local dev). Production may use Postgres if `gp/local_settings.py` is changed.

Common change patterns & examples
- To add an API resource:
  1. Add models in the appropriate app (e.g., `plantings/models.py`).
  2. Add serializers/viewsets in `app/rest.py` and register with `router.register(...)`.
  3. Ensure the app `urls.py` includes the router and is mounted in `gp/urls.py`.
- To update frontend UI: change `frontend/js/*.tsx`, then run the frontend build and reload the Django server static files.

What to avoid or watch for
- Do not commit secrets or `gp/local_settings.py`.
- The repo uses shell scripts for canonical setup — replicate their behavior rather than inventing alternate bootstrap logic.
- Tests and linting assume the venv created by `setup-venv.sh` (paths/tools in `check-code.sh`).

If you modify code, helpful PR checklist
- Run `./check-code.sh` and `python manage.py test` locally.
- If the frontend changed: `./build-frontend.sh` and verify `static/main.js` updates.
- Update README if you add or change developer-facing scripts or new top-level URLs.

Questions or gaps
- If you need endpoint names or router details, inspect `*.rest.py` files (e.g., `seeds/rest.py`) and app `urls.py` for exact route paths.
