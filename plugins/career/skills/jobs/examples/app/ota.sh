#!/bin/sh
set -eu
: "${CAREER_HOME:?Set CAREER_HOME to your owned workspace}"
exec "${CAREER_PYTHON:-$CAREER_HOME/.venv/bin/python}" "$CAREER_HOME/tools/build_app.py"
