#!/bin/bash -ex

source venv/bin/activate

pycodestyle --ignore=E501 */*.py

pylint --max-line-length=240 --load-plugins pylint_django --django-settings-module=gp.settings --ignore migrations frontend/ garden/ plantings/ plants/ seeds/

deactivate
