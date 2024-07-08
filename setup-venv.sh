#!/bin/bash -ex

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

# Create the local settings file from the template
if [ ! -f gp/local_settings.py ]
then
	cp gp/local_settings.py.template gp/local_settings.py
	echo ""
	echo "Create gp/local_settings.py from template"
	echo "You should check this reflects your required settings"
        echo "At a minimum you will need to set your database parameters"
fi

./setup-db.sh

if [ ! -f gp/secretkey.txt ]
then
	python -c 'import random; result = "".join([random.choice("abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*(-_=+)") for i in range(50)]); print(result)' > gp/secretkey.txt
	echo ""
	echo "Created new secretkey.txt in gp/secretkey.txt"
fi

./manage.py collectstatic --no-input
