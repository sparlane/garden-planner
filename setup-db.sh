#!/bin/bash -ex

[ ! -z "$DB_HOST" ] && sed -i "s|'HOST': .*|'HOST': '$DB_HOST',|" gp/local_settings.py || true
[ ! -z "$DB_USER" ] && sed -i "s|'USER': .*|'USER': '$DB_USER',|" gp/local_settings.py || true
[ ! -z "$DB_NAME" ] && sed -i "s|'NAME': .*|'NAME': '$DB_NAME',|" gp/local_settings.py || true
[ ! -z "$DB_PASS" ] && sed -i "s|'PASSWORD': .*|'PASSWORD': '$DB_PASS',|" gp/local_settings.py || true

