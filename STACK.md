# Autonomous Video Recording — Technology Stack

> Documents the full software and technology stack used to produce the **Bubble Sort** and **Methods** Java tutorial videos.

---

## Videos Produced

| File | Topic | Duration (approx) |
|---|---|---|
| `output/bubblesort-tutorial.mp4` | Java Bubble Sort — build, compile, run in VS Code | ~6 min |
| `output/functions-tutorial.mp4` | Java Methods — parameters, return types, overloading, recursion | ~8 min |

---

## Pipeline Overview

```
JSON Spec
   │
   ├─► Pre-setup shell commands (mkdir, touch, clean workspace)
   │
   ├─► Kokoro ONNX TTS → WAV audio files (one per step, pre-rendered)
   │
   ├─► Playwright (Chromium) → MP4 video clips (one per step, browser-native capture)
   │        │
   │        └─ code-server (VS Code in browser) for all coding actions
   │
   └─► FFmpeg → assemble clips + audio → final MP4
```

**Mode**: `continuous` — single browser context persists across all steps so typed code accumulates naturally.

---

## Core Recording Engine

### `tour-recorder/record-tour.py`
Custom Python pipeline (~1250 lines). Orchestrates the full recording lifecycle:
- Parses JSON spec
- Runs pre-setup shell commands
- Pre-renders all TTS audio
- Drives Playwright browser step-by-step
- Assembles clips with FFmpeg in post

---

## Software Stack

### Python Runtime
| Component | Version |
|---|---|
| Python | 3.14.0 |

### Browser Automation
| Component | Version | Role |
|---|---|---|
| Playwright (Python) | 1.58.0 | Browser control, video capture |
| Chromium | 143.0.7499.192 (Arch Linux) | Headless browser runtime |

Playwright's built-in `page.video` API captures video natively — no screen compositor or external capture tool required.

### Text-to-Speech (TTS)
| Component | Version | Role |
|---|---|---|
| kokoro-onnx | 0.4.7 | TTS inference engine |
| onnxruntime | 1.24.2 | ONNX model runtime |
| soundfile | 0.13.1 | WAV file I/O |
| Kokoro model | v1.0 | `~/.openclaw/models/kokoro-v1.0.onnx` |
| Kokoro voices | v1.0 | `~/.openclaw/models/voices-v1.0.bin` |
| Voice used | `am_michael` | American English male voice |

TTS is **pre-rendered** before recording begins. Each step's narration is converted to WAV, its duration measured, then mixed in post — not played live during capture.

### Video Assembly
| Component | Version | Role |
|---|---|---|
| FFmpeg | n8.0.1 | Clip assembly, audio mixing, loudnorm, encoding |
| FFprobe | n8.0.1 | Duration probing per clip |

**Output encoding settings:**
- Video codec: `libx264`, preset `medium`, CRF `20`
- Audio codec: `aac`, bitrate `192k`
- Loudness normalization: enabled (`loudnorm` filter)

### Motion Graphics (Overlays)

| Component | Version | Role |
|---|---|---|
| Remotion | 4.0.296 | React-based motion graphics for intro/outro animations |
| Node.js | 23.9.0 | Remotion runtime |
| TypeScript | 5.7.3 | Scene definitions |

**Overlay pipeline:**
```
Remotion Scene (TypeScript/React) → Remotion CLI → MP4 overlay clip
                                    ↓
                        [Intro clip] + [Main video] + [Outro clip] → FFmpeg concat → Final MP4
```

**Overlay specs:**
- Intro: 4 seconds, 30fps, 1920×1080
- Outro: 5 seconds, 30fps, 1920×1080
- Branding: Matches thumbnail aesthetic (`#0D1117` bg, `#F7C948` accent)

**Project location:** `overlays/` — TypeScript React scenes rendered to MP4
- Resolution: `1920×1080`

### Code Editor (Tutorial Subject)
| Component | Version | Role |
|---|---|---|
| code-server | 4.108.2 (VS Code 1.108.2) | Browser-based VS Code served at `http://127.0.0.1:8080` |
| Java (OpenJDK) | 25.0.1 | Compiling and running tutorial code |
| javac | 25.0.1 | Java compiler used in terminal steps |

---

## Tutorial Spec Format

Tutorials are defined as JSON files in `tour-recorder/`:

| File | Tutorial |
|---|---|
| `bubblesort-tutorial.json` | Bubble Sort |
| `functions-tutorial.json` | Methods / Functions |

Each spec contains:
- **`meta`** — title, description, target/max duration
- **`settings`** — viewport, voice, speech speed, browser, mode (`continuous`)
- **`pre_setup`** — shell commands to run before recording (workspace setup)
- **`output`** — output path and encoding parameters
- **`steps[]`** — ordered list of steps, each with:
  - `id` — unique step name
  - `url` — browser URL for this step
  - `narration` — TTS script for this step
  - `actions[]` — browser actions (wait, click, type, key press, command palette, etc.)
  - `assertions[]` — post-step checks

---

## Infrastructure

| Component | Details |
|---|---|
| OS | Arch Linux (rolling) |
| Kernel | 6.18.3-arch1-1 |
| Docker | 29.2.1 (used for other services, not the recording pipeline itself) |
| OpenClaw | 2026.2.22-2 — agent platform used to orchestrate and run the pipeline |

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Pre-rendered TTS (not live) | Deterministic timing — audio duration drives step length |
| Browser-native video capture | No compositor dependency, works headless |
| `continuous` mode | Code typed in step N is still visible in step N+1 |
| code-server (not desktop VS Code) | Playwright can automate a browser; can't automate a native app |
| JSON spec-driven | Fully reproducible — re-run produces identical video |
| Remotion for intro/outro | Complements Playwright (handles motion graphics, not screen recording) |
