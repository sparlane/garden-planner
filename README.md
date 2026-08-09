# Garden Planner

Garden Planner is a Django-based web application to design and manage garden layouts, seed stock, and planting activity. The repository contains a Django backend (Python) and a JavaScript frontend (React components), plus helper scripts to bootstrap local development.

Table of contents
- Features
- Tech stack
- Prerequisites
- Quick start (recommended)
- Production deploy (PostgreSQL)
- Development notes
- API endpoints (important)
- Project layout (high level)
- Contributing
- License
- Contact

Features
- Garden layout management
  - GardenArea, GardenBed, GardenRow, and GardenSquare models to represent garden areas, beds, rows and squares.
  - GardenSquare includes an `as_json()` helper for serialising position/size and bed/area metadata.

- Plants and varieties
  - PlantFamily, Plant and PlantVariety models.
  - Plants and varieties store metadata useful for planning: spacing, inter-row spacing, plants per square foot, germination and maturity ranges, notes.

- Seed suppliers and seed stocks
  - Supplier model for seed suppliers.
  - Seeds model linking supplier -> plant variety with optional supplier code and URL.
  - SeedPacket model to track purchase date, sow-by date, empty flag and notes.
  - Frontend UI to view/add seeds and seed packets (see `frontend/js/seeds.js`).

- Plantings & lifecycle tracking
  - Models for:
    - SeedTrayPlanting (seed trays)
    - GardenRowDirectSowPlanting (direct-sow into rows)
    - GardenSquareDirectSowPlanting (direct-sow into squares)
    - SpecificPlant and SpecificPlantLocation (germination and individual location history)
    - GardenSquareTransplant (read-only legacy aggregate transplants)
  - Planting attributes include dates, quantity, location, notes and a `removed` flag to mark completed/removed plantings.
  - Seed-tray planting and cell quantities mean seeds or seed clusters sown. Observed `SpecificPlant` seedlings are counted separately and may exceed those quantities for multigerm crops.
  - Individual plant locations are the source of truth for new transplant workflows. Legacy aggregate rows remain visible and completable but cannot be created through the REST API.
  - Views compute germination/maturity dates (using variety/plant metadata) and return JSON summaries without double-counting legacy and individual transplant representations.

- REST API (Django REST Framework)
  - REST viewsets / routers for seeds, seed packets, plantings and varieties.
  - Plantings router exposes direct-sow, seed-tray, read-only legacy transplant, specific-plant, and specific-plant-location resources.
  - Seeds router exposes: `seeds`, `packets` and `packets/all`.

- Workspace profile and ownership
  - One configured workspace owns every catalog, garden, tray, and planting record.
  - The workspace can switch between Garden and Nursery presentation without converting or deleting data.
  - Workspace settings include currency, default tax percentage, IANA timezone, and metric or imperial display preferences.

- React-based frontend components
  - Frontend lives in `frontend/js/` (React components) and uses Bootstrap and jQuery for the UI; examples include `menu.js`, `planting.js`, `seeds.js`, and `plants.js`.
  - Frontend build is wired up via the repository `package.json` and build scripts.

- Dev / helper scripts
  - `setup-venv.sh` creates a Python virtual environment, installs Python dependencies, builds the frontend (npm), creates SQLite development settings when needed, generates a secret key, applies migrations, and collects static files.
  - `setup-db.sh` applies `DB_HOST`, `DB_NAME`, `DB_USER`, and `DB_PASS` to explicitly selected PostgreSQL settings. `start-wsgi.sh` serves the app in deployments.
  - `check-code.sh` for style/lint checks (pycodestyle, pylint).

- Local-first defaults
  - `gp/local_settings.dev.py.template` is the canonical local configuration and uses `db.sqlite3`.
  - `gp/local_settings.postgresql.py.template` is available for deployments that use PostgreSQL.

Tech stack
- Django (Python) backend, Django REST Framework for APIs
- React + Bootstrap (JavaScript) frontend components
- SQLite for local development; PostgreSQL for deployment
- Node/npm for frontend build; esbuild configuration present
- Shell scripts for environment setup and build automation

Prerequisites
- Git
- Python 3.12+ (required by Django 6)
- Node.js 22.22+ and npm (required by React Router)
- Bash-compatible shell for `setup-venv.sh`

Quick start (recommended)
1. Clone the repo:
   ```bash
   git clone https://github.com/sparlane/garden-planner.git
   cd garden-planner
   ```

2. Run the setup script. It creates `venv`, installs the dependencies declared in `requirements.txt`, builds the frontend, creates SQLite settings and a secret key when absent, applies migrations, and collects static files:
   ```bash
   ./setup-venv.sh
   ```

3. Activate the virtualenv (if not already active):
   - macOS / Linux:
     ```bash
     source venv/bin/activate
     ```
   - Windows (PowerShell):
     ```powershell
     .\venv\Scripts\Activate.ps1
     ```

4. Confirm the installation and optionally create a Django superuser:
   ```bash
   python manage.py check
   python manage.py createsuperuser
   ```

5. Run the development server:
   ```bash
   python manage.py runserver
   ```
   Open http://127.0.0.1:8000

Production deploy (PostgreSQL)

Install and initialise PostgreSQL, then copy the deployment template before running `setup-venv.sh`; the setup script preserves an existing `gp/local_settings.py`:

  ```bash
  cp gp/local_settings.postgresql.py.template gp/local_settings.py
  chmod 600 gp/local_settings.py
  ```

Before setup:

- Set `ALLOWED_HOSTS` to every hostname that serves the site.
- Set `CSRF_TRUSTED_ORIGINS` to every public origin, including its scheme.
- Fill in all four database values directly, or provide `DB_HOST`, `DB_NAME`, `DB_USER`, and `DB_PASS` in the deployment environment.
- Leave `CURRENT_WORKSPACE_ID = 1` for an existing single-workspace installation. Selecting another ID does not move records between workspaces.
- For HTTPS behind a trusted reverse proxy, review and enable the commented proxy and secure-cookie settings.

Run `./setup-venv.sh`; it substitutes any provided database variables and applies migrations to the configured PostgreSQL database.

Development notes
- Python dependencies are defined by `requirements.txt`. Recreate a stale local `venv` before setup when it contains older or untracked dependency versions.

- Frontend: if you modify frontend code, rebuild with:
  ```bash
  npm ci
  npm run build
  ```
  The `setup-venv.sh` already runs the build unless `NODE_DONE=yes` is set in the environment.

- Do not commit secrets: `gp/local_settings.py` is produced from a selected template; keep secrets out of source control.

- Secret-key management:
  - `setup-venv.sh` creates `gp/local_settings.py` and `gp/secretkey.txt` with mode `0600`. Rerunning setup preserves both files while repairing their permissions.
  - To rotate the key, stop the application and run:
    ```bash
    (
      umask 077
      venv/bin/python -c 'import sys; from django.core.management.utils import get_random_secret_key; open(sys.argv[1], "x", encoding="utf-8").write(get_random_secret_key() + "\n")' gp/secretkey.txt.new
    )
    mv gp/secretkey.txt.new gp/secretkey.txt
    ```
    Restart the application afterward. Rotation invalidates existing sessions, password-reset links, and other values signed with the old key.

- Linting and checks:
  - `./check-code.sh` runs pycodestyle and pylint (uses `venv`).

Multigerm seeds and legacy transplant recovery

- Record the actual number of seeds or clusters sown. Do not inflate that quantity when one cluster produces multiple seedlings; record every observed seedling as its own specific plant.
- Seed-tray and seed-packet summaries show seeds or clusters sown separately from distinct germinated and transplanted plants.
- Migration `0015_audit_seed_allocation_capacity` is intentionally corrected in place because its old data-only germination audit could block a database before any later migration ran. Databases that already applied `0015` need no action; a database blocked by the old audit can rerun migrations with the corrected code.
- If migration `0016_audit_transplant_ownership` reports a mixed legacy aggregate and individual history, inspect one reported row without changing it:
  ```bash
  python manage.py convert_legacy_transplant 12
  ```
- Preview a mapping by supplying a source cell allocation, timezone-aware germination time, and each existing individual plant already included in the aggregate total:
  ```bash
  python manage.py convert_legacy_transplant 12 --cell-planting 34 --germinated-at 2026-01-15T09:00:00+13:00 --existing-plant 56 --existing-plant 57
  ```
  The aggregate quantity is treated as the total target, so the command previews only the missing plants. After checking the labels and counts, repeat the command with `--apply`. Conversion creates complete tray-to-garden history and deletes the aggregate in one transaction. Already-removed aggregates are rejected because they contain no trustworthy location end time.

Workspace and account boundaries
- Migration creates workspace `1` as `My Garden` with Garden mode, UTC, USD, 0% tax, and metric display. Correct these neutral defaults from the Settings screen before recording future financial transactions.
- `CURRENT_WORKSPACE_ID` selects the one workspace served by the deployment. Existing endpoint payloads do not expose or accept workspace IDs; the server scopes reads and binds writes to the configured workspace.
- Every authenticated account has the same access to that workspace. Memberships and roles are not implemented, so do not create accounts for mutually untrusted users.
- Additional workspace rows are supported as an isolation boundary for future development, but serving unrelated tenants, selecting workspaces per user, and moving records between workspaces remain unsupported.

API endpoints (examples)
- Workspace settings:
  - GET /settings/workspace/ — retrieve the current workspace profile without exposing its ID
  - PATCH /settings/workspace/ — update profile, financial defaults, timezone, and display measurements
- Plantings views:
  - GET /plantings/seedtray/current/ — list current seedtray plantings (with computed germination dates and transplanted counts)
  - POST /plantings/seedtray/ — create seedtray planting (used by frontend)
  - POST /plantings/seedtray/complete/ — mark seedtray planting removed
  - GET /plantings/garden/squares/current/ — list current garden-square plantings
  - POST /plantings/specificplants/{id}/move/ — atomically move an individual plant
  - GET /plantings/transplantedgardensquare/ — list read-only legacy aggregate transplants
  - POST /plantings/garden/squares/transplant/complete/ — complete a legacy aggregate transplant
- Seeds:
  - GET /seeds/seeds/ — list Seeds entries
  - GET /seeds/packets/ — list non-empty SeedPacket (current stock)
  - POST /seeds/packets/empty/ — mark a SeedPacket empty
- Locations:
  - GET /locations/ — list the physical places the workspace uses, filterable by `active` and `location_type`
  - POST /locations/ — name a new place; PATCH `{"active": false}` retires one that stock has passed through
- REST routers are registered in each app (see `*.rest.py` files) and wired into the Django URL config.

Project layout (high level)
- gp/ — Django project settings and WSGI/ASGI entry points
- frontend/ — JS React components and build configuration
- locations/ — the shared catalog of physical places, referenced by stock, trays, and plants
- garden/, plants/, seeds/, plantings/, supplies/ — Django apps with models, views, rest.py, urls
- setup-venv.sh, setup-db.sh, build-frontend.sh, start-wsgi.sh — helper scripts
- requirements.txt, package.json — dependency manifests

Contributing
- Fork, create a feature branch, add tests, and open a PR.
- Run backend tests with `./test-venv.sh` and linting with `./check-code.sh` before submitting.
- Consider adding a CONTRIBUTING.md and CODE_OF_CONDUCT.md if you want contribution guidelines formalised.

License
- Add a LICENSE file (e.g., MIT) if you want the project to be permissively licensed.

Contact
- Repository: https://github.com/sparlane/garden-planner
- For questions, open an issue in the repo.

Notes
- I scanned the repository code (models, rest endpoints and frontend components) to update the Features section; the code search results may be incomplete — view the full repo here: https://github.com/sparlane/garden-planner to confirm or request further refinements.
