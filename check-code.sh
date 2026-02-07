#!/bin/bash -ex

source venv/bin/activate

pycodestyle --ignore=E501 */*.py

pylint frontend/ garden/ plantings/ plants/ seeds/

deactivate
