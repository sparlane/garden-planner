#!/bin/bash -e

if grep -Eq 'django\.db\.backends\.postgresql' gp/local_settings.py
then
    [ -n "$DB_HOST" ] && sed -Ei "s|(['\"]HOST['\"]: ).*|\\1\"$DB_HOST\",|" gp/local_settings.py || true
    [ -n "$DB_USER" ] && sed -Ei "s|(['\"]USER['\"]: ).*|\\1\"$DB_USER\",|" gp/local_settings.py || true
    [ -n "$DB_NAME" ] && sed -Ei "s|(['\"]NAME['\"]: ).*|\\1\"$DB_NAME\",|" gp/local_settings.py || true
    [ -n "$DB_PASS" ] && sed -Ei "s|(['\"]PASSWORD['\"]: ).*|\\1\"$DB_PASS\",|" gp/local_settings.py || true
fi
