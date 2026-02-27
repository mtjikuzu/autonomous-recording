# Tour Recorder — AI Agent Reference

> This file helps AI agents (OpenCode, Cursor, Copilot, etc.) continue work on this project without repeating past mistakes. Read this FIRST before making changes.

## Project Overview

Autonomous narrated video recording pipeline. Takes a JSON spec describing steps (navigation, clicks, typing, terminal commands) and produces a polished MP4 with TTS narration — no manual intervention.

### Architecture (Oracle-reviewed)

- **Shot-based pipeline**: Spec → TTS generation → Browser capture → FFmpeg assembly
- **Playwright browser-native capture**: Uses `page.video` (not compositor/wf-recorder)
- **Pre-rendered TTS**: Kokoro ONNX generates WAV files before recording, mixed in post-production (not live playback)
- **Deterministic execution**: Assertions and retries per step
- **Synchronous Playwright**: Uses `playwright.sync_api` (NOT async)

### Files

| File | Purpose |
|---|---|
| `record-tour.py` | Main pipeline (~1254 lines). All action types, TTS, ffmpeg assembly |
| `bubblesort-tutorial.json` | Working coding tutorial spec (VS Code + Java) |
| `nust-tour.json` | Working website tour spec |
| `LESSONS.md` | Detailed pitfall reference — **read before debugging** |

### Capture Modes

- `"mode": "independent"` — Each step = fresh browser context + separate video clip. For website tours where state doesn't persist.
- `"mode": "continuous"` — Single browser context + single video. For coding tutorials where state persists between steps (e.g., typed code stays in editor).

### Key Dependencies

- Python 3, Playwright (Chromium), Kokoro ONNX TTS, FFmpeg
- Kokoro model: `~/.openclaw/models/kokoro-v1.0.onnx`
- Kokoro voices: `~/.openclaw/models/voices-v1.0.bin`

## Code-Server (VS Code in Browser) — Critical Knowledge

**If you're building a coding tutorial spec, read `LESSONS.md` thoroughly.** The most expensive lessons from this project all involve code-server DOM quirks.

### Quick Reference — What Works

| Task | Working Approach | DO NOT Use |
|---|---|---|
| Open terminal | Command Palette → "Terminal: Create New Terminal" | `Ctrl+Shift+\`` (broken in headless) |
| Focus terminal for typing | `page.evaluate()` JS to focus `.terminal-wrapper.active textarea.xterm-helper-textarea` | `Ctrl+\`` (toggles terminal closed), `.click()` (intercepted by folding icons) |
| Open a file | Pre-create on disk via `pre_setup`, then Ctrl+P Quick Open | `File: New Untitled Text File` + Save As (native dialog, not automatable) |
| Hide Copilot chat panel | DOM removal via `hide_secondary_sidebar` action (removes auxiliary bar node + fires resize) | CSS `display:none` (VS Code layout manager overrides it) |
| Type code in editor | `focus_editor` then `type_text` with delay | Typing without focus (goes to wrong element) |
| Keep cursor between typing steps | `{ "type": "pause", "duration": 0.3 }` | `focus_editor` between steps (`.view-lines.click()` moves cursor to click position) |

### Code-Server Settings (already configured)

Path: `~/.local/share/code-server/User/settings.json`

Must disable: trust dialog, welcome tab, auto-indent, auto-close brackets, format on type, suggestions, Copilot. See `LESSONS.md` for the full settings block.

## Narration Guidelines

- Use a **teacher persona**: conversational, adds insight beyond what's on screen
- Use analogies and real-world comparisons
- Don't just describe what's visible — explain *why* and add teaching moments
- Close with broader context (complexity, alternatives, what to learn next)
- Narration duration drives step timing — longer narration = longer step recording

## Common Pitfalls (summary — see LESSONS.md for details)

1. **Terminal typing silently fails** — The `terminal_type` action must use JavaScript `page.evaluate()` to focus the textarea. Playwright `.click()` is intercepted by editor overlays.
2. **URLs must match exactly in continuous mode** — Mismatched URLs cause page reloads that reset all state.
3. **`networkidle` hangs on some sites** — Always wrap in try/except with timeout fallback.
4. **Multiple textareas exist** — `textarea.xterm-helper-textarea` appears multiple times (one per terminal tab). `textarea.ime-text-area` belongs to the EDITOR, not the terminal. Always scope to `.terminal-wrapper.active`.
5. **Copilot chat panel reappears** — Must hide it AFTER opening files, not just at startup.

## Remotion Intro/Outro Overlays (New)

> Integration of Remotion motion graphics with the Playwright recording pipeline.

### How It Works

1. **Remotion Project** (`overlays/`): TypeScript/React scenes rendered to MP4 clips
   - `src/scenes/BubbleSortIntro.tsx`: 4s animated intro with branding
   - `src/scenes/BubbleSortOutro.tsx`: 5s outro with topic summary
   - Render: `cd overlays && npm run render:intro` / `npm run render:outro`

2. **Pipeline Integration** (`record-tour.py`):
   - `normalize_overlay_clip()`: Converts Remotion MP4 to consistent format (h264, 24kHz mono AAC)
   - `apply_overlays()`: Normalizes main video, then concatenates intro + main + outro with stream copy
   - Spec fields: `output.intro_clip` and `output.outro_clip` paths

3. **Assembly Flow**:
   ```
   TTS → Recording → assemble_continuous_video() → apply_overlays() → Final MP4
                                                        ↓
                                               [intro][main][outro]
   ```

### Critical Requirements

| Requirement | Why |
|---|---|
| All clips must have **identical** audio format | FFmpeg concat demuxer fails with mismatched sample rates/channels |
| Use `-shortest` in assembly | Prevents audio stream from exceeding video duration |
| Normalize main video too, not just overlays | Main video from assembly may have different format than overlays |
| Stream copy (`-c copy`) for concat | Fast, lossless, but requires format matching |
| Use `os.replace()` not `shutil.move()` | Atomic rename prevents partial files on interrupt |

### Adding Overlays to a Spec

```json
{
  "output": {
    "path": "~/output/tutorial.mp4",
    "intro_clip": "~/overlays/out/tutorial-intro.mp4",
    "outro_clip": "~/overlays/out/tutorial-outro.mp4"
  }
}
```

Omit `intro_clip`/`outro_clip` for backward compatibility (no overlays applied).

### Common Overlay Issues

| Symptom | Cause | Fix |
|---|---|---|
| Outro doesn't appear | Main video not normalized; format mismatch | Ensure `apply_overlays()` normalizes main video |
| Audio/video desync | `-shortest` missing or concat format mismatch | Add `-shortest` to assembly; verify all clips have same sample rate |
| Video truncated | `amix=duration=longest` produces longer audio than video | Use `-shortest` flag |
| Black frames at boundaries | Remotion scenes need consistent backgrounds | Ensure scenes fill 1920x1080 and have explicit background colors |

### Rendering Overlays

```bash
cd overlays/
npm install  # First time only

# Render individual clips
npm run render:intro   # → out/bubblesort-intro.mp4
npm run render:outro   # → out/bubblesort-outro.mp4

# Render both
npm run render:all
```

### Design Guidelines

Match existing thumbnail aesthetic:
- Background: `#0D1117` (dark)
- Panel BG: `#161B22`
- Accent: `#F7C948` (gold)
- White: `#FFFFFF`
- Code colors: Keywords `#FF7B72`, Values `#79C0FF`
- Font: JetBrains Mono



## Mixed Slides + Demo Workflow (New)

> Tutorial videos that alternate between PowerPoint-style slides (theory) and live code demonstrations (practice).

### Overview

Instead of just recording code-server demos, the mixed workflow lets you:
1. **Generate slides** via Gamma API (or create placeholder images)
2. **Record slide segments** showing theory/concepts
3. **Record demo segments** showing live coding
4. **Automatically assemble** everything in sequence

### Architecture

```
JSON Spec with "segments"
   │
   ├─► Generate/Cache Slides via Gamma API (if configured)
   │
   ├─► For each segment:
   │     ├── "slides" type → Serve slide-viewer.html → Record with Playwright
   │     └── "demo" type  → Record code-server with Playwright
   │
   └─► FFmpeg → Concatenate all segment clips → Final MP4
```

### Spec Format

```json
{
  "meta": { ... },
  "settings": { ... },
  "slides": {
    "generate": true,
    "theme": "Chisel",
    "cache_key": "tutorial-theory-v1",
    "content": [
      {
        "type": "title",
        "title": "Understanding Bubble Sort",
        "subtitle": "A gentle introduction"
      },
      {
        "type": "content",
        "title": "What is Bubble Sort?",
        "bullet_points": [
          "Simple comparison-based sorting algorithm",
          "Repeatedly steps through the list"
        ]
      }
    ]
  },
  "segments": [
    {
      "id": "intro-slides",
      "type": "slides",
      "narration": "Before we dive into code...",
      "slides": { "range": [1, 3], "advance_interval": 8000 },
      "duration": 24
    },
    {
      "id": "live-demo",
      "type": "demo",
      "narration": "Now let's implement this...",
      "url": "http://127.0.0.1:8080/?folder=...",
      "actions": [ ... ]
    }
  ]
}
```

### Segment Types

#### `slides` Segments

Display Gamma-generated (or placeholder) slides with auto-advance.

```json
{
  "id": "theory-part",
  "type": "slides",
  "narration": "Explanation of the concept...",
  "slides": {
    "range": [1, 4],        // Which slides to show (1-indexed)
    "advance_interval": 8000 // Milliseconds per slide
  },
  "duration": 32            // Total segment duration (for validation)
}
```

**How it works:**
1. Pipeline generates/cache slides via `gamma_client.py`
2. Serves `slide-viewer.html` with slides as query parameters
3. Playwright captures the slide viewer at 1920×1080
4. JavaScript auto-advances slides at specified interval

#### `demo` Segments

Live code-server recording (same as traditional steps).

```json
{
  "id": "coding-demo",
  "type": "demo",
  "narration": "Let's write the code...",
  "url": "http://127.0.0.1:8080/?folder=...",
  "actions": [
    { "type": "wait_for_load" },
    { "type": "type_text", "text": "public class...", "delay": 40 }
  ]
}
```

### Slide Generation

**Via Gamma API (requires API key):**
```bash
export GAMMA_API_KEY="sk-gamma-..."
python record-tour.py tutorial.json
```

**Fallback (no API key):**
Automatically generates placeholder slides using PIL with:
- Dark background (#0D1117)
- Title in gold accent (#F7C948)
- Bullet points in white
- 1920×1080 resolution

### Caching

Slides are cached by `cache_key` in `~/.cache/gamma-slides/{cache_key}/`:
- `slide-001.png`
- `slide-002.png`
- ...

Subsequent runs with the same `cache_key` reuse cached slides (no API call).

### Slide Viewer Component

**File:** `slide-viewer.html`

Features:
- Full-screen 1920×1080 display
- Keyboard navigation (arrow keys)
- Auto-advance mode
- Present mode (hides controls)
- Playwright automation API exposed via `window.slideViewer`

### Common Issues

| Issue | Cause | Fix |
|---|---|---|
| Slides not generating | Missing GAMMA_API_KEY env var | Set key or use fallback (auto) |
| Slide images blurry | PNG resolution mismatch | Ensure 1920×1080 PNGs |
| Wrong slide shown | Range indices off-by-one | Remember: 1-indexed range |
| Auto-advance too fast/slow | interval in milliseconds | Typical: 5000-8000ms per slide |
| Slide viewer not found | HTML file path wrong | Check `slide-viewer.html` exists next to `record-tour.py` |

### Workflow Comparison

| Feature | Traditional Steps | Mixed Segments |
|---|---|---|
| Format | `steps[]` | `segments[]` |
| Content | Code demos only | Slides + demos |
| Slides | ❌ | ✅ Via Gamma API |
| Backward compat | ✅ | ✅ (auto-detects format) |
| Spec complexity | Lower | Higher |
| Use case | Pure coding tutorials | Theory + practice |

### Backward Compatibility

The pipeline auto-detects spec format:
- `steps` present → traditional workflow
- `segments` present → mixed workflow
- Both work with existing intro/outro overlays

### Creating Slides

**Option 1: Gamma API (production quality)**
1. Get API key from gamma.app
2. Set `GAMMA_API_KEY` environment variable
3. Define slides in spec with `generate: true`
4. Run pipeline → slides generated and cached

**Option 2: Manual (full control)**
1. Create PNGs in `~/.cache/gamma-slides/{cache_key}/`
2. Name them `slide-001.png`, `slide-002.png`, etc.
3. Set matching `cache_key` in spec
4. Pipeline uses cached slides directly

**Option 3: Placeholder (fallback)**
- Don't set API key
- Pipeline auto-generates simple text-based slides using PIL

## Colab GPU TTS Offloading

> Offload TTS narration to Google Colab T4 GPU for higher-quality voice synthesis.

### Architecture overview:

- Local machine dispatches jobs to Google Drive
- Colab worker polls for jobs, generates WAVs, writes done.marker
- Local copies WAVs back, continues pipeline
- Communication bridge: Google Drive via rclone mount

### Three TTS Backends

| Backend | Flag | Model | Speed | Quality | Use Case |
|---|---|---|---|---|---|
| local | --tts-backend local | Kokoro 82M CPU | RTF ~0.5 | Good robotic | Default/fast iteration |
| colab | --tts-backend colab | Kokoro 82M GPU | RTF ~0.9 | Good robotic | NOT recommended (CPU is faster for this small model) |
| colab-f5 | --tts-backend colab-f5 | F5-TTS ~300M GPU | RTF ~0.8 | Near-realistic voice cloning | Production narration |

### Key Files

| File | Purpose |
|---|---|
| colab/colab_dispatcher.py | Local-side dispatchers (ColabTTSDispatcher, ColabF5TTSDispatcher) |
| colab/tts_worker.ipynb | Kokoro GPU Colab notebook |
| colab/f5_tts_worker.ipynb | F5-TTS voice cloning Colab notebook |
| colab/README.md | Detailed setup and usage docs |

### F5-TTS Spec Settings

```json
{
  "settings": {
    "f5_ref_audio": "voice-refs/myvoice.wav",
    "f5_ref_text": "The quick brown fox jumps over the lazy dog.",
    "f5_nfe_step": 32
  }
}
```

### Complete Workflow

1. Mount Google Drive locally: `rclone mount gdrive: ~/gdrive --vfs-cache-mode writes --dir-cache-time 5s --poll-interval 5s --daemon`
2. Create directories: `~/gdrive/autonomous-recording/tts-jobs/`, `f5-tts-jobs/`, `voice-refs/`
3. Copy notebook to Drive, open in Colab, set T4 GPU, Run All
4. Generate reference voice (for F5-TTS): python script using Kokoro to create 8-12s WAV
5. Run pipeline: `python record-tour.py spec.json --tts-backend colab-f5`
6. Pipeline dispatches job → Colab generates → Pipeline copies back → continues

### Critical Requirements

| Requirement | Why |
|---|---|
| Reference audio MUST be 6-12 seconds | F5-TTS clips audio >12s internally, causing ref_text/audio mismatch and text bleeding |
| Reference text MUST match audio exactly | Mismatched text causes generated audio to contain ref_text fragments |
| Reference content MUST be unrelated to narration | Semantically similar ref_text bleeds into generated output |
| rclone needs `--dir-cache-time 5s --poll-interval 5s` | Default cache is too slow for responsive job detection (10-50s Drive sync latency) |
| Google Drive sync adds 10-50s latency each way | Dispatcher sync_delay=10s and poll_interval=5s account for this |

### Discovery: Kokoro is faster on CPU than Colab T4

Note that Kokoro-82M (RTF 0.50 local CPU vs 0.92 Colab T4) is too small to benefit from GPU acceleration. CUDA overhead hurts. F5-TTS (~300M params) is genuinely GPU-bound and benefits from T4.

## NVENC GPU Video Encoding

> Offload final video encoding to Colab T4 NVENC for faster encode times.

### How It Works

The pipeline assembles the video locally with `libx264`, then optionally re-encodes with NVENC on Colab T4:

```
assemble_*_video() → _maybe_nvenc_reencode() → apply_overlays() → Final MP4
   (local libx264)      (Colab h264_nvenc)        (stream copy)
```

### Usage

```bash
# Enable NVENC encoding
python record-tour.py tutorial.json --encode-backend colab-nvenc

# Combine with F5-TTS
python record-tour.py tutorial.json --tts-backend colab-f5 --encode-backend colab-nvenc
```

### Key Files

| File | Purpose |
|---|---|
| colab/encode_worker.ipynb | NVENC GPU Colab notebook |
| colab/colab_dispatcher.py | Includes `ColabNVENCDispatcher` |

### CLI Flags

| Flag | Default | Description |
|---|---|---|
| `--encode-backend` | `local` | `local` (libx264 CPU) or `colab-nvenc` (T4 GPU) |
| `--nvenc-drive-path` | auto-detect | Path to Google Drive encode-jobs directory |
| `--nvenc-timeout` | `1200` | Max seconds to wait for Colab worker |

### When to Use

- **Short videos (<2 min)**: Skip — local is fast enough
- **Long videos (8+ min)**: Recommended — 5-10x speedup
- **Batch processing**: Recommended
