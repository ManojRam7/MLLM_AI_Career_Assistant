#!/usr/bin/env bash
# DIAGNOSTIC — run this in the Codespace terminal when the live viewer stays blank:
#
#     bash scripts/check_live.sh
#
# It checks every link in the chain and then opens a REAL browser on the desktop, so you get
# instant visual confirmation in the port-6080 viewer.
set -uo pipefail
export DISPLAY="${DISPLAY:-:1}"
ok(){ echo "  ✅ $*"; }
bad(){ echo "  ❌ $*"; }
echo "──────────── live-view check ────────────"

# 1 · desktop
if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
  ok "desktop is up on DISPLAY=$DISPLAY"
else
  bad "no desktop on $DISPLAY — starting one…"
  [ -x /usr/local/share/desktop-init.sh ] && /usr/local/share/desktop-init.sh >/tmp/desktop.log 2>&1 &
  sleep 4
  xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && ok "desktop started" || bad "could not start a desktop"
fi

# 2 · noVNC bridge
pgrep -f websockify >/dev/null && ok "noVNC/websockify running (port 6080)" \
                               || bad "websockify not running — the viewer can't connect"

# 3 · python + playwright
python -c "import playwright" 2>/dev/null && ok "playwright installed" || {
  bad "playwright missing — installing now…"; pip install -q playwright && playwright install --with-deps chromium; }

# 4 · secrets
if [ -n "${SUPABASE_DB_URL:-}" ]; then
  ok "SUPABASE_DB_URL is set"
elif [ -f .env ]; then
  ok ".env file present"
else
  bad "SUPABASE_DB_URL NOT set and no .env"
  echo "     → repo Settings → Secrets and variables → Codespaces → add SUPABASE_DB_URL,"
  echo "       then REBUILD this Codespace (Cmd/Ctrl+Shift+P → Codespaces: Rebuild Container)."
fi

# 5 · database + queue
python - <<'PY' 2>&1 | sed 's/^/  /'
import sys, pathlib
sys.path.insert(0, str(pathlib.Path("src").resolve()))
try:
    from uk_jobops.config import load_config
    from uk_jobops.db import Store
    cfg = load_config()
    if not cfg.secrets.supabase_db_url:
        print("❌ config loaded but SUPABASE_DB_URL is empty"); raise SystemExit
    s = Store(cfg.secrets.supabase_db_url); s.init_schema()
    q = s.apply_requests("queued", limit=50); s.close()
    print(f"✅ database reachable · {len(q)} job(s) queued")
    for r in q[:5]:
        print(f"     • {(r.get('title') or '(pasted URL)')[:48]} — {r.get('url','')[:60]}")
    if not q:
        print("   ⚠ queue is EMPTY — queue something in the app, then the watcher will pick it up")
except Exception as e:
    print(f"❌ database check failed: {str(e)[:200]}")
PY

# 6 · prove the viewer works — open a real browser on the desktop
echo ""
echo "▶ opening a visible browser on the desktop — LOOK AT THE VIEWER NOW…"
python - <<'PY'
import os
os.environ.setdefault("DISPLAY", ":1")
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False, args=["--no-sandbox", "--start-maximized"])
        pg = b.new_page(no_viewport=True)
        pg.goto("https://example.com", timeout=30000)
        pg.set_content("<h1 style='font:700 42px sans-serif;padding:60px'>✅ Live view works.<br>"
                       "<span style='font-size:22px;font-weight:400'>This browser is running in your "
                       "Codespace. Queue a job and press Run &amp; watch live.</span></h1>")
        import time; time.sleep(20)
        b.close()
    print("  ✅ browser opened and closed — if you saw it, the live view is fully working")
except Exception as e:
    print(f"  ❌ could not open a visible browser: {str(e)[:200]}")
PY
echo "─────────────────────────────────────────"
echo "If everything is ✅ start the watcher:   bash scripts/queue_watcher.sh"
