#!/bin/bash

cd "$(dirname "$0")"

: "${UWSGI_SOCKET:=127.0.0.1:8080}"
: "${UWSGI_PROCESSES:=4}"

source venv/bin/activate

./manage.py migrate
exec uwsgi \
	--socket "$UWSGI_SOCKET" \
	-w gp.wsgi \
	-M \
	-p "$UWSGI_PROCESSES" \
	--vacuum \
	--die-on-term \
	--need-app
