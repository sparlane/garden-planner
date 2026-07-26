#!/bin/bash -e

python3 -m venv venv

source venv/bin/activate

pip install -r requirements.txt

if [ "x${NODE_DONE}" != "xyes" ]
then
    mkdir -p frontend/static/
    npm ci
    npm run build
fi

echo ""

# Create local development settings unless the user selected another template.
if [ ! -f gp/local_settings.py ]
then
	(
		umask 077
		cp gp/local_settings.dev.py.template gp/local_settings.py
	)
	echo ""
	echo "Created gp/local_settings.py with the SQLite development settings"
fi
chmod 600 gp/local_settings.py

./setup-db.sh

if [ ! -f gp/secretkey.txt ]
then
	(
		umask 077
		python -c 'import sys; from django.core.management.utils import get_random_secret_key; open(sys.argv[1], "x", encoding="utf-8").write(get_random_secret_key() + "\n")' gp/secretkey.txt
	)
	echo ""
	echo "Created new secretkey.txt in gp/secretkey.txt"
fi
chmod 600 gp/secretkey.txt

./manage.py migrate
./manage.py collectstatic --no-input
