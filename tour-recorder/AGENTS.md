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
