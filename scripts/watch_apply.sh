#!/usr/bin/env bash
# WATCH THE AUTO-APPLY LIVE — in the cloud, from your phone or Mac.
#
# Runs inside a GitHub Codespace (free tier) with a real desktop, so Playwright opens a VISIBLE
# Chrome you can watch in your browser while it fills each application — exactly like running it
# locally, except nothing is on your machine.
#
#   1) GitHub repo → Code ▾ → Codespaces → Create codespace on main   (wait for setup)
#   2) In the Codespace terminal:   ./scripts/watch_apply.sh
#   3) Open the forwarded port 6080 ("Watch the browser") from the PORTS tab — password: vscode
#      On your phone: same Codespace URL works in mobile Safari/Chrome.
#
# Anything after the script name is passed straight to auto_apply.py, e.g.:
#   ./scripts/watch_apply.sh --from-queue
#   ./scripts/watch_apply.sh --url "https://jobs.lever.co/acme/123" --title "Data Analyst"
set -euo pipefail

export DISPLAY="${DISPLAY:-:1}"

# The desktop-lite feature provides Xvfb + a VNC/noVNC stack; start it if it isn't already up.
if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
  echo "▶ starting virtual desktop on $DISPLAY ..."
  if [ -x /usr/local/share/desktop-init.sh ]; then
    /usr/local/share/desktop-init.sh >/tmp/desktop.log 2>&1 || true
  else
    Xvfb "$DISPLAY" -screen 0 1440x900x24 >/tmp/xvfb.log 2>&1 &
  fi
  sleep 4
fi

echo "────────────────────────────────────────────────────────────────"
echo " Watch it live:  PORTS tab → port 6080 → open in browser"
echo " Password: vscode"
echo " Live decision log also streams to the app → Auto-Apply → Live run"
echo "────────────────────────────────────────────────────────────────"

# NOTE: no --headless, so the browser is visible on the virtual desktop.
# Not passing --ci either, so you keep the review pauses if you want them.
exec python scripts/auto_apply.py "$@"
