#!/bin/bash -ex

source venv/bin/activate

pycodestyle --ignore=E501 */*.py

# Every package with checked-in Python except gp/, which is excluded on purpose:
# it holds gp/local_settings.py, which is gitignored and differs on every
# machine, so linting it would gate the build on an untracked file. When a new
# app is added, add it here — applications/ was missed and went unlinted from
# the day it landed until the seed-tray generation work noticed.
pylint applications/ costing/ frontend/ garden/ health/ inventory/ labels/ locations/ plantings/ plants/ seeds/ seedtrays/ supplies/ tests/ work/ workspaces/

deactivate
