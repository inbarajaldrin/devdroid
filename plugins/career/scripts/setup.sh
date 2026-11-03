#!/bin/sh
set -eu
: "${CAREER_HOME:?Set CAREER_HOME to the owned workspace prepared by tools/setup.py}"
python3 -m venv "$CAREER_HOME/.venv"
"$CAREER_HOME/.venv/bin/python" -m pip install -r "$CAREER_HOME/requirements.txt"
npm ci --prefix "$CAREER_HOME/tools" --ignore-scripts --no-audit --no-fund
printf '%s\n' 'Dependencies installed. Read the resume, save the person’s rulings, then run tools/career.py check. Service templates remain inactive.'
