#!/bin/bash -e

# Run the backend test suite.
#
# It runs on PostgreSQL whenever a server is reachable, because that is what CI
# runs and because roughly two dozen concurrency tests skip on any backend
# without has_select_for_update. Start one with `docker compose up -d db`.
#
#   ./test-venv.sh                 # PostgreSQL if reachable, otherwise fall back
#   ./test-venv.sh --postgresql    # require PostgreSQL; fail if it is absent
#   ./test-venv.sh --sqlite        # use gp/local_settings.py as it stands
#
# DB_HOST, DB_PORT, DB_NAME, DB_USER, and DB_PASS select the server, and
# GP_TEST_DB presets the same choice as the flags. Remaining arguments are
# passed to `manage.py test`.

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-55432}"
DB_NAME="${DB_NAME:-garden_tracker}"
GP_TEST_DB="${GP_TEST_DB:-auto}"

args=()
for arg in "$@"
do
	case "$arg" in
		--postgresql) GP_TEST_DB=postgresql ;;
		--sqlite) GP_TEST_DB=sqlite ;;
		*) args+=("$arg") ;;
	esac
done

postgresql_reachable() {
	# timeout is GNU coreutils and is absent on a stock macOS, where a bare
	# connect is still the right test; without it the probe would report every
	# server unreachable and quietly send the run back to SQLite.
	if command -v timeout >/dev/null
	then
		timeout 2 bash -c "exec 3<>/dev/tcp/$DB_HOST/$DB_PORT" 2>/dev/null
	else
		bash -c "exec 3<>/dev/tcp/$DB_HOST/$DB_PORT" 2>/dev/null
	fi
}

case "$GP_TEST_DB" in
	auto)
		if postgresql_reachable
		then
			GP_TEST_DB=postgresql
		else
			echo "WARNING: no PostgreSQL server at $DB_HOST:$DB_PORT; using gp/local_settings.py." >&2
			echo "         Start one with 'docker compose up -d db' to run the concurrency tests." >&2
			GP_TEST_DB=sqlite
		fi
		;;
	postgresql)
		if ! postgresql_reachable
		then
			echo "ERROR: no PostgreSQL server at $DB_HOST:$DB_PORT." >&2
			echo "  Start one with: docker compose up -d db" >&2
			echo "  Or point DB_HOST and DB_PORT at an existing server." >&2
			exit 1
		fi
		;;
	sqlite) ;;
	*)
		echo "ERROR: GP_TEST_DB must be auto, postgresql, or sqlite." >&2
		exit 1
		;;
esac

if [ "$GP_TEST_DB" = "postgresql" ]
then
	echo "Running the suite on PostgreSQL ($DB_NAME@$DB_HOST:$DB_PORT)."
	export GP_SITE_SETTINGS="${GP_SITE_SETTINGS:-gp.ci_settings}"
	export DB_HOST DB_PORT DB_NAME
	# --parallel first so an explicit one in "$@" overrides it. --noinput
	# because an interrupted run leaves test_garden_tracker behind, and the
	# prompt offering to delete it fails with EOFError in any non-interactive
	# run, which reads as a configuration fault rather than stale state.
	exec venv/bin/python manage.py test --parallel auto --noinput "${args[@]}"
fi

exec venv/bin/python manage.py test --noinput "${args[@]}"
