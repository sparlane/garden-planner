#!/bin/bash -ex

source venv/bin/activate

pycodestyle --ignore=E501 */*.py

pylint frontend/ garden/ inventory/ plantings/ plants/ seeds/ seedtrays/ supplies/ workspaces/

deactivate
