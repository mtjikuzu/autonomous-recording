# Wayland Demo Recording Framework

A reusable system for producing clean, multi-clip demo videos on Wayland (Hyprland). Records only the target window, starts and stops at precise moments, and merges clips into a single video with no dead time.

## Table of Contents

- [Core Principles](#core-principles)
- [Toolchain](#toolchain)
- [Architecture](#architecture)
- [Clip Lifecycle](#clip-lifecycle)
- [Start/Stop Patterns](#startstop-patterns)
- [Window Capture](#window-capture)
- [Browser Interaction via CDP](#browser-interaction-via-cdp)
- [Merge Pipeline](#merge-pipeline)
- [Orchestration Template](#orchestration-template)
- [Gotchas and Lessons Learned](#gotchas-and-lessons-learned)
- [Narrated Recordings](#narrated-recordings)

---

## Core Principles

1. **Never record continuously.** Record each logical segment as an independent clip. Dead time between segments (launching apps, waiting for windows, switching focus) is never captured.

2. **Record only the target window.** Use `wf-recorder -g "x,y WxH"` with geometry from `hyprctl activewindow`. Never record full screen — irrelevant windows, desktop, and taskbars leak in.

3. **Start recording after the window is ready.** The target window must be focused, maximized, and fully loaded before the recorder starts. For browsers, this means JS/WebSocket connections are established.

4. **Stop recording before the window dies.** When a terminal script finishes, the terminal closes and exposes whatever is behind it. Stop the recorder while the final content is still visible. Use signal files or process monitoring to detect "content done" vs "window closed."

5. **Automate all interaction.** Manual mouse/keyboard input is unreliable on Wayland. Use signal files for terminal coordination and Chrome DevTools Protocol (CDP) for browser interaction.

---

## Toolchain

| Tool | Purpose | Install |
|------|---------|---------|
| `wf-recorder` | Wayland-native screen/region recorder | `pacman -S wf-recorder` |
| `hyprctl` | Window management, focus, geometry queries | Ships with Hyprland |
| `ffmpeg` / `ffprobe` | Video merge, normalize, inspect | `pacman -S ffmpeg` |
| `mpv` | Video playback/verification | `pacman -S mpv` |
| `python3` + `websockets` | CDP browser automation | `pip install websockets` |
| `chromium` | Browser with CDP support | `pacman -S chromium` |
| `ghostty` | Terminal emulator | Already installed |
| `grim` | Screenshot verification | `pacman -S grim` |

---

## Architecture

```
 Clip 1: Terminal Demo          Clip 2: Browser Demo         Final Video
┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────┐
│ 1. Launch terminal   │   │ 1. Launch browser     │   │                  │
│ 2. Focus + maximize  │   │ 2. Focus + maximize   │   │  Clip 1 (CLI)    │
│ 3. START recording   │   │ 3. Wait for page load │   │    seamless      │
│ 4. Run demo script   │   │ 4. START recording    │   │  Clip 2 (Web)    │
│ 5. Detect completion │   │ 5. Interact via CDP   │   │                  │
│ 6. STOP recording    │   │ 6. Detect completion  │   │  (1280x720@30fps)│
│ 7. Kill terminal     │   │ 7. STOP recording     │   │                  │
└──────────────────────┘   │ 8. Kill browser       │   └──────────────────┘
                           └──────────────────────┘
                                                        ffmpeg concat
```

No recording happens during transitions between clips.

---

## Clip Lifecycle

Every clip follows the same five phases:

```
SETUP ──▶ READY ──▶ RECORD ──▶ SIGNAL ──▶ TEARDOWN
```

| Phase | What happens | Recorder state |
|-------|-------------|----------------|
| **SETUP** | Launch app, focus window, maximize, wait for load | OFF |
| **READY** | Window is visible, content loaded, interaction possible | OFF |
| **RECORD** | Start `wf-recorder` on window geometry | ON |
| **SIGNAL** | Content is complete, final frame visible | ON → OFF |
| **TEARDOWN** | Stop recorder, kill app, clean up | OFF |

The recorder is ON only during the RECORD and SIGNAL phases. Everything else happens off-camera.

---

## Start/Stop Patterns

### Pattern 1: Signal File (for terminal/script clips)

The demo script writes a signal file when its content is done, then sleeps. The orchestrator watches for the signal, holds the frame, then stops the recorder before killing the terminal.

**Demo script (runs inside terminal):**
```bash
#!/bin/bash
# ... do the demo ...

echo "Demo Complete!"

# Signal that content is done (but DON'T exit yet)
touch /tmp/recording/clip-done

# Sleep so the terminal stays open while recorder captures the final frame
sleep 30
```

**Orchestrator:**
```bash
# Launch terminal
ghostty --title="Demo" -e /tmp/demo-script.sh &
TERM_PID=$!
sleep 2

# Focus and maximize
hyprctl dispatch focuswindow "pid:$TERM_PID" >/dev/null 2>&1
sleep 0.3
hyprctl dispatch fullscreen 1 >/dev/null 2>&1
sleep 0.5

# Get geometry and START recording
GEOM=$(hyprctl activewindow -j | python3 -c "
import json, sys
w = json.load(sys.stdin)
x, y = w['at']
w2, h = w['size']
print(f'{x},{y} {w2}x{h}')
")
wf-recorder -g "$GEOM" -f clip.mp4 -c libx264 -p preset=ultrafast -p crf=18 -D &
WF_PID=$!
sleep 1

# Wait for signal (content done, terminal still open)
while [ ! -f /tmp/recording/clip-done ]; do sleep 1; done

# Hold final frame for 3 seconds
sleep 3

# STOP recording (terminal is still showing the final content)
kill -INT "$WF_PID"
wait "$WF_PID" 2>/dev/null || true

# NOW kill the terminal (off-camera)
kill "$TERM_PID" 2>/dev/null || true
```

**Why this works:** The recorder stops while the "Demo Complete!" banner is still on screen. The terminal closure (which would expose the desktop) happens after the recorder is already off.

### Pattern 2: CDP Polling (for browser clips)

For browser-based demos, use Chrome DevTools Protocol to both drive interaction and detect completion. The orchestrator monitors DOM state (button text, element presence) to know when the response has finished rendering.

**Orchestrator (Python):**
```python
# Detect response completion by polling button state
for i in range(60):
    await asyncio.sleep(1)
    status = await cdp_eval(ws, """
        (() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const stop = btns.find(b => b.textContent.includes('Stop'));
            const send = btns.find(b => b.textContent.includes('Send'));
            if (stop) return 'streaming';
            if (send) return 'done';
            return 'unknown';
        })()
    """)
    if status == 'done' and i > 3:
        break

# Hold final frame
await asyncio.sleep(5)

# STOP recording
recorder.send_signal(signal.SIGINT)
```

**Adapt the polling condition to your app:**
- Chat app: poll for "Send" button reappearing (means streaming finished)
- Loading page: poll for spinner disappearing
- Form submission: poll for success message appearing
- Any app: poll for a specific CSS class, element count, or text content

### Pattern 3: Timed (simple cases)

When the content is predictable and doesn't depend on async operations:

```bash
# START recording
wf-recorder -g "$GEOM" -f clip.mp4 ... &
WF_PID=$!

# Wait fixed duration
sleep 15

# STOP recording
kill -INT "$WF_PID"
```

Use this only when you know the exact duration. Prefer signal files or polling for anything async.

---

## Window Capture

### Get geometry of the focused window

```bash
GEOM=$(hyprctl activewindow -j | python3 -c "
import json, sys
w = json.load(sys.stdin)
x, y = w['at']
width, height = w['size']
print(f'{x},{y} {width}x{height}')
")
```

### Focus and maximize a window by PID

```bash
hyprctl dispatch focuswindow "pid:$PID" >/dev/null 2>&1
sleep 0.3
hyprctl dispatch fullscreen 1 >/dev/null 2>&1
sleep 0.5
```

### Focus by window class (when PID is shared)

```bash
hyprctl dispatch focuswindow "class:chromium" >/dev/null 2>&1
```

### Record only that window region

```bash
wf-recorder -g "$GEOM" -f output.mp4 -c libx264 -p preset=ultrafast -p crf=18 -D &
```

The `-D` flag disables damage-based capture. Without it, `wf-recorder` only captures frames when the compositor reports pixel changes — resulting in very few frames for mostly-static content. Always use `-D` for demo recordings.

---

## Browser Interaction via CDP

When you need to interact with a web page during recording, keyboard simulators (`wtype`, `ydotool`) are unreliable on Wayland — they send events to the window manager, not to the browser's DOM. CDP gives you direct DOM access.

### Launch browser with CDP

```bash
chromium --remote-debugging-port=9222 "http://your-app-url" &
BROWSER_PID=$!
```

### Get the page WebSocket URL

```bash
PAGE_WS=$(curl -s http://localhost:9222/json | python3 -c "
import json, sys
pages = json.load(sys.stdin)
for p in pages:
    if 'your-app' in p.get('url', ''):
        print(p['webSocketDebuggerUrl'])
        break
")
```

### CDP eval helper (Python)

```python
import json, asyncio, websockets

async def cdp_eval(ws, expr, msg_id=1):
    msg = json.dumps({
        "id": msg_id,
        "method": "Runtime.evaluate",
        "params": {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": True
        }
    })
    await ws.send(msg)
    resp = json.loads(await ws.recv())
    return resp.get("result", {}).get("result", {}).get("value", None)
```

### Common CDP operations

**Set a textarea value (React/framework-compatible):**
```python
await cdp_eval(ws, f"""
    (() => {{
        const ta = document.querySelector('textarea');
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLTextAreaElement.prototype, 'value'
        ).set;
        setter.call(ta, '{message}');
        ta.dispatchEvent(new Event('input', {{ bubbles: true }}));
        return 'typed';
    }})()
""")
```

Using the native property setter + dispatching an `input` event is necessary because modern frameworks (React, Vue, Svelte) intercept the `value` property. Setting `ta.value = "..."` directly won't trigger their state updates.

**Submit by pressing Enter:**
```python
await cdp_eval(ws, """
    (() => {
        const ta = document.querySelector('textarea');
        ta.dispatchEvent(new KeyboardEvent('keydown', {
            key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true
        }));
        return 'sent';
    })()
""")
```

**Click a button:**
```python
await cdp_eval(ws, """
    (() => {
        const btn = Array.from(document.querySelectorAll('button'))
            .find(b => b.textContent.includes('Send'));
        if (btn) { btn.click(); return 'clicked'; }
        return 'not found';
    })()
""")
```

**Auto-scroll a container:**
```python
await cdp_eval(ws, """
    (() => {
        const log = document.querySelector('[role=log]');
        if (log) log.scrollTop = log.scrollHeight;
        return 'scrolled';
    })()
""")
```

---

## Merge Pipeline

### Normalize and concatenate clips

All clips must be normalized to the same resolution and framerate before concatenation:

```bash
ffmpeg -y \
  -i clip1.mp4 \
  -i clip2.mp4 \
  -filter_complex "\
    [0:v]scale=1280:720:force_original_aspect_ratio=decrease,\
         pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v0]; \
    [1:v]scale=1280:720:force_original_aspect_ratio=decrease,\
         pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v1]; \
    [v0][v1]concat=n=2:v=1:a=0[outv]" \
  -map "[outv]" \
  -c:v libx264 -preset medium -crf 20 \
  final.mp4
```

For 3+ clips, extend the pattern:
```bash
# Add [2:v]scale...fps=30[v2]; to filter_complex
# Change concat=n=3:v=1:a=0
# Add -i clip3.mp4
```

### Verify before merging

```bash
# Check resolution and frame count of each clip
ffprobe -v quiet -select_streams v:0 \
  -show_entries stream=width,height,duration,nb_frames \
  -of csv=p=0 clip.mp4
```

### Spot-check the final video

```bash
# Extract frames at key timestamps
ffmpeg -y -ss 10 -i final.mp4 -frames:v 1 check-10s.png
ffmpeg -y -ss 60 -i final.mp4 -frames:v 1 check-60s.png

# Or just play it
mpv final.mp4
```

---

## Orchestration Template

Copy and adapt this for any new recording session:

```bash
#!/bin/bash
set -euo pipefail

CLIP_DIR="/tmp/recording-clips"
FINAL_OUTPUT="$HOME/demo-video.mp4"

rm -rf "$CLIP_DIR"
mkdir -p "$CLIP_DIR"

get_active_geometry() {
    hyprctl activewindow -j | python3 -c "
import json, sys
w = json.load(sys.stdin)
x, y = w['at']
width, height = w['size']
print(f'{x},{y} {width}x{height}')
"
}

# ─── CLIP 1: Terminal Demo ───────────────────
# SETUP (off-camera)
ghostty --title="My Demo" -e /tmp/my-demo-script.sh &
TERM_PID=$!
sleep 2
hyprctl dispatch focuswindow "pid:$TERM_PID" >/dev/null 2>&1
sleep 0.3
hyprctl dispatch fullscreen 1 >/dev/null 2>&1
sleep 0.5

# RECORD
GEOM=$(get_active_geometry)
wf-recorder -g "$GEOM" -f "$CLIP_DIR/clip1.mp4" \
  -c libx264 -p preset=ultrafast -p crf=18 -D &
WF_PID=$!
sleep 1

# SIGNAL (wait for content completion)
while [ ! -f "$CLIP_DIR/clip1-done" ]; do sleep 1; done
sleep 3  # hold final frame

# TEARDOWN (off-camera)
kill -INT "$WF_PID"; wait "$WF_PID" 2>/dev/null || true
kill "$TERM_PID" 2>/dev/null || true
sleep 1

# ─── CLIP 2: Browser Demo ───────────────────
# SETUP (off-camera)
chromium --remote-debugging-port=9222 "http://localhost:3000" &
BROWSER_PID=$!
sleep 3
hyprctl dispatch focuswindow "pid:$BROWSER_PID" >/dev/null 2>&1
sleep 0.3
hyprctl dispatch fullscreen 1 >/dev/null 2>&1
sleep 1
sleep 5  # wait for page JS to load

# RECORD + INTERACT via CDP
python3 /tmp/record-browser-clip.py \
  "$(curl -s http://localhost:9222/json | python3 -c "
import json,sys
for p in json.load(sys.stdin):
    if 'localhost' in p.get('url',''):
        print(p['webSocketDebuggerUrl']); break
")" \
  "$CLIP_DIR/clip2.mp4" \
  "$(get_active_geometry)"

# TEARDOWN (off-camera)
kill "$BROWSER_PID" 2>/dev/null || true
sleep 1

# ─── MERGE ───────────────────────────────────
ffmpeg -y \
  -i "$CLIP_DIR/clip1.mp4" \
  -i "$CLIP_DIR/clip2.mp4" \
  -filter_complex "\
    [0:v]scale=1280:720:force_original_aspect_ratio=decrease,\
         pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v0]; \
    [1:v]scale=1280:720:force_original_aspect_ratio=decrease,\
         pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v1]; \
    [v0][v1]concat=n=2:v=1:a=0[outv]" \
  -map "[outv]" \
  -c:v libx264 -preset medium -crf 20 \
  "$FINAL_OUTPUT"

echo "Done: $FINAL_OUTPUT"
ls -lh "$FINAL_OUTPUT"

# Clean up
rm -rf "$CLIP_DIR"
```

---

## Gotchas and Lessons Learned

**OBS does not work reliably on Wayland.** PipeWire screen capture requires portal authorization that silently fails. Use `wf-recorder` instead — it uses `wlr-screencopy` directly.

**`wf-recorder` without `-D` produces very few frames.** Damage-based capture only records when pixels change. Static content (text that's already rendered) generates almost no frames, resulting in tiny files and choppy playback. Always pass `-D` for demo recordings.

**`chromium --app=URL` shares the parent process PID.** You cannot use PID-based window lookup (`hyprctl clients -j` filtering by PID) because the app window has the same PID as the main browser. Either use `--user-data-dir` for a separate process, or use class-based lookup (`chrome-127.0.0.1__-Default`).

**Wayland keyboard simulators don't reliably reach browser inputs.** `wtype` and `ydotool` send events at the compositor level. Chromium may not route them to the focused DOM element. CDP `Runtime.evaluate` is the only reliable way to interact with browser content during recording.

**React/framework textareas ignore direct value assignment.** Setting `textarea.value = "text"` bypasses framework state. Use the native `HTMLTextAreaElement.prototype.value` setter and dispatch an `input` event to trigger framework reactivity.

**Terminal window closure leaks the desktop.** When a script finishes, the terminal emulator closes its window. If the recorder is still capturing, it records one or more frames of whatever is behind the terminal (typically your editor or desktop). The signal file pattern prevents this by decoupling "content is done" from "window is closed."

**Always verify clips before merging.** Extract frames with `ffmpeg -ss N -i clip.mp4 -frames:v 1 check.png` at the start, middle, and end of each clip. Check for: black frames, wrong window captured, desktop leakage, missing content.

**Gateway tokens change after `openclaw onboard`.** If you re-run onboard, the gateway token in `.env` gets regenerated. Update any hardcoded tokens in scripts and browser bookmarks.


---

## Narrated Recordings

Add synthesized voice narration to recordings using Kokoro TTS. Narration audio is played through PipeWire during `wf-recorder` capture, so the recorder picks up both screen content and voice simultaneously.

### Toolchain Additions

| Tool | Purpose | Install |
|------|---------|---------|
| `kokoro-onnx` | Local TTS synthesis (ONNX runtime) | `pip install kokoro-onnx` |
| `soundfile` | WAV file read/write | `pip install soundfile` |
| `pw-play` | Play audio through PipeWire | Ships with PipeWire |
| Kokoro model | `~/.openclaw/models/kokoro-v1.0.onnx` | Download from Kokoro releases |
| Kokoro voices | `~/.openclaw/models/voices-v1.0.bin` | Download from Kokoro releases |

### How It Works

```
1. Pre-synthesize all narration segments with Kokoro TTS
2. Launch target app (terminal/browser)
3. Start wf-recorder with audio capture (-a=<monitor>)
4. Play WAV segments via pw-play at scripted moments
5. wf-recorder captures screen + TTS audio in one pass
6. Stop recorder, merge clips as usual
```

The key insight: `wf-recorder -a=<monitor>` captures the PipeWire monitor source, which includes all system audio. When `pw-play` outputs TTS audio, it goes through the default sink, and the monitor picks it up alongside any other system sounds.

### Audio Monitor Setup

Find your PipeWire audio monitor:

```bash
pactl list short sinks
# Output: alsa_output.pci-0000_05_00.6.analog-stereo  PipeWire  s32le 2ch 48000Hz

# The monitor source is the sink name + ".monitor"
# e.g. alsa_output.pci-0000_05_00.6.analog-stereo.monitor
```

### Config File Format

The narrated recorder (`narrated-record.py`) takes a JSON config:

```json
{
  "output": "~/demo-narrated.mp4",
  "clips": [
    {
      "name": "cli-demo",
      "type": "terminal",
      "title": "My Demo",
      "script": "/tmp/demo-script.sh",
      "segments": [
        {
          "narration": "Welcome to the demo.",
          "trigger": "immediate",
          "pause_after": 1.0
        },
        {
          "narration": "Now watch as we run the command.",
          "trigger": "signal",
          "signal_name": "cli-demo-step2",
          "pause_after": 1.5
        },
        {
          "narration": "The command completed successfully.",
          "trigger": "delay",
          "delay": 3,
          "pause_after": 2.0
        }
      ]
    },
    {
      "name": "web-demo",
      "type": "browser",
      "url": "http://localhost:3000",
      "cdp_port": 9222,
      "segments": [
        {
          "narration": "Here we see the web interface.",
          "trigger": "immediate",
          "action": "type_and_send",
          "message": "Hello world",
          "play_before_action": true,
          "pause_after": 1.0
        }
      ]
    }
  ]
}
```

### Segment Triggers

| Trigger | Behavior |
|---------|----------|
| `immediate` | Play narration as soon as recording starts |
| `signal` | Wait for a signal file from the demo script, then play |
| `delay` | Wait N seconds, then play |

For `signal` triggers, the demo script creates the signal file:

```bash
touch "$SIGNAL_DIR/cli-demo-step2"
```

### Running

```bash
python3 narrated-record.py --config demo.json
python3 narrated-record.py --config demo.json --voice af_heart --speed 0.9
python3 narrated-record.py --config demo.json --skip-synth  # reuse existing WAVs
```

Options:
- `--voice` --- Kokoro voice ID (default: `am_michael`). Prefix `am_`/`af_` for American male/female.
- `--speed` --- Speech rate multiplier (default: 1.0)
- `--lang` --- Language code (default: `en-us`)
- `--output-dir` --- Working directory for WAVs and clips (default: `/tmp/narrated-recording`)
- `--skip-synth` --- Skip TTS synthesis, reuse existing WAV files

### Browser Clip Narration

Browser clips combine narration with CDP interaction. Each segment can optionally include an `action`:

- `type_and_send` --- Type a message into a textarea and press Enter
- `wait_for_response` --- Poll DOM until streaming response completes
- `play_before_action: true/false` --- Whether narration plays before or during the action

### Gotchas

**Mute other audio sources during recording.** The monitor captures ALL system audio. Browser notification sounds, chat pings, and system alerts will bleed into the narration track.

**TTS synthesis is slow but one-time.** Kokoro takes ~1s per second of audio on CPU. Use `--skip-synth` to reuse WAVs when iterating on timing without changing narration text.

**PipeWire monitor name varies by hardware.** Run `pactl list short sinks` to find yours. The monitor source is always `<sink-name>.monitor`.

**Verify audio is captured.** After recording, check with:
```bash
ffprobe -v quiet -select_streams a:0 -show_entries stream=codec_name,duration -of csv=p=0 output.mp4
# Should show: aac,28.608000 (or similar)
```

---

## Autonomous Narrated Recordings

For recordings where you want an AI agent to drive the browser and generate narration on-the-fly, use `auto-narrated-record.py`. This replaces the static JSON config approach with fully autonomous navigation.

### How It Works

```
1. Orchestrator sets up: idle inhibition, fullscreen, wf-recorder
2. Orchestrator starts TTS file watcher (monitors /tmp/openclaw/tts-*/)
3. Orchestrator sends prompt to OpenClaw agent via CLI
4. OpenClaw autonomously navigates browser + calls TTS tool
5. TTS watcher detects new MP3 files, waits for write completion,
   copies to stable location, plays via pw-play
6. wf-recorder captures screen + TTS audio via PipeWire monitor
7. Agent signals completion or times out
8. Orchestrator stops recorder, restores idle
```

### Architecture

```
 Orchestrator (auto-narrated-record.py)
 ├─ inhibit_idle()        kill hypridle/hyprlock + watchdog thread
 ├─ ensure_fullscreen()   focus browser, toggle fullscreen if needed
 ├─ start_recorder()      wf-recorder -g geometry -a=<monitor>
 ├─ start_tts_watcher()   background thread polling /tmp/openclaw/tts-*/
 ├─ run_openclaw_agent()  openclaw agent --agent main -m "..." --json
 ├─ stop_tts_watcher()    signal thread to stop
 ├─ stop_recorder()       SIGINT to wf-recorder
 └─ restore_idle()        restart hypridle
```

### TTS File Watcher

OpenClaw's `tts` tool writes MP3 files to `/tmp/openclaw/tts-<random>/voice-<timestamp>.mp3`. The watcher:

1. Snapshots existing files before recording (ignores pre-existing)
2. Polls for new MP3 files every 200ms
3. Waits for file size to stabilize (non-zero, unchanged for 300ms)
4. Copies to `/tmp/tts-playback/tts-NNN.mp3` (stable location)
5. Plays the copy via `pw-play`

The copy step is critical: OpenClaw may delete the original file shortly after creation.

### Running

```bash
python3 auto-narrated-record.py \
  --prompt "Tour www.nust.na for prospective students" \
  --output ~/nust-tour.mp4 \
  --timeout 480 \
  --browser-class chromium
```

Options:
- `--prompt` / `--prompt-file` --- Recording instructions (required, mutually exclusive)
- `--output` --- Output video path (default: `~/auto-narrated.mp4`)
- `--timeout` --- OpenClaw agent timeout in seconds (default: 600)
- `--browser-class` --- Hyprland window class for fullscreen (default: `chromium`)
- `--pre-delay` --- Seconds after recorder starts before launching agent (default: 3)
- `--post-delay` --- Seconds to record after agent finishes (default: 5)

### Prompt Engineering Tips

- Use explicit numbered steps for predictable navigation sequences
- Include `Wait N seconds` after each TTS instruction for pacing
- Tell the agent to respond with `RECORDING_COMPLETE` when done
- Keep TTS text to 3 sentences or fewer per call for natural pacing
- Mention the browser class in the prompt so the agent knows not to resize
- Set timeout to 1.5x the expected tour duration

### Gotchas

**Agent may not complete all steps.** The LLM may return early, especially with shorter timeouts or vague prompts. Use explicit step-by-step prompts for reliable coverage.

**TTS files are ephemeral.** OpenClaw deletes TTS MP3 files after use. The watcher must copy files before playing. Direct `pw-play` on the original path will fail if the file is deleted.

**Fullscreen must be set ONCE before recording.** Do not call `ensure_fullscreen` during recording. The `focuswindow` dispatch causes Hyprland to briefly exit fullscreen, and wf-recorder captures the tiled frames with other windows visible.

**Long agent timeouts produce long videos.** If the agent takes 10 minutes, the video is 10 minutes regardless of narration density. Use shorter timeouts and denser prompts for concise videos.