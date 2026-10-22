#!/bin/bash
# Replays one fake restock with mock data, start to finish.
#   demo/run.sh --dry-run   print the alert email instead of sending it
#   demo/run.sh             send a real alert email marked [DEMO ...]
# Uses its own state file in demo/, so real monitoring is never affected.
set -e
cd "$(dirname "$0")/.."
PY=${PY:-python}
[ -x .venv/bin/python ] && PY=.venv/bin/python
run() { $PY -m restock --config demo/config.yaml --once "$@"; }

cp data/mock_stock.json demo/stock.json
rm -f demo/state.db
trap 'rm -f demo/stock.json demo/state.db' EXIT

echo "=== 1. First check: record a baseline (no email) ==="
run "$@"

echo; echo "=== 2. The Cross Ring comes back in stock ==="
$PY - <<'PYEOF'
import json
p = "demo/stock.json"
d = json.load(open(p))
d["products"][0]["status"] = "in_stock"
json.dump(d, open(p, "w"), indent=2)
print("cross-ring: out_of_stock -> in_stock")
PYEOF

echo; echo "=== 3. Next check: one alert ==="
run "$@"

echo; echo "=== 4. Check again: no repeat alert ==="
run "$@"
