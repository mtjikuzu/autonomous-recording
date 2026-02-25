# Tour Recorder — Lessons Learned

> Every entry here cost at least one failed recording attempt. Read before making changes.

---

## Table of Contents

1. [Terminal Interaction](#1-terminal-interaction)
2. [File Creation in Code-Server](#2-file-creation-in-code-server)
3. [Copilot Chat Panel / Secondary Sidebar](#3-copilot-chat-panel--secondary-sidebar)
4. [Code Typing and Indentation](#4-code-typing-and-indentation)
5. [Cursor Positioning Between Typing Steps](#5-cursor-positioning-between-typing-steps)
6. [URL Consistency in Continuous Mode](#6-url-consistency-in-continuous-mode)
7. [Trust Dialog and Welcome Tab](#7-trust-dialog-and-welcome-tab)
8. [Network Wait States](#8-network-wait-states)
9. [Code-Server Settings Reference](#9-code-server-settings-reference)
10. [JSON Spec Authoring Tips](#10-json-spec-authoring-tips)
11. [Narration and Duration](#11-narration-and-duration)
12. [FFmpeg Overlay Concatenation](#12-ffmpeg-overlay-concatenation)
13. [Remotion Integration](#13-remotion-integration)
---

## 1. Terminal Interaction

### Problem: Terminal commands not executing

The `terminal_type` action needs to focus the terminal textarea before typing. Multiple approaches were tried and failed before finding the working solution.

### What DOESN'T work

| Approach | Why it fails |
|---|---|
| `Ctrl+\`` keyboard shortcut | **Toggles** the terminal. If already open (from Command Palette → "Terminal: Create New Terminal"), this **closes** it instead of focusing it. |
| `Ctrl+Shift+\`` | Broken in headless Chromium / code-server. Does nothing. |
| `page.locator("textarea.xterm-helper-textarea").first.click()` | The editor's **code folding icons** (`<div class="cldr codicon codicon-folding-expanded">`) sit in a gutter overlay that **intercepts pointer events**. Playwright's actionability check detects the overlay and times out after 30s. |
| `page.locator("textarea.xterm-helper-textarea").first.click(force=True)` | Better, but still unreliable. The `.first` selector may match the **wrong textarea** — there's one per terminal tab, plus the editor has its own `textarea.ime-text-area`. |
| `.terminal-wrapper` or `.xterm` with `.first` | Multiple matches. `.first` picks the first in DOM order, which may be an **inactive/hidden** terminal from a previous tab. |

### What WORKS

Use `page.evaluate()` with JavaScript to directly focus the correct textarea inside the **active** terminal wrapper. This completely bypasses Playwright's actionability checks.

```python
focused = page.evaluate("""() => {
    const active = document.querySelector('.terminal-wrapper.active');
    if (!active) return 'no-active-wrapper';
    const ta = active.querySelector('textarea.xterm-helper-textarea');
    if (!ta) return 'no-textarea';
    ta.focus();
    return 'focused';
}""")
if focused != 'focused':
    # Fallback: force-click the active wrapper
    page.locator('.terminal-wrapper.active').first.click(force=True)
page.wait_for_timeout(300)
page.keyboard.type(text, delay=0)
```

### Key insight: DOM structure

When a terminal is open in code-server, the DOM looks like:

```
.integrated-terminal (panel container)
  .terminal-wrapper (one per terminal tab — most are hidden)
  .terminal-wrapper.active (the visible one)
    .xterm
      textarea.xterm-helper-textarea  ← THIS is the keyboard input target
```

The editor ALSO has a textarea: `textarea.ime-text-area`. **Never use a selector that matches both.** Always scope to `.terminal-wrapper.active`.

### Opening the terminal

Use Command Palette, not keyboard shortcuts:

```json
{ "type": "command_palette", "command": "Terminal: Create New Terminal" },
{ "type": "pause", "duration": 3.0 },
{ "type": "terminal_type", "text": "your command here", "press_enter": true }
```

The 3-second pause after opening is important — the terminal needs time to initialize the shell.

---

## 2. File Creation in Code-Server

### Problem: Can't create new files through the UI

### What DOESN'T work

| Approach | Why it fails |
|---|---|
| Command Palette → "File: New Untitled Text File" + "File: Save As..." | The Save As dialog is a **native OS dialog**, not a VS Code text input. Playwright cannot interact with it in headless mode. |

### What WORKS

1. **Pre-create the file on disk** using the `pre_setup` commands in the JSON spec:
   ```json
   "pre_setup": [
     "mkdir -p /home/user/project",
     "touch /home/user/project/MyFile.java"
   ]
   ```

2. **Open it with Ctrl+P Quick Open**:
   ```json
   { "type": "press_key", "key": "Control+p" },
   { "type": "pause", "duration": 0.8 },
   { "type": "type_text", "text": "MyFile.java", "delay": 40 },
   { "type": "pause", "duration": 0.5 },
   { "type": "press_key", "key": "Enter" }
   ```

This is reliable and looks natural on video (Quick Open is how most developers open files anyway).

---

## 3. Copilot Chat Panel / Secondary Sidebar

### Problem: Built-in chat panel covers part of the editor

Code-server v4.108+ has a built-in Copilot chat panel in the **auxiliary sidebar** (secondary side bar) that opens by default. It's NOT a separate extension — it's part of the workbench.

### What DOESN'T work

| Approach | Why it fails |
|---|---|
| CSS `display: none` on the panel | VS Code's **layout manager** overrides inline styles. The panel reappears on next layout cycle. |
| `Ctrl+Alt+B` keyboard shortcut | Works in standalone tests but has timing issues during recording — the toggle may not register if focus is elsewhere. |

### What WORKS

DOM removal via JavaScript, implemented as the `hide_secondary_sidebar` action:

```python
page.evaluate("""() => {
    const aux = document.querySelector('.auxiliarybar');
    if (aux) {
        aux.remove();
        window.dispatchEvent(new Event('resize'));
    }
}""")
```

The `resize` event forces VS Code to recalculate its layout without the removed node.

### IMPORTANT: Timing

The chat panel can **reappear after opening files** (Quick Open triggers re-rendering of the sidebar). Always call `hide_secondary_sidebar`:
1. Once during workspace setup (after dismissing popups)
2. Again **after** opening a file with Ctrl+P

```json
{ "type": "press_key", "key": "Enter" },
{ "type": "pause", "duration": 2.0 },
{ "type": "hide_secondary_sidebar" },
{ "type": "focus_editor" }
```

---

## 4. Code Typing and Indentation

### Problem: VS Code auto-indent doubles indentation

When typing code that already includes spaces/tabs for indentation, VS Code's auto-indent adds **additional** indentation on top. Result: code shifts further and further right with each line.

### Solution

Disable ALL auto-formatting in code-server settings:

```json
{
    "editor.autoIndent": "none",
    "editor.formatOnType": false,
    "editor.formatOnPaste": false,
    "editor.autoClosingBrackets": "never",
    "editor.autoClosingQuotes": "never",
    "editor.autoSurround": "never",
    "editor.suggest.showSnippets": false,
    "editor.quickSuggestions": { "other": false, "comments": false, "strings": false },
    "editor.suggestOnTriggerCharacters": false,
    "editor.acceptSuggestionOnEnter": "off",
    "editor.tabCompletion": "off",
    "editor.wordBasedSuggestions": "off"
}
```

The typed text in the JSON spec should contain the **exact** whitespace you want in the file. The `type_text` action types it character-by-character.

---

## 5. Cursor Positioning Between Typing Steps

### Problem: Code appears in wrong location after splitting typing across steps

When typing is split across multiple steps (e.g., class declaration in step 1, method body in step 2), the cursor must remain at the end of the last typed character.

### What DOESN'T work

Using `focus_editor` (which calls `.view-lines.click()`) between typing steps. The click targets the **center of the editor view**, which **moves the cursor** to that position instead of keeping it at the end of the previously typed text.

### What WORKS

Use a short pause instead:

```json
// Step N: type some code
{ "type": "type_text", "delay": 40, "text": "first chunk of code\n" },

// Step N+1: continue typing (cursor stays at end)
{ "type": "pause", "duration": 0.3 },
{ "type": "type_text", "delay": 40, "text": "next chunk of code\n" }
```

Only use `focus_editor` for the **first** typing step (to ensure focus is in the editor). All subsequent steps should use `pause` to maintain cursor position.

---

## 6. URL Consistency in Continuous Mode

### Problem: Page reloads between steps reset all state

In `"mode": "continuous"`, all steps share a single browser context. If step URLs differ, the pipeline navigates to the new URL, causing a **full page reload** that:
- Closes open files
- Resets the sidebar
- Re-opens the welcome tab
- Loses terminal state

### Solution

Use **identical URLs** for every step in a continuous-mode spec:

```json
// CORRECT — all steps use the same URL
{ "id": "step-1", "url": "http://127.0.0.1:8080/?folder=/home/user/project" },
{ "id": "step-2", "url": "http://127.0.0.1:8080/?folder=/home/user/project" },

// WRONG — different URL triggers reload
{ "id": "step-1", "url": "http://127.0.0.1:8080" },
{ "id": "step-2", "url": "http://127.0.0.1:8080/?folder=/home/user/project" }
```

The `?folder=` parameter MUST be present and identical on every step.

---

## 7. Trust Dialog and Welcome Tab

### Problem: Trust prompts and Welcome tab appear during recording

Code-server shows workspace trust dialogs and a Welcome/Walkthrough tab on first load.

### Solution

Disable permanently via settings:

```json
{
    "security.workspace.trust.enabled": false,
    "workbench.startupEditor": "none",
    "workbench.welcomePage.walkthroughs.openOnInstall": false
}
```

Also dismiss any remaining popups with the `dismiss_popups` action and Escape keys at the start of the recording:

```json
{ "type": "dismiss_popups" },
{ "type": "press_key", "key": "Escape" },
{ "type": "press_key", "key": "Escape" },
{ "type": "command_palette", "command": "View: Close All Editors" }
```

---

## 8. Network Wait States

### Problem: `networkidle` wait hangs indefinitely

Some sites (especially Drupal-based or sites with persistent WebSocket connections) never reach `networkidle` state because background requests keep firing.

### Solution

Wrap `networkidle` in a try/except with a timeout fallback:

```python
try:
    page.wait_for_load_state("networkidle", timeout=15000)
except:
    page.wait_for_timeout(3000)  # fallback: just wait 3 seconds
```

This is already implemented in `record-tour.py`'s `wait_for_load` action.

---

## 9. Code-Server Settings Reference

Full working settings at `~/.local/share/code-server/User/settings.json`:

```json
{
    "security.workspace.trust.enabled": false,
    "workbench.startupEditor": "none",
    "workbench.welcomePage.walkthroughs.openOnInstall": false,
    "workbench.colorTheme": "Default Dark Modern",
    "editor.fontSize": 18,
    "editor.autoIndent": "none",
    "editor.formatOnType": false,
    "editor.formatOnPaste": false,
    "editor.autoClosingBrackets": "never",
    "editor.autoClosingQuotes": "never",
    "editor.autoSurround": "never",
    "editor.suggest.showSnippets": false,
    "editor.quickSuggestions": { "other": false, "comments": false, "strings": false },
    "editor.suggestOnTriggerCharacters": false,
    "editor.acceptSuggestionOnEnter": "off",
    "editor.tabCompletion": "off",
    "editor.wordBasedSuggestions": "off",
    "github.copilot.enable": { "*": false },
    "github.copilot.editor.enableAutoCompletions": false
}
```

Code-server config at `~/.config/code-server/config.yaml`:

```yaml
bind-addr: 127.0.0.1:8080
auth: none
cert: false
```

---

## 10. JSON Spec Authoring Tips

### Step Structure

```json
{
    "id": "unique-step-id",
    "url": "http://127.0.0.1:8080/?folder=/home/user/project",
    "narration": "Teacher-style narration text...",
    "actions": [
        { "type": "action_type", "param": "value" }
    ],
    "assertions": [
        { "type": "url_contains", "value": "expected" }
    ]
}
```

### Available Action Types

| Action | Parameters | Notes |
|---|---|---|
| `wait_for_load` | — | Waits for page load with networkidle fallback |
| `wait_for_selector` | `selector`, `state`, `timeout` | Wait for DOM element |
| `pause` | `duration` (seconds) | Static wait |
| `press_key` | `key` | Keyboard shortcut (e.g., `"Control+s"`) |
| `type_text` | `text`, `delay` (ms per char) | Types character by character |
| `terminal_type` | `text`, `press_enter` | Focuses active terminal via JS, then types |
| `command_palette` | `command` | Opens Ctrl+Shift+P and types command |
| `focus_editor` | — | Clicks `.view-lines` to focus editor |
| `select_all_and_delete` | — | Ctrl+A then Delete |
| `scroll` | `direction`, `amount` | Scroll the page |
| `highlight_lines` | `from_line`, `to_line` | Select line range in editor |
| `dismiss_popups` | — | Clicks common cookie/consent buttons |
| `hide_secondary_sidebar` | — | Removes auxiliary bar DOM node |

### Pre-setup Commands

Run shell commands before recording starts. Use for:
- Creating directories and empty files
- Cleaning up artifacts from previous runs
- Any filesystem setup the recording depends on

```json
"pre_setup": [
    "mkdir -p /home/user/project",
    "rm -f /home/user/project/*.class",
    "touch /home/user/project/Main.java"
]
```

---

## 11. Narration and Duration

### Duration Constraints

- `target_duration_seconds`: Total narration must fit within this. If it exceeds, the pipeline aborts.
- `max_duration_seconds`: The final video (narration + action time) must fit within this.
- **Rule of thumb**: Set target to ~1.2x the estimated narration length. Set max to ~1.5x target.
- Teacher-style narration is ~30% longer than dry descriptions. Budget accordingly.

### Narration Tips

- Each step's video duration = max(narration_duration, action_duration) + padding
- Longer narration = more time for actions to complete naturally
- If actions take longer than narration, there will be silent padding at the end of the step
- Keep narration for typing-heavy steps shorter (the typing itself takes time)
- Keep narration for terminal/compile steps descriptive (fills time while commands run)

### Voice Configuration

```json
"settings": {
    "voice": "am_michael",
    "speech_speed": 1.0,
    "language": "en-us"
}
```

Available Kokoro voices: `am_michael`, `af_heart`, `af_bella`, etc. See Kokoro ONNX docs for full list.


---


## 12. FFmpeg Overlay Concatenation

> Hard-won lessons about concatenating intro/outro overlays with main video content. **Read this before modifying `apply_overlays()`.**

### Problem: FFmpeg concat demuxer produces corrupt output with mismatched audio formats

When concatenating clips with the FFmpeg concat demuxer (`-f concat -i list.txt`), if the input files have different audio sample rates, channel layouts, or codecs, the output will have:
- Truncated video (e.g., 84s instead of 384s)
- Audio/video stream duration mismatch
- Missing segments (outro doesn't appear)

### What DOESN'T work

| Approach | Why it fails |
|---|---|
| Concat without re-encoding mismatched formats | Concat demuxer requires identical stream parameters; different sample rates (48kHz vs 96kHz) or channels (stereo vs mono) cause corruption |
| Re-encoding only overlay clips | Main video from `assemble_continuous_video()` may have different format (96kHz from AAC encoder vs 24kHz in overlays) |
| Using `-c:a aac` without explicit `-ar` and `-ac` | AAC encoder auto-selects sample rate (often 48kHz or 96kHz), ignoring the `anullsrc` input rate |
| `shutil.move()` to replace output file | Non-atomic; partial file on interrupt. Use `os.replace()` instead |

### What WORKS

**Normalize ALL clips to identical format before concat:**

```python
# Normalize overlays (already done)
normalize_overlay_clip(intro_source, intro_normalized)  # → 24kHz mono AAC
normalize_overlay_clip(outro_source, outro_normalized)  # → 24kHz mono AAC

# ALSO normalize the main video
ffmpeg -i main_video.mp4 \
    -vf "fps=30,format=yuv420p" \
    -c:v libx264 -preset medium -crf 20 \
    -ac 1 -ar 24000 -c:a aac -b:a 192k \
    -movflags +faststart \
    main_normalized.mp4

# Concat with stream copy (fast, lossless)
ffmpeg -f concat -safe 0 -i concat_list.txt \
    -c copy -movflags +faststart \
    final_with_overlays.mp4
```

### Key Requirements

All clips MUST have identical:
- Video codec: h264 (High profile)
- Resolution: 1920x1080
- Framerate: 30 fps
- Pixel format: yuv420p
- Audio codec: AAC (LC)
- Sample rate: 24000 Hz (or any fixed rate, but MUST match)
- Channels: 1 (mono)

### Verification

```bash
# Check formats match before concat
for f in intro.mp4 main.mp4 outro.mp4; do
    ffprobe -v quiet \
        -show_entries stream=codec_name,sample_rate,channels,width,height,r_frame_rate \
        -of compact "$f"
done

# After concat, verify stream alignment
ffprobe -v quiet -show_entries stream=duration -of csv final.mp4
# Should show video and audio durations matching within ~0.1s
```

### Audio/Video Duration Mismatch Fix

If audio stream is longer than video (e.g., from `amix=duration=longest` filter), add `-shortest` to the FFmpeg command:

```python
cmd = [
    "ffmpeg", "-y",
    "-i", video_input,
    "-i", audio_input,
    "-c:v", "libx264",
    "-c:a", "aac",
    "-shortest",  # ← Stop when shortest stream ends
    output_path
]
```


---


## 13. Remotion Integration

> Lessons from integrating Remotion motion graphics with the autonomous recording pipeline.

### Project Structure

```
overlays/
├── package.json          # Remotion dependencies and render scripts
├── tsconfig.json         # TypeScript config
├── remotion.config.ts    # Remotion CLI settings
├── src/
│   ├── index.ts          # Entry point
│   ├── Root.tsx          # Composition definitions (duration, fps, dimensions)
│   └── scenes/
│       ├── BubbleSortIntro.tsx   # 4s intro scene
│       └── BubbleSortOutro.tsx   # 5s outro scene
└── out/                  # Rendered MP4 outputs (gitignored)
```

### Creating New Overlays

1. **Create scene file** in `src/scenes/MyScene.tsx`:
   - Use Remotion components: `AbsoluteFill`, `spring`, `interpolate`
   - Set explicit background color (e.g., `backgroundColor: '#0D1117'`)
   - Animation duration must match composition duration exactly

2. **Register in Root.tsx**:
   ```typescript
   export const RemotionRoot: React.FC = () => {
       return (
           <>
               <Composition
                   id="MyIntro"
                   component={MyIntro}
                   durationInFrames={4 * 30}  // 4 seconds at 30fps
                   fps={30}
                   width={1920}
                   height={1080}
               />
           </>
       );
   };
   ```

3. **Add render script** to `package.json`:
   ```json
   "scripts": {
       "render:myintro": "remotion render src/index.ts MyIntro out/myintro.mp4"
   }
   ```

### Branding Consistency

Match the existing thumbnail aesthetic used in `generate-thumbnails.py`:

| Element | Color | Usage |
|---|---|---|
| Background | `#0D1117` | Scene background |
| Panel BG | `#161B22` | Code panels, cards |
| Accent | `#F7C948` | Highlights, badges, CTA |
| White | `#FFFFFF` | Primary text |
| Keyword | `#FF7B72` | Code syntax (keywords) |
| Value | `#79C0FF` | Code syntax (values) |
| Font | JetBrains Mono | All text |

### Common Remotion Pitfalls

| Issue | Solution |
|---|---|
| Scene renders black | Ensure `AbsoluteFill` has explicit `backgroundColor` style |
| Animation timing off | Check `durationInFrames` matches actual animation length |
| Text blurry at edges | Use `transform: translate()` with `interpolate()` instead of direct x/y |
| Chrome not found | Remotion auto-downloads Chrome on first render; ensure network access |

### Pipeline Integration Checklist

Before running `record-tour.py` with overlays:

- [ ] Overlay MP4s rendered to `overlays/out/`
- [ ] All overlays have matching duration (check with `ffprobe`)
- [ ] Spec has `output.intro_clip` and/or `output.outro_clip` paths set
- [ ] Paths in spec use `~` (home) or absolute paths (not relative)
- [ ] `.gitignore` includes `overlays/node_modules/` and `overlays/out/`


---

> **Document version:** 2025-02-25
> **Last updated:** Added Remotion overlay lessons and FFmpeg concat troubleshooting
