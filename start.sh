#!/bin/bash
# One-command start on macOS/Linux:  ./start.sh
# Sets up Python packages the first time, asks for your email and app password,
# then checks the store every 20 minutes. Keep this Terminal window open.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "First run: installing packages..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
[ -f config.yaml ] || cp config.example.yaml config.yaml

if [ -z "$EMAIL_ADDRESS" ]; then
  read -r -p "Your email address (Gmail or 163): " EMAIL_ADDRESS
  export EMAIL_ADDRESS
fi
if [ -z "$SMTP_PASSWORD" ]; then
  read -r -s -p "Gmail app password / 163 authorization code: " SMTP_PASSWORD; echo
  export SMTP_PASSWORD
fi

# caffeinate (macOS) stops the computer from sleeping while the watcher runs.
if command -v caffeinate >/dev/null; then
  exec caffeinate -i .venv/bin/python -m restock --interval 20 "$@"
else
  exec .venv/bin/python -m restock --interval 20 "$@"
fi
