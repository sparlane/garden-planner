# Garden Planner

Garden Planner is a Django-based web application to design and manage garden layouts, seed stock, and planting activity. The repository contains a Django backend (Python) and a JavaScript frontend (React components), plus helper scripts to bootstrap local development.

Table of contents
- Features
- Tech stack
- Prerequisites
- Quick start (recommended)
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
    - GardenSquareTransplant (transplants from seed trays into garden squares)
  - Planting attributes include dates, quantity, location, notes and a `removed` flag to mark completed/removed plantings.
  - Views that compute germination/maturity dates (using variety/plant metadata) and return JSON summaries for current plantings.

- REST API (Django REST Framework)
  - REST viewsets / routers for seeds, seed packets, plantings and varieties.
  - Plantings router exposes: `directsowgardenrow`, `directsowgardensquare`, `seedtray`, and `transplantedgardensquare`.
  - Seeds router exposes: `seeds`, `packets` and `packets/all`.

- React-based frontend components
  - Frontend lives in `frontend/js/` (React components) and uses Bootstrap and jQuery for the UI; examples include `menu.js`, `planting.js`, `seeds.js`, and `plants.js`.
  - Frontend build is wired up via the repository `package.json` and build scripts.

- Dev / helper scripts
  - `setup-venv.sh` creates a Python virtual environment, installs Python dependencies, builds the frontend (npm), creates `gp/local_settings.py` from `gp/local_settings.py.template`, runs `setup-db.sh`, generates a secret key file `gp/secretkey.txt` if needed, and runs `manage.py collectstatic`.
  - `setup-db.sh` and `start-wsgi.sh` are included to help bootstrap DB and serve app in deployments.
  - `check-code.sh` for style/lint checks (pycodestyle, pylint).

- Local-first defaults
  - The project includes a `gp/local_settings.py.template` used by the setup script to generate local settings for development; by default the app is set up to use local DB (SQLite) unless changed.

Tech stack
- Django (Python) backend, Django REST Framework for APIs
- React + Bootstrap (JavaScript) frontend components
- SQLite for local development; Postgres or other DBs can be configured in local settings
- Node/npm for frontend build; esbuild configuration present
- Shell scripts for environment setup and build automation

Prerequisites
- Git
- Python 3.8+
- Node.js (>=16) and npm (or yarn)
- Bash-compatible shell for `setup-venv.sh`

Quick start (recommended)
1. Clone the repo:
   ```bash
   git clone https://github.com/sparlane/garden-planner.git
   cd garden-planner
   ```

2. Run the setup script (creates venv, installs deps, builds frontend, creates local settings, initialises DB, collects static):
   ```bash
   chmod +x setup-venv.sh
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

4. Create a Django superuser and run migrations if not covered by setup:
   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   ```

5. Run the development server:
   ```bash
   python manage.py runserver
   ```
   Open http://127.0.0.1:8000

Development notes
- Frontend: if you modify frontend code, rebuild with:
  ```bash
  npm ci
  npm run build
  ```
  The `setup-venv.sh` already runs the build unless `NODE_DONE=yes` is set in the environment.

- Do not commit secrets: `gp/local_settings.py` is produced by the template; keep secrets out of source control.

- Linting and checks:
  - `./check-code.sh` runs pycodestyle and pylint (uses `venv`).

API endpoints (examples)
- Plantings views:
  - GET /plantings/seedtray/current/ — list current seedtray plantings (with computed germination dates and transplanted counts)
  - POST /plantings/seedtray/ — create seedtray planting (used by frontend)
  - POST /plantings/seedtray/complete/ — mark seedtray planting removed
  - GET /plantings/garden/squares/current/ — list current garden-square plantings
  - POST /plantings/garden/squares/transplant/complete/ — complete transplant operations
- Seeds:
  - GET /seeds/seeds/ — list Seeds entries
  - GET /seeds/packets/ — list non-empty SeedPacket (current stock)
  - POST /seeds/packets/empty/ — mark a SeedPacket empty
- REST routers are registered in each app (see `*.rest.py` files) and wired into the Django URL config.

Project layout (high level)
- gp/ — Django project settings and WSGI/ASGI entry points
- frontend/ — JS React components and build configuration
- garden/, plants/, seeds/, plantings/, supplies/ — Django apps with models, views, rest.py, urls
- setup-venv.sh, setup-db.sh, build-frontend.sh, start-wsgi.sh — helper scripts
- requirements.txt, package.json — dependency manifests

Contributing
- Fork, create a feature branch, add tests, and open a PR.
- Please run linting and tests locally before submitting.
- Consider adding a CONTRIBUTING.md and CODE_OF_CONDUCT.md if you want contribution guidelines formalised.

License
- Add a LICENSE file (e.g., MIT) if you want the project to be permissively licensed.

Contact
- Repository: https://github.com/sparlane/garden-planner
- For questions, open an issue in the repo.

Notes
- I scanned the repository code (models, rest endpoints and frontend components) to update the Features section; the code search results may be incomplete — view the full repo here: https://github.com/sparlane/garden-planner to confirm or request further refinements.
