#!/bin/bash
# Orchestrated recording script for OpenClaw + Kimi K2.5 demo
# Records each test window individually (window-only, not full screen), then merges into one video
#
# Key fixes from v1:
# - Uses hyprctl activewindow (not PID) for geometry — works with chromium --app
# - Opens chromium app with --user-data-dir to get a fresh separate process
# - Uses Playwright (via node) to interact with Web UI chat so actions are visible
# - Adds transition text between clips

set -euo pipefail

CLIP_DIR="/tmp/openclaw-recording"
FINAL_OUTPUT="/home/mtjikuzu/dev/autonomous-recording/output/kimi-openclaw-demo.mp4"
GATEWAY_TOKEN="f705449cd4eceee2350d61a1a3b386cd86832703bb94f4db"
DASHBOARD_URL="http://127.0.0.1:18789/#token=$GATEWAY_TOKEN"

rm -rf "$CLIP_DIR"
mkdir -p "$CLIP_DIR"

source ~/.openclaw/.env
export OPENCLAW_GATEWAY_TOKEN="$GATEWAY_TOKEN"

# Helper: get geometry of the currently focused (active) window
get_active_geometry() {
    hyprctl activewindow -j | python3 -c "
import json, sys
w = json.load(sys.stdin)
x, y = w['at']
width, height = w['size']
print(f'{x},{y} {width}x{height}')
"
}

# Helper: focus window by class, then optionally maximize
focus_and_maximize() {
    local window_class="$1"
    hyprctl dispatch focuswindow "class:$window_class" >/dev/null 2>&1
    sleep 0.5
    # Check if already fullscreen
    local is_full
    is_full=$(hyprctl activewindow -j | python3 -c "
import json, sys
w = json.load(sys.stdin)
print(w.get('fullscreen', 0))
")
    if [ "$is_full" = "0" ]; then
        hyprctl dispatch fullscreen 1 >/dev/null 2>&1  # maximize (keeps top bar)
        sleep 0.5
    fi
}

# Helper: record active window region
start_recording() {
    local output_file="$1"
    local geom
    geom=$(get_active_geometry)
    echo "  Recording geometry: $geom"
    wf-recorder -g "$geom" -f "$output_file" -c libx264 -p preset=ultrafast -p crf=18 &
    echo $!
}

# Helper: stop recording
stop_recording() {
    local pid="$1"
    kill -INT "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null || true
    sleep 1
}

echo "=== OpenClaw + Kimi K2.5 Demo Recording ==="
echo ""

# ──────────────────────────────────────────────
# PART 1: CLI Test
# ──────────────────────────────────────────────
echo "[1/4] Opening CLI test terminal..."

# Create CLI test script
cat > /tmp/kimi-cli-demo.sh << 'DEMO_SCRIPT'
#!/bin/bash
export OPENCLAW_GATEWAY_TOKEN="f705449cd4eceee2350d61a1a3b386cd86832703bb94f4db"
source ~/.openclaw/.env

clear
echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │     OpenClaw + Kimi K2.5 — CLI Demo         │"
echo "  └─────────────────────────────────────────────┘"
echo ""
sleep 2

echo "  ▸ Checking gateway health..."
echo "  $ openclaw health"
echo ""
openclaw health
echo ""
sleep 2

echo "  ─────────────────────────────────────────────"
echo ""
echo "  ▸ Asking Kimi K2.5 to identify itself..."
echo '  $ openclaw agent -m "What model are you? Respond in 2-3 sentences."'
echo ""
openclaw agent -m "What model are you? Respond in 2-3 sentences about yourself." --agent main --timeout 60
echo ""
sleep 3

echo "  ─────────────────────────────────────────────"
echo ""
echo "  ▸ Asking Kimi K2.5 to write code..."
echo '  $ openclaw agent -m "Write hello world in 3 languages."'
echo ""
openclaw agent -m "Write a hello world function in Python, JavaScript, and Rust. Show code inline, explain each in one sentence." --agent main --timeout 60
echo ""
sleep 3

echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │     ✅ CLI Test Complete!                    │"
echo "  └─────────────────────────────────────────────┘"
sleep 3
DEMO_SCRIPT
chmod +x /tmp/kimi-cli-demo.sh

# Launch a NEW ghostty terminal for the CLI demo
ghostty --title="Kimi K2.5 CLI Demo" --class="kimi-cli-demo" -e /tmp/kimi-cli-demo.sh &
CLI_TERM_PID=$!
sleep 2

echo "[2/4] Recording CLI test window..."

# Focus and maximize the CLI demo terminal
# ghostty uses the class we set, but hyprctl may report it differently
# Use the title to find it
CLI_CLASS=$(hyprctl clients -j | python3 -c "
import json, sys
clients = json.load(sys.stdin)
for c in clients:
    if c['pid'] == $CLI_TERM_PID and c.get('mapped'):
        print(c['class'])
        break
")
echo "  CLI window class: $CLI_CLASS"

hyprctl dispatch focuswindow "pid:$CLI_TERM_PID" >/dev/null 2>&1
sleep 0.3
hyprctl dispatch fullscreen 1 >/dev/null 2>&1
sleep 0.5

# Start recording the active window (CLI terminal)
WF_PID=$(start_recording "$CLIP_DIR/cli-test.mp4")
echo "  wf-recorder PID: $WF_PID"

# Wait for CLI test script to finish
wait "$CLI_TERM_PID" 2>/dev/null || true

# Stop recording
stop_recording "$WF_PID"

echo "  CLI clip saved: $CLIP_DIR/cli-test.mp4"
echo ""

# ──────────────────────────────────────────────
# PART 2: Web UI Test
# ──────────────────────────────────────────────
echo "[3/4] Opening Web UI and recording..."

# Close any existing OpenClaw app windows
hyprctl dispatch closewindow "class:chrome-127.0.0.1__-Default" >/dev/null 2>&1 || true
sleep 1

# Open dashboard in a SEPARATE chromium profile so we get a distinct process
chromium \
    --user-data-dir="/tmp/openclaw-chromium-profile" \
    --app="$DASHBOARD_URL" \
    --no-first-run \
    --disable-default-apps \
    --window-size=1280,720 &
BROWSER_PID=$!
echo "  Browser PID: $BROWSER_PID"

# Wait for the window to appear
echo "  Waiting for browser window..."
for i in $(seq 1 20); do
    FOUND=$(hyprctl clients -j | python3 -c "
import json, sys
clients = json.load(sys.stdin)
for c in clients:
    if c['pid'] == $BROWSER_PID and c.get('mapped'):
        print('yes')
        break
" 2>/dev/null)
    if [ "$FOUND" = "yes" ]; then
        echo "  Browser window appeared after ${i}s"
        break
    fi
    sleep 1
done

# Focus and maximize the browser window
hyprctl dispatch focuswindow "pid:$BROWSER_PID" >/dev/null 2>&1
sleep 0.5
hyprctl dispatch fullscreen 1 >/dev/null 2>&1
sleep 1

# Give the dashboard a moment to fully load (JS, WebSocket connect)
echo "  Waiting for dashboard to load..."
sleep 5

# Start recording the active window (browser)
WF_PID=$(start_recording "$CLIP_DIR/webui-test.mp4")
echo "  wf-recorder PID: $WF_PID"

# Let recording capture the initial dashboard state for a couple seconds
sleep 3

# Now interact with the Web UI via openclaw agent
# The dashboard is connected via WebSocket and will show the response in real-time
echo "  Sending test message via gateway API..."
openclaw agent -m "Hello! I'm Kimi K2.5 running through OpenClaw. What model are you? List your top 3 capabilities briefly." --agent main --timeout 60 >/dev/null 2>&1 &
AGENT_PID=$!

# Wait for the agent to finish or timeout
if wait "$AGENT_PID" 2>/dev/null; then
    echo "  Agent response received"
else
    echo "  Agent timed out or failed"
fi

# Let the browser render and show the streamed response
sleep 8

# Stop recording
stop_recording "$WF_PID"

# Close the browser
kill "$BROWSER_PID" 2>/dev/null || true
sleep 1

echo "  Web UI clip saved: $CLIP_DIR/webui-test.mp4"
echo ""

# ──────────────────────────────────────────────
# PART 3: Merge clips
# ──────────────────────────────────────────────
echo "[4/4] Merging clips into final video..."

# Check clip stats
echo "  CLI clip:"
ffprobe -v quiet -select_streams v:0 -show_entries stream=width,height,nb_frames,duration -of csv=p=0 "$CLIP_DIR/cli-test.mp4" || true
echo "  WebUI clip:"
ffprobe -v quiet -select_streams v:0 -show_entries stream=width,height,nb_frames,duration -of csv=p=0 "$CLIP_DIR/webui-test.mp4" || true

# Normalize to 1280x720 and concatenate
ffmpeg -y \
  -i "$CLIP_DIR/cli-test.mp4" \
  -i "$CLIP_DIR/webui-test.mp4" \
  -filter_complex "\
    [0:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1[v0]; \
    [1:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1[v1]; \
    [v0][v1]concat=n=2:v=1:a=0[outv]" \
  -map "[outv]" \
  -c:v libx264 -preset medium -crf 20 \
  "$FINAL_OUTPUT" 2>&1 | tail -5

echo ""
echo "=== Done! ==="
echo "Final video: $FINAL_OUTPUT"
ls -lh "$FINAL_OUTPUT"
