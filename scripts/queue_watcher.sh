#!/usr/bin/env bash
# QUEUE WATCHER — runs inside the Codespace, started automatically when you open it.
#
# Polls the apply queue. The moment you press "Run & watch live" (or just queue jobs) in the app,
# it applies to them in a VISIBLE Chrome on this Codespace's desktop, so you can watch it in the
# viewer (port 6080) from your Mac or phone — and click in to take over at any point.
#
# Manual use is fine too:   ./scripts/queue_watcher.sh
# Stop it with:             pkill -f queue_watcher
set -uo pipefail

export DISPLAY="${DISPLAY:-:1}"
INTERVAL="${WATCH_INTERVAL:-15}"

# Make sure the virtual desktop is up (desktop-lite normally starts it for us).
if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
  echo "▶ starting virtual desktop on $DISPLAY …"
  [ -x /usr/local/share/desktop-init.sh ] && /usr/local/share/desktop-init.sh >/tmp/desktop.log 2>&1 || \
    Xvfb "$DISPLAY" -screen 0 1440x900x24 >/tmp/xvfb.log 2>&1 &
  sleep 4
fi

echo "👁  Queue watcher running (every ${INTERVAL}s). Watch on port 6080."
echo "    Anything you queue in the app gets applied here, visibly."

while true; do
  # Does the queue have anything waiting? (exit 0 = yes)
  if python - <<'PY' 2>/dev/null
import sys, pathlib
sys.path.insert(0, str(pathlib.Path("src").resolve()))
from uk_jobops.config import load_config
from uk_jobops.db import Store
cfg = load_config()
s = Store(cfg.secrets.supabase_db_url); s.init_schema()
n = len(s.apply_requests("queued", limit=50)); s.close()
sys.exit(0 if n else 1)
PY
  then
    echo ""
    echo "▶ $(date '+%H:%M:%S') — jobs queued, applying now (watch port 6080)…"
    python scripts/auto_apply.py --from-queue || echo "! run ended with an error; continuing to watch"
    echo "✓ $(date '+%H:%M:%S') — done, back to watching."
  fi
  sleep "$INTERVAL"
done
