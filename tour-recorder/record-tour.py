#!/usr/bin/env python3

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


import soundfile as sf
from kokoro_onnx import Kokoro
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

KOKORO_MODEL = os.path.expanduser("~/.openclaw/models/kokoro-v1.0.onnx")
KOKORO_VOICES = os.path.expanduser("~/.openclaw/models/voices-v1.0.bin")
FFMPEG_BIN = "/usr/bin/ffmpeg"
FFPROBE_BIN = "/usr/bin/ffprobe"

POPUP_SELECTORS = [
    "button:has-text('Accept')",
    "button:has-text('I agree')",
    "button:has-text('Allow all')",
    "button:has-text('Got it')",
    "button:has-text('Close')",
    "text=Accept All",
    "text=Accept all",
    "text=AGREE",
    "#onetrust-accept-btn-handler",
    ".cookie-accept",
    ".cookie-consent-accept",
    # VS Code / code-server trust dialog (uses <a role='button'> not <button>)
    "a[role='button']:has-text('Yes, I trust the authors')",
    "a[role='button']:has-text('Yes, I trust')",
    "button:has-text('Yes, I trust the authors')",
    "button:has-text('Trust folder')",
    # VS Code walkthrough/welcome close
    "button:has-text('Mark Done')",
]


class TourError(Exception):
    pass


@dataclass
class StepResult:
    step_id: str
    attempt_count: int
    success: bool
    clip_path: Path | None
    audio_path: Path
    audio_duration: float
    step_elapsed: float
    video_offset: float = 0.0
    step_end_offset: float | None = None


def log(message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def run_cmd(cmd: list[str], description: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout)[-1200:]
        raise TourError(f"{description} failed: {tail}")
    return result


def ffprobe_duration(path: Path) -> float:
    cmd = [
        FFPROBE_BIN,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = run_cmd(cmd, f"ffprobe duration for {path.name}")
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise TourError(f"Invalid duration from ffprobe for {path}") from exc


def ensure_tooling() -> None:
    for binary in (FFMPEG_BIN, FFPROBE_BIN):
        if not Path(binary).exists():
            raise TourError(f"Missing required binary: {binary}")
    if not Path(KOKORO_MODEL).exists():
        raise TourError(f"Missing Kokoro model: {KOKORO_MODEL}")
    if not Path(KOKORO_VOICES).exists():
        raise TourError(f"Missing Kokoro voices: {KOKORO_VOICES}")


def run_pre_setup(spec: dict[str, Any]) -> None:
    commands = spec.get("pre_setup", [])
    if not commands:
        return
    log(f"Phase A: running {len(commands)} pre-setup command(s)")
    for idx, cmd_str in enumerate(commands, start=1):
        log(f"Phase A: pre-setup [{idx}/{len(commands)}]: {cmd_str}")
        result = subprocess.run(cmd_str, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            tail = (result.stderr or result.stdout)[-500:]
            raise TourError(f"Pre-setup command failed: {cmd_str}\n{tail}")
        if result.stdout.strip():
            log(f"Phase A: pre-setup output: {result.stdout.strip()[:200]}")


def load_tour_spec(spec_path: Path) -> dict[str, Any]:
    try:
        with spec_path.open("r", encoding="utf-8") as handle:
            spec = json.load(handle)
    except FileNotFoundError as exc:
        raise TourError(f"Spec not found: {spec_path}") from exc
    except json.JSONDecodeError as exc:
        raise TourError(f"Spec JSON is invalid: {exc}") from exc

    # Allow either traditional 'steps' or new 'segments' format
    has_steps = "steps" in spec
    has_segments = "segments" in spec

    if not has_steps and not has_segments:
        raise TourError("Spec must have either 'steps' or 'segments' field")

    if has_steps and (not isinstance(spec["steps"], list) or not spec["steps"]):
        raise TourError("Spec 'steps' must be a non-empty array")

    if has_segments and (
        not isinstance(spec["segments"], list) or not spec["segments"]
    ):
        raise TourError("Spec 'segments' must be a non-empty array")

    meta = spec["meta"]
    settings = spec["settings"]
    output = spec["output"]

    required_meta = ("title", "target_duration_seconds", "max_duration_seconds")
    required_settings = (
        "viewport",
        "video_size",
        "voice",
        "speech_speed",
        "language",
        "default_step_timeout",
        "max_retries_per_step",
        "browser",
    )
    required_output = (
        "path",
        "video_codec",
        "video_preset",
        "video_crf",
        "audio_codec",
        "audio_bitrate",
    )

    for key in required_meta:
        if key not in meta:
            raise TourError(f"Spec meta missing: {key}")
    for key in required_settings:
        if key not in settings:
            raise TourError(f"Spec settings missing: {key}")
    for key in required_output:
        if key not in output:
            raise TourError(f"Spec output missing: {key}")

    mode = str(settings.get("mode", "independent")).strip().lower()
    if mode not in {"independent", "continuous"}:
        raise TourError("Spec settings.mode must be 'independent' or 'continuous'")
    settings["mode"] = mode

    # Optional F5-TTS voice cloning settings (used with --tts-backend colab-f5)
    if "f5_ref_audio" in settings:
        ref_audio = Path(str(settings["f5_ref_audio"])).expanduser()
        if not ref_audio.suffix.lower() in (".wav", ".mp3", ".flac", ".ogg"):
            raise TourError(
                f"settings.f5_ref_audio must be a WAV/MP3/FLAC/OGG file, got: {ref_audio.name}"
            )
    if "f5_nfe_step" in settings:
        nfe = int(settings["f5_nfe_step"])
        if nfe < 1 or nfe > 128:
            raise TourError("settings.f5_nfe_step must be between 1 and 128")

    # Validate step/segment IDs
    item_ids: set[str] = set()
    items = spec.get("steps") or spec.get("segments", [])
    for idx, item in enumerate(items, start=1):
        for field in ("id", "narration"):
            if field not in item:
                item_type = "step" if "steps" in spec else "segment"
                raise TourError(
                    f"{item_type.capitalize()} {idx} missing required field: {field}"
                )
        item_id = str(item["id"])
        if item_id in item_ids:
            raise TourError(f"Duplicate id: {item_id}")
        item_ids.add(item_id)

    target = float(meta["target_duration_seconds"])
    if target <= 0:
        raise TourError("target_duration_seconds must be > 0")
    max_dur = float(meta["max_duration_seconds"])
    if max_dur < target:
        raise TourError("max_duration_seconds must be >= target_duration_seconds")

    # Calculate budget for steps (not segments)
    if "steps" in spec:
        budget = target / len(spec["steps"])
        for step in spec["steps"]:
            step["time_budget_seconds"] = budget

    return spec


def create_work_dirs(base: Path | None = None) -> dict[str, Path]:
    if base is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = Path(f"/tmp/tour-recording-{stamp}")
    audio = base / "audio"
    clips = base / "clips"
    assembly = base / "assembly"
    screenshots = base / "screenshots"
    for directory in (base, audio, clips, assembly, screenshots):
        directory.mkdir(parents=True, exist_ok=True)
    return {
        "base": base,
        "audio": audio,
        "clips": clips,
        "assembly": assembly,
        "screenshots": screenshots,
    }


def write_wav_atomic(path: Path, samples: Any, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f"{path.stem}-",
            suffix=".wav",
            dir=str(path.parent),
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
        sf.write(str(temp_path), samples, sample_rate)
        with temp_path.open("rb") as reader:
            os.fsync(reader.fileno())
        os.replace(temp_path, path)
        parent_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except Exception:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


def prerender_tts(
    spec: dict[str, Any],
    audio_dir: Path,
    skip_tts: bool,
) -> dict[str, dict[str, Any]]:
    settings = spec["settings"]
    target_duration = float(spec["meta"]["target_duration_seconds"])
    step_audio: dict[str, dict[str, Any]] = {}

    kokoro: Kokoro | None = None
    if not skip_tts:
        log("Phase B: loading Kokoro model")
        kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)

    total_narration = 0.0
    for idx, step in enumerate(spec["steps"], start=1):
        step_id = str(step["id"])
        wav_path = audio_dir / f"step-{step_id}.wav"
        narration = str(step["narration"]).strip()
        if not narration:
            raise TourError(f"Step {step_id} has empty narration")

        if skip_tts:
            if not wav_path.exists():
                raise TourError(
                    f"--skip-tts requested but missing audio file: {wav_path}"
                )
            data, sample_rate = sf.read(str(wav_path), always_2d=False)
            sample_count = data.shape[0] if hasattr(data, "shape") else len(data)
            duration = float(sample_count) / float(sample_rate)
            log(
                f"Phase B: [{idx}/{len(spec['steps'])}] reused {wav_path.name} ({duration:.2f}s)"
            )
        else:
            assert kokoro is not None
            samples, sample_rate = kokoro.create(
                narration,
                voice=str(settings["voice"]),
                speed=float(settings["speech_speed"]),
                lang=str(settings["language"]),
            )
            write_wav_atomic(wav_path, samples, sample_rate)
            duration = float(len(samples)) / float(sample_rate)
            log(
                f"Phase B: [{idx}/{len(spec['steps'])}] generated {wav_path.name} ({duration:.2f}s)"
            )

        step_audio[step_id] = {"path": wav_path, "duration": duration}
        total_narration += duration

    if total_narration > target_duration:
        raise TourError(
            "Total narration duration exceeds target_duration_seconds "
            f"({total_narration:.2f}s > {target_duration:.2f}s)"
        )

    log(
        f"Phase B: total narration {total_narration:.2f}s / target {target_duration:.2f}s"
    )
    return step_audio


def run_assertions(
    page: Any, assertions: list[dict[str, Any]], timeout_ms: int
) -> None:
    for assertion in assertions:
        a_type = assertion.get("type")
        value = assertion.get("value")
        if a_type == "url_contains":
            page.wait_for_url(f"**{value}**", timeout=timeout_ms)
        elif a_type == "title_contains":
            deadline = time.time() + (timeout_ms / 1000.0)
            while time.time() < deadline:
                title = page.title()
                if str(value).lower() in title.lower():
                    break
                time.sleep(0.2)
            else:
                raise TourError(f"title does not contain '{value}'")
        elif a_type == "element_visible":
            locator = page.locator(str(value)).first
            locator.wait_for(state="visible", timeout=timeout_ms)
        else:
            raise TourError(f"Unknown assertion type: {a_type}")


def smooth_scroll(page: Any, target: Any, speed: str = "medium") -> None:
    speed_map = {"slow": 1600, "medium": 1100, "fast": 700}
    duration_ms = speed_map.get(str(speed).lower(), 1100)

    if isinstance(target, str) and target.lower() == "bottom":
        page.evaluate(
            """
            () => {
                const delta = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
                window.scrollBy({ top: delta, behavior: 'smooth' });
            }
            """
        )
    elif isinstance(target, str) and target.lower() == "top":
        page.evaluate("() => window.scrollTo({ top: 0, behavior: 'smooth' })")
    else:
        top_value = int(target)
        page.evaluate(
            """
            (y) => {
                const current = window.scrollY || window.pageYOffset || 0;
                const delta = y - current;
                window.scrollBy({ top: delta, behavior: 'smooth' });
            }
            """,
            top_value,
        )
    page.wait_for_timeout(duration_ms)


def dismiss_popups(page: Any, timeout_ms: int) -> int:
    clicked = 0
    for selector in POPUP_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            locator.click(timeout=min(1200, timeout_ms), force=True)
            clicked += 1
            page.wait_for_timeout(200)
        except PlaywrightError:
            continue
    return clicked


def execute_actions(page: Any, step: dict[str, Any], timeout_ms: int) -> None:
    actions = step.get("actions", [])
    for action in actions:
        a_type = action.get("type")
        if a_type == "wait_for_load":
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=min(15000, timeout_ms))
            except PlaywrightError:
                log(
                    f"Step {step['id']}: networkidle timed out, continuing with domcontentloaded"
                )
        elif a_type == "dismiss_popups":
            count = dismiss_popups(page, timeout_ms)
            if count:
                log(f"Step {step['id']}: dismissed {count} popup element(s)")
        elif a_type == "pause":
            duration = float(action.get("duration", 1))
            page.wait_for_timeout(int(duration * 1000))
        elif a_type == "wait_for_hidden":
            selector = str(action.get("selector", ""))
            if selector:
                try:
                    page.locator(selector).first.wait_for(
                        state="hidden", timeout=min(10000, timeout_ms)
                    )
                    log(f"Step {step['id']}: preloader '{selector}' hidden")
                except PlaywrightError:
                    log(
                        f"Step {step['id']}: preloader '{selector}' wait timed out, continuing"
                    )
        elif a_type == "scroll":
            smooth_scroll(
                page, action.get("to", "bottom"), action.get("speed", "medium")
            )
            pause_bottom = float(action.get("pause_at_bottom", 0))
            if pause_bottom > 0:
                page.wait_for_timeout(int(pause_bottom * 1000))
        elif a_type == "type_text":
            text = str(action.get("text", ""))
            delay = int(action.get("delay", 50))
            log(
                f"Step {step['id']}: typing text ({len(text)} chars) with {delay}ms delay"
            )
            try:
                page.keyboard.type(text, delay=delay)
            except PlaywrightError as exc:
                log(f"Step {step['id']}: type_text failed, continuing: {exc}")
        elif a_type == "press_key":
            key = str(action.get("key", "")).strip()
            if key:
                log(f"Step {step['id']}: pressing key '{key}'")
                try:
                    page.keyboard.press(key)
                except PlaywrightError as exc:
                    log(f"Step {step['id']}: press_key failed, continuing: {exc}")
            else:
                log(f"Step {step['id']}: press_key missing key, skipping")
        elif a_type == "click_selector":
            selector = str(action.get("selector", "")).strip()
            click_timeout = action.get("timeout")
            if selector:
                timeout_for_click = (
                    int(click_timeout) if click_timeout is not None else timeout_ms
                )
                log(
                    f"Step {step['id']}: clicking selector '{selector}' "
                    f"(timeout={timeout_for_click}ms)"
                )
                try:
                    page.locator(selector).first.click(timeout=timeout_for_click)
                except PlaywrightError as exc:
                    log(f"Step {step['id']}: click_selector failed, continuing: {exc}")
            else:
                log(f"Step {step['id']}: click_selector missing selector, skipping")
        elif a_type == "focus_editor":
            log(f"Step {step['id']}: focusing editor area")
            try:
                page.locator(".monaco-editor .view-lines").first.click()
            except PlaywrightError as exc:
                log(f"Step {step['id']}: focus_editor failed, continuing: {exc}")
        elif a_type == "command_palette":
            command = str(action.get("command", ""))
            log(f"Step {step['id']}: opening command palette and typing '{command}'")
            try:
                page.keyboard.press("Control+Shift+p")
                page.wait_for_timeout(500)
                if command:
                    page.keyboard.type(command, delay=30)
                page.keyboard.press("Enter")
            except PlaywrightError as exc:
                log(f"Step {step['id']}: command_palette failed, continuing: {exc}")
        elif a_type == "terminal_type":
            text = str(action.get("text", ""))
            press_enter = bool(action.get("press_enter", True))
            log(
                f"Step {step['id']}: typing terminal command '{text}' "
                f"(press_enter={press_enter})"
            )
            try:
                # Use JavaScript to directly focus the active terminal's textarea.
                # Playwright .click() fails because editor folding icons intercept
                # pointer events. page.evaluate bypasses all actionability checks.
                focused = page.evaluate("""() => {
                    // Find the active terminal wrapper
                    const active = document.querySelector('.terminal-wrapper.active');
                    if (!active) return 'no-active-wrapper';
                    // Find xterm textarea inside the active terminal
                    const ta = active.querySelector('textarea.xterm-helper-textarea');
                    if (!ta) return 'no-textarea';
                    ta.focus();
                    return 'focused';
                }""")
                if focused != "focused":
                    log(
                        f"Step {step['id']}: JS terminal focus returned: {focused}, trying fallback"
                    )
                    # Fallback: click the panel area with force
                    page.locator(".terminal-wrapper.active").first.click(force=True)
                page.wait_for_timeout(300)
                page.keyboard.type(text, delay=0)
                if press_enter:
                    page.keyboard.press("Enter")
            except PlaywrightError as exc:
                log(f"Step {step['id']}: terminal_type failed, continuing: {exc}")
        elif a_type == "wait_for_selector":
            selector = str(action.get("selector", "")).strip()
            state = str(action.get("state", "visible"))
            wait_timeout = action.get("timeout")
            if selector:
                timeout_for_wait = (
                    int(wait_timeout) if wait_timeout is not None else timeout_ms
                )
                log(
                    f"Step {step['id']}: waiting for selector '{selector}' "
                    f"state='{state}' (timeout={timeout_for_wait}ms)"
                )
                try:
                    page.locator(selector).first.wait_for(
                        state=state, timeout=timeout_for_wait
                    )
                except PlaywrightError as exc:
                    log(
                        f"Step {step['id']}: wait_for_selector failed, continuing: {exc}"
                    )
            else:
                log(f"Step {step['id']}: wait_for_selector missing selector, skipping")
        elif a_type == "select_all_and_delete":
            log(f"Step {step['id']}: selecting all and deleting")
            try:
                page.keyboard.press("Control+a")
                page.keyboard.press("Backspace")
            except PlaywrightError as exc:
                log(
                    f"Step {step['id']}: select_all_and_delete failed, continuing: {exc}"
                )
        elif a_type == "highlight_lines":
            from_line = int(action.get("from_line", 1))
            to_line = int(action.get("to_line", from_line))
            down_count = max(0, to_line - from_line)
            log(f"Step {step['id']}: highlighting lines {from_line} to {to_line}")
            try:
                page.keyboard.press("Control+g")
                page.wait_for_timeout(120)
                page.keyboard.type(str(from_line), delay=0)
                page.keyboard.press("Enter")
                page.wait_for_timeout(120)
                for _ in range(down_count):
                    page.keyboard.press("Shift+ArrowDown")
            except PlaywrightError as exc:
                log(f"Step {step['id']}: highlight_lines failed, continuing: {exc}")
        elif a_type == "hide_secondary_sidebar":
            log(f"Step {step['id']}: hiding secondary sidebar (auxiliary bar)")
            try:
                page.evaluate(
                    """() => {
                        const aux = document.getElementById('workbench.parts.auxiliarybar');
                        if (aux) aux.remove();
                        document.querySelectorAll('.auxiliarybar').forEach(el => el.remove());
                        window.dispatchEvent(new Event('resize'));
                    }"""
                )
                page.wait_for_timeout(500)
            except PlaywrightError as exc:
                log(f"Step {step['id']}: hide_secondary_sidebar failed: {exc}")
        else:
            raise TourError(f"Unknown action type: {a_type}")

    if "scroll" in step:
        s = step["scroll"]
        smooth_scroll(page, s.get("to", "bottom"), s.get("speed", "medium"))
        pause_bottom = float(s.get("pause_at_bottom", 0))
        if pause_bottom > 0:
            page.wait_for_timeout(int(pause_bottom * 1000))


def capture_step_video(
    browser: Any,
    step: dict[str, Any],
    audio_duration: float,
    settings: dict[str, Any],
    dirs: dict[str, Path],
    attempt: int,
) -> Path:
    timeout_ms = int(float(settings["default_step_timeout"]) * 1000)
    context = None
    page = None
    video_obj = None
    step_id = str(step["id"])
    destination = dirs["clips"] / f"step-{step_id}.webm"

    try:
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(dirs["clips"]),
            record_video_size={"width": 1920, "height": 1080},
            java_script_enabled=True,
        )
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        page.set_default_navigation_timeout(timeout_ms)

        log(f"Phase C: step {step_id} attempt {attempt} navigating to {step['url']}")
        page.goto(str(step["url"]), wait_until="domcontentloaded", timeout=timeout_ms)

        run_assertions(page, step.get("assertions", []), timeout_ms)
        execute_actions(page, step, timeout_ms)

        hold_seconds = audio_duration + 1.4
        page.wait_for_timeout(int(hold_seconds * 1000))

        video_obj = page.video
        if context is not None:
            context.close()

        if video_obj is None:
            raise TourError(f"No video handle created for step {step_id}")

        try:
            video_obj.save_as(str(destination))
        except PlaywrightError as save_error:
            log(
                f"Phase C: save_as after context close failed for step {step_id}: {save_error}"
            )
            source_path = Path(video_obj.path())
            if not source_path.exists():
                raise TourError(
                    f"Video file unavailable for step {step_id}"
                ) from save_error
            shutil.copy2(source_path, destination)

        if not destination.exists() or destination.stat().st_size == 0:
            raise TourError(f"Recorded clip missing or empty for step {step_id}")
        return destination

    except Exception:
        if page is not None:
            screenshot = dirs["screenshots"] / f"step-{step_id}-attempt-{attempt}.png"
            try:
                page.screenshot(path=str(screenshot), full_page=True)
            except Exception:
                pass
        raise

    finally:
        if page is not None:
            try:
                page.close()
            except PlaywrightError:
                pass
        if context is not None:
            try:
                context.close()
            except PlaywrightError:
                pass


def run_capture_phase(
    spec: dict[str, Any],
    step_audio: dict[str, dict[str, Any]],
    dirs: dict[str, Path],
    dry_run: bool,
) -> list[StepResult]:
    if dry_run:
        log("Phase C: dry-run enabled, skipping browser capture")
        return [
            StepResult(
                step_id=str(step["id"]),
                attempt_count=0,
                success=True,
                clip_path=None,
                audio_path=step_audio[str(step["id"])]["path"],
                audio_duration=float(step_audio[str(step["id"])]["duration"]),
                step_elapsed=0.0,
            )
            for step in spec["steps"]
        ]

    results: list[StepResult] = []
    max_retries = int(spec["settings"]["max_retries_per_step"])
    browser_name = str(spec["settings"].get("browser", "chromium")).lower()
    if browser_name != "chromium":
        raise TourError("This pipeline currently supports browser=chromium only")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for step in spec["steps"]:
                step_id = str(step["id"])
                audio_info = step_audio[step_id]
                audio_duration = float(audio_info["duration"])
                attempts = 0
                step_start = time.time()
                last_error: Exception | None = None

                while attempts <= max_retries:
                    attempts += 1
                    try:
                        clip_path = capture_step_video(
                            browser=browser,
                            step=step,
                            audio_duration=audio_duration,
                            settings=spec["settings"],
                            dirs=dirs,
                            attempt=attempts,
                        )
                        elapsed = time.time() - step_start
                        log(
                            f"Phase C: step {step_id} succeeded in {elapsed:.2f}s after {attempts} attempt(s)"
                        )
                        results.append(
                            StepResult(
                                step_id=step_id,
                                attempt_count=attempts,
                                success=True,
                                clip_path=clip_path,
                                audio_path=audio_info["path"],
                                audio_duration=audio_duration,
                                step_elapsed=elapsed,
                            )
                        )
                        break
                    except Exception as exc:
                        last_error = exc
                        log(f"Phase C: step {step_id} failed attempt {attempts}: {exc}")

                        if attempts <= max_retries:
                            log(
                                f"Phase C: retrying step {step_id} ({attempts}/{max_retries})"
                            )
                            if attempts > 1:
                                try:
                                    browser.close()
                                except Exception:
                                    pass
                                browser = playwright.chromium.launch(headless=True)
                        else:
                            elapsed = time.time() - step_start
                            results.append(
                                StepResult(
                                    step_id=step_id,
                                    attempt_count=attempts,
                                    success=False,
                                    clip_path=None,
                                    audio_path=audio_info["path"],
                                    audio_duration=audio_duration,
                                    step_elapsed=elapsed,
                                )
                            )
                            raise TourError(
                                f"Step {step_id} failed after {attempts} attempts: {last_error}"
                            )
        finally:
            try:
                browser.close()
            except Exception:
                pass

    return results


def run_continuous_capture(
    spec: dict[str, Any],
    step_audio: dict[str, dict[str, Any]],
    dirs: dict[str, Path],
    dry_run: bool,
) -> list[StepResult]:
    if dry_run:
        log("Phase C: dry-run enabled, skipping browser capture")
        return [
            StepResult(
                step_id=str(step["id"]),
                attempt_count=0,
                success=True,
                clip_path=None,
                audio_path=step_audio[str(step["id"])]["path"],
                audio_duration=float(step_audio[str(step["id"])]["duration"]),
                step_elapsed=0.0,
            )
            for step in spec["steps"]
        ]

    browser_name = str(spec["settings"].get("browser", "chromium")).lower()
    if browser_name != "chromium":
        raise TourError("This pipeline currently supports browser=chromium only")

    timeout_ms = int(float(spec["settings"]["default_step_timeout"]) * 1000)
    results: list[StepResult] = []
    destination = dirs["clips"] / "continuous.webm"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = None
        page = None
        video_obj = None
        try:
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                record_video_dir=str(dirs["clips"]),
                record_video_size={"width": 1920, "height": 1080},
                java_script_enabled=True,
            )
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.set_default_navigation_timeout(timeout_ms)
            video_obj = page.video

            recording_started = time.time()
            for idx, step in enumerate(spec["steps"]):
                step_id = str(step["id"])
                audio_info = step_audio[step_id]
                audio_duration = float(audio_info["duration"])
                step_wall_start = time.time()
                step_start_offset = step_wall_start - recording_started
                target_url = str(step["url"])
                success = True

                try:
                    current_url = page.url or ""
                    should_navigate = idx == 0
                    if not should_navigate:
                        should_navigate = current_url.rstrip("/") != target_url.rstrip(
                            "/"
                        )

                    if should_navigate:
                        log(
                            f"Phase C: step {step_id} navigating to {target_url} "
                            "(continuous mode)"
                        )
                        page.goto(
                            target_url,
                            wait_until="domcontentloaded",
                            timeout=timeout_ms,
                        )
                    else:
                        log(
                            f"Phase C: step {step_id} reusing current page "
                            "without navigation (continuous mode)"
                        )

                    run_assertions(page, step.get("assertions", []), timeout_ms)
                    execute_actions(page, step, timeout_ms)
                except Exception as exc:
                    success = False
                    log(
                        f"Phase C: step {step_id} failed in continuous mode, "
                        f"continuing: {exc}"
                    )
                    screenshot = dirs["screenshots"] / f"step-{step_id}-continuous.png"
                    try:
                        page.screenshot(path=str(screenshot), full_page=True)
                    except Exception:
                        pass

                hold_seconds = audio_duration + 1.0
                page.wait_for_timeout(int(hold_seconds * 1000))
                step_end_offset = time.time() - recording_started
                elapsed = time.time() - step_wall_start

                if success:
                    log(
                        f"Phase C: step {step_id} succeeded in {elapsed:.2f}s "
                        "(continuous mode)"
                    )
                else:
                    log(
                        f"Phase C: step {step_id} recorded with failures in {elapsed:.2f}s "
                        "(continuous mode)"
                    )

                results.append(
                    StepResult(
                        step_id=step_id,
                        attempt_count=1,
                        success=success,
                        clip_path=None,
                        audio_path=audio_info["path"],
                        audio_duration=audio_duration,
                        step_elapsed=elapsed,
                        video_offset=step_start_offset,
                        step_end_offset=step_end_offset,
                    )
                )

            if page is not None:
                page.close()
                page = None
            if context is not None:
                context.close()
                context = None

            if video_obj is None:
                raise TourError("No video handle created for continuous capture")

            try:
                video_obj.save_as(str(destination))
            except PlaywrightError as save_error:
                log(
                    f"Phase C: save_as after context close failed in continuous mode: {save_error}"
                )
                source_path = Path(video_obj.path())
                if not source_path.exists():
                    raise TourError("Continuous video file unavailable") from save_error
                shutil.copy2(source_path, destination)

            if not destination.exists() or destination.stat().st_size == 0:
                raise TourError("Continuous recorded clip missing or empty")

            if results:
                results[0].clip_path = destination

            return results
        finally:
            if page is not None:
                try:
                    page.close()
                except PlaywrightError:
                    pass
            if context is not None:
                try:
                    context.close()
                except PlaywrightError:
                    pass
            try:
                browser.close()
            except Exception:
                pass


def transcode_step_clip(webm_path: Path, output_mp4_path: Path) -> None:
    cmd = [
        FFMPEG_BIN,
        "-y",
        "-i",
        str(webm_path),
        "-an",
        "-vf",
        "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        str(output_mp4_path),
    ]
    run_cmd(cmd, f"Normalize clip {webm_path.name}")


def normalize_overlay_clip(input_path: Path, output_path: Path) -> None:
    """Normalize an intro/outro MP4 overlay to match the recording format.

    Overlays are pre-rendered by Remotion at 1920x1080. We normalize to ensure
    consistent codec, framerate, and pixel format for ffmpeg concat.
    Silent audio track is added so concat works with audio-containing main video.
    """
    cmd = [
        FFMPEG_BIN,
        "-y",
        "-i",
        str(input_path),
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=24000:cl=mono",
        "-vf",
        "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-ac",
        "1",
        "-ar",
        "24000",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    run_cmd(cmd, f"Normalize overlay {input_path.name}")


def apply_overlays(
    spec: dict[str, Any],
    main_video: Path,
    assembly_dir: Path,
) -> Path:
    """Prepend intro and/or append outro overlay clips to the main video.

    Reads intro_clip and outro_clip paths from spec['output'].
    If neither is set, returns main_video unchanged.
    """
    intro_raw = spec["output"].get("intro_clip")
    outro_raw = spec["output"].get("outro_clip")

    if not intro_raw and not outro_raw:
        return main_video

    clips_to_concat: list[Path] = []

    if intro_raw:
        intro_path = Path(os.path.expanduser(str(intro_raw))).resolve()
        if not intro_path.exists():
            raise TourError(f"Intro overlay clip not found: {intro_path}")
        intro_normalized = assembly_dir / "intro-normalized.mp4"
        normalize_overlay_clip(intro_path, intro_normalized)
        clips_to_concat.append(intro_normalized)
        log(f"Phase D: intro overlay added ({ffprobe_duration(intro_normalized):.2f}s)")

    # Normalize main video to match overlay format for reliable concat
    main_normalized = assembly_dir / "main-for-concat.mp4"
    cmd_norm = [
        FFMPEG_BIN,
        "-y",
        "-i",
        str(main_video),
        "-vf",
        "fps=30,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-ac",
        "1",
        "-ar",
        "24000",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(main_normalized),
    ]
    run_cmd(cmd_norm, "Normalize main video for overlay concat")
    clips_to_concat.append(main_normalized)

    if outro_raw:
        outro_path = Path(os.path.expanduser(str(outro_raw))).resolve()
        if not outro_path.exists():
            raise TourError(f"Outro overlay clip not found: {outro_path}")
        outro_normalized = assembly_dir / "outro-normalized.mp4"
        normalize_overlay_clip(outro_path, outro_normalized)
        clips_to_concat.append(outro_normalized)
        log(f"Phase D: outro overlay added ({ffprobe_duration(outro_normalized):.2f}s)")

    if len(clips_to_concat) == 1:
        return main_video

    concat_list = assembly_dir / "overlay-concat.txt"
    with concat_list.open("w", encoding="utf-8") as handle:
        for clip in clips_to_concat:
            handle.write(f"file '{clip.as_posix()}'\n")

    final_with_overlays = (
        main_video.parent / f"{main_video.stem}-with-overlays{main_video.suffix}"
    )
    cmd = [
        FFMPEG_BIN,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(final_with_overlays),
    ]
    run_cmd(cmd, "Concatenate overlays with main video")

    # Replace the original output with the overlay version
    os.replace(final_with_overlays, main_video)
    log(f"Phase D: final video with overlays ({ffprobe_duration(main_video):.2f}s)")
    return main_video


def mux_audio_with_offset(
    video_mp4: Path, audio_wav: Path, output_mp4: Path, audio_duration: float
) -> None:
    base_duration = ffprobe_duration(video_mp4)
    required_duration = audio_duration + 1.0
    extra = max(0.0, required_duration - base_duration)
    filter_parts: list[str] = []
    if extra > 0:
        filter_parts.append(f"[0:v]tpad=stop_mode=clone:stop_duration={extra:.3f}[v0]")
        video_input = "[v0]"
    else:
        video_input = "[0:v]"
    filter_parts.append(f"{video_input}setpts=PTS-STARTPTS[v]")
    filter_parts.append("[1:a]adelay=1000|1000,asetpts=PTS-STARTPTS[a]")

    cmd = [
        FFMPEG_BIN,
        "-y",
        "-i",
        str(video_mp4),
        "-i",
        str(audio_wav),
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-shortest",
        str(output_mp4),
    ]
    run_cmd(cmd, f"Mux narration for {video_mp4.name}")


def concat_step_clips(
    step_mp4s: list[Path],
    assembly_dir: Path,
    final_output: Path,
    target_duration: float,
    use_loudnorm: bool,
) -> None:
    concat_list = assembly_dir / "concat.txt"
    with concat_list.open("w", encoding="utf-8") as handle:
        for clip in step_mp4s:
            handle.write(f"file '{clip.as_posix()}'\n")

    cmd = [
        FFMPEG_BIN,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
    ]

    if use_loudnorm:
        cmd.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"])

    cmd.extend(["-t", f"{target_duration:.3f}", str(final_output)])
    run_cmd(cmd, "Concatenate final tour video")


def assemble_video(
    spec: dict[str, Any],
    results: list[StepResult],
    dirs: dict[str, Path],
) -> Path:
    log("Phase D: assembling clips with ffmpeg")
    assembly_dir = dirs["assembly"]
    per_step_muxed: list[Path] = []

    for result in results:
        if not result.success or result.clip_path is None:
            raise TourError(f"Cannot assemble failed step {result.step_id}")

        normalized = assembly_dir / f"step-{result.step_id}-normalized.mp4"
        muxed = assembly_dir / f"step-{result.step_id}-muxed.mp4"

        transcode_step_clip(result.clip_path, normalized)
        mux_audio_with_offset(
            normalized, result.audio_path, muxed, result.audio_duration
        )
        per_step_muxed.append(muxed)

    final_path = Path(os.path.expanduser(str(spec["output"]["path"]))).resolve()
    final_path.parent.mkdir(parents=True, exist_ok=True)
    concat_step_clips(
        step_mp4s=per_step_muxed,
        assembly_dir=assembly_dir,
        final_output=final_path,
        target_duration=float(spec["meta"]["target_duration_seconds"]),
        use_loudnorm=bool(spec["output"].get("loudnorm", True)),
    )

    for result in results:
        if result.clip_path and result.clip_path.exists():
            result.clip_path.unlink(missing_ok=True)

    return final_path


def assemble_continuous_video(
    spec: dict[str, Any],
    results: list[StepResult],
    dirs: dict[str, Path],
) -> Path:
    log("Phase D: assembling continuous capture with ffmpeg")
    if not results:
        raise TourError("No steps available to assemble")

    source_clip = next((r.clip_path for r in results if r.clip_path is not None), None)
    if source_clip is None:
        raise TourError("Missing continuous capture clip for assembly")

    assembly_dir = dirs["assembly"]
    normalized = assembly_dir / "continuous-normalized.mp4"
    transcode_step_clip(source_clip, normalized)

    filter_parts: list[str] = []
    delayed_labels: list[str] = []
    cmd: list[str] = [FFMPEG_BIN, "-y", "-i", str(normalized)]

    for index, result in enumerate(results, start=1):
        cmd.extend(["-i", str(result.audio_path)])
        delay_ms = int(round((result.video_offset + 1.0) * 1000))
        label = f"a{index}"
        filter_parts.append(f"[{index}:a]adelay={delay_ms}|{delay_ms}[{label}]")
        delayed_labels.append(f"[{label}]")

    mix_inputs = "".join(delayed_labels)
    filter_parts.append(
        f"{mix_inputs}amix=inputs={len(delayed_labels)}:duration=longest[amixed]"
    )
    if bool(spec["output"].get("loudnorm", True)):
        filter_parts.append("[amixed]loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
    else:
        filter_parts.append("[amixed]anull[aout]")

    final_path = Path(os.path.expanduser(str(spec["output"]["path"]))).resolve()
    final_path.parent.mkdir(parents=True, exist_ok=True)

    cmd.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-shortest",
            "-t",
            f"{float(spec['meta']['max_duration_seconds']):.3f}",
            str(final_path),
        ]
    )

    run_cmd(cmd, "Assemble continuous tour video")

    if source_clip.exists():
        source_clip.unlink(missing_ok=True)

    return final_path


def print_report(
    spec: dict[str, Any],
    results: list[StepResult],
    final_path: Path | None,
    started_at: float,
) -> None:
    log("Phase E: tour report")
    total_retries = 0
    for result in results:
        retries = max(0, result.attempt_count - 1)
        total_retries += retries
        status = "OK" if result.success else "FAILED"
        log(
            f"Step {result.step_id}: status={status}, attempts={result.attempt_count}, "
            f"audio={result.audio_duration:.2f}s, elapsed={result.step_elapsed:.2f}s"
        )

    elapsed = time.time() - started_at
    log(f"Retries total: {total_retries}")
    log(f"Wall-clock time: {elapsed:.2f}s")

    if final_path and final_path.exists():
        size_mb = final_path.stat().st_size / (1024 * 1024)
        duration = ffprobe_duration(final_path)
        log(f"Output: {final_path}")
        log(f"Output duration: {duration:.2f}s")
        log(f"Output size: {size_mb:.2f} MB")
        target = float(spec["meta"]["target_duration_seconds"])
        maximum = float(spec["meta"]["max_duration_seconds"])
        if duration > maximum:
            raise TourError(
                f"Final duration exceeds max_duration_seconds ({duration:.2f}s > {maximum:.2f}s)"
            )
        if duration > target:
            log(
                f"Output exceeds target ({target:.2f}s) but remains within max duration"
            )


def _dispatch_colab_tts(
    spec: dict[str, Any],
    audio_dir: Path,
    args: argparse.Namespace,
) -> dict[str, dict[str, Any]]:
    """Dispatch TTS generation to Google Colab via Drive sync."""
    # Import here to avoid hard dependency when using local backend
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from colab.colab_dispatcher import create_dispatcher_from_args

    log("Phase B: dispatching TTS to Colab GPU worker")
    dispatcher = create_dispatcher_from_args(
        drive_path=getattr(args, "colab_drive_path", None),
        timeout=getattr(args, "colab_timeout", 600.0),
    )
    return dispatcher.dispatch_and_wait(spec, audio_dir)


def _dispatch_colab_nvenc_encode(
    args: argparse.Namespace,
    input_files: dict[str, Path],
    operations: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Path]:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from colab.colab_dispatcher import create_nvenc_dispatcher_from_args

    log("Phase D: dispatching encoding to Colab T4 NVENC worker")
    dispatcher = create_nvenc_dispatcher_from_args(
        drive_path=getattr(args, "nvenc_drive_path", None),
        timeout=getattr(args, "nvenc_timeout", 1200.0),
    )
    return dispatcher.dispatch_encode_job(
        operations=operations,
        input_files=input_files,
        output_dir=output_dir,
    )


def _maybe_nvenc_reencode(
    video_path: Path,
    args: argparse.Namespace,
    assembly_dir: Path,
) -> Path:
    if getattr(args, "encode_backend", "local") != "colab-nvenc":
        return video_path

    log("Phase D: re-encoding with Colab T4 NVENC")

    input_name = video_path.name
    output_name = f"{video_path.stem}-nvenc{video_path.suffix}"
    operations = [
        {
            "type": "transcode",
            "input": input_name,
            "output": output_name,
        }
    ]

    results = _dispatch_colab_nvenc_encode(
        args=args,
        input_files={input_name: video_path},
        operations=operations,
        output_dir=assembly_dir,
    )

    nvenc_path = results.get(output_name)
    if nvenc_path and nvenc_path.exists():
        os.replace(nvenc_path, video_path)
        log(f"Phase D: NVENC re-encode complete ({ffprobe_duration(video_path):.2f}s)")
    else:
        log("Phase D: NVENC re-encode failed, keeping local encode")

    return video_path


# ── Dynamic zoom/pan (virtual camera) for mobile-friendly output ──────────

# Focus presets: (center_x, center_y, zoom) for VS Code layout at 1920x1080
# Coordinates assume sidebar hidden, terminal open at bottom ~30%.
FOCUS_PRESETS: dict[str, tuple[float, float, float]] = {
    "editor": (780.0, 400.0, 2.2),    # Editor text area (center of code region)
    "terminal": (960.0, 870.0, 2.4),  # Terminal panel (bottom section)
    "full": (960.0, 540.0, 1.0),      # Full view (no zoom)
}

# Default transition duration in milliseconds between camera states
ZOOM_TRANSITION_MS = 600
# Hold at full view briefly during navigation actions (ms)
ZOOM_NAV_HOLD_MS = 800


@dataclass
class CameraKeyframe:
    """A point on the virtual camera timeline."""
    time: float          # seconds into the video
    cx: float            # center X in source pixels (0..1920)
    cy: float            # center Y in source pixels (0..1080)
    zoom: float          # zoom factor (1.0 = full, 2.2 = editor crop)
    transition_ms: int   # ease-in-out duration to reach this state


def _action_focus(action: dict[str, Any]) -> str:
    """Map a single action to a focus region name."""
    atype = action.get("type", "")
    if atype in ("type_text", "focus_editor", "highlight_lines", "select_all_and_delete"):
        return "editor"
    if atype == "terminal_type":
        return "terminal"
    if atype in ("command_palette", "dismiss_popups", "wait_for_load",
                 "wait_for_selector", "hide_secondary_sidebar"):
        return "full"
    # press_key / pause / other — inherit previous focus
    return ""


def _dominant_focus(actions: list[dict[str, Any]]) -> str:
    """Determine dominant focus region for a step from its actions."""
    counts: dict[str, int] = {}
    for action in actions:
        focus = _action_focus(action)
        if focus:
            counts[focus] = counts.get(focus, 0) + 1
    if not counts:
        return "editor"
    # Prefer terminal if any terminal_type action exists, otherwise majority
    if "terminal" in counts:
        return "terminal"
    return max(counts, key=lambda k: counts[k])


def build_camera_path(
    spec: dict[str, Any],
    results: list["StepResult"],
    total_duration: float,
) -> list[CameraKeyframe]:
    """Generate a camera keyframe timeline from step actions and offsets.

    Auto-derives focus regions from action types with smooth transitions.
    Supports optional per-step overrides via spec step 'zoom' field.
    """
    steps = spec.get("steps") or spec.get("segments", [])
    keyframes: list[CameraKeyframe] = []

    # Start at full view
    keyframes.append(CameraKeyframe(
        time=0.0, cx=960.0, cy=540.0, zoom=1.0, transition_ms=0
    ))

    prev_focus = "full"
    for result in results:
        # Find matching step definition
        step_def = None
        for s in steps:
            if s["id"] == result.step_id:
                step_def = s
                break

        if step_def is None:
            continue

        step_start = result.video_offset
        step_end = result.step_end_offset or (step_start + result.audio_duration + 1.0)

        # Check for explicit per-step zoom override
        zoom_override = step_def.get("zoom")
        if zoom_override:
            focus_name = zoom_override.get("focus", "editor")
            zoom_level = zoom_override.get("z")
            cx_override = zoom_override.get("cx")
            cy_override = zoom_override.get("cy")
            transition = zoom_override.get("transition_ms", ZOOM_TRANSITION_MS)
            preset = FOCUS_PRESETS.get(focus_name, FOCUS_PRESETS["editor"])
            cx = cx_override if cx_override is not None else preset[0]
            cy = cy_override if cy_override is not None else preset[1]
            z = zoom_level if zoom_level is not None else preset[2]
        else:
            # Auto-derive from actions
            actions = step_def.get("actions", [])
            focus_name = _dominant_focus(actions)
            preset = FOCUS_PRESETS[focus_name]
            cx, cy, z = preset
            transition = ZOOM_TRANSITION_MS

        # If focus changed, insert brief full-view hold at transitions
        if focus_name != prev_focus and prev_focus != "full" and focus_name != "full":
            # Zoom out to full briefly, then zoom into new region
            full_preset = FOCUS_PRESETS["full"]
            keyframes.append(CameraKeyframe(
                time=step_start,
                cx=full_preset[0], cy=full_preset[1], zoom=full_preset[2],
                transition_ms=transition,
            ))
            keyframes.append(CameraKeyframe(
                time=step_start + ZOOM_NAV_HOLD_MS / 1000.0,
                cx=cx, cy=cy, zoom=z,
                transition_ms=transition,
            ))
        else:
            keyframes.append(CameraKeyframe(
                time=step_start,
                cx=cx, cy=cy, zoom=z,
                transition_ms=transition,
            ))

        prev_focus = focus_name

    # End at full view for clean finish
    if total_duration > 0:
        keyframes.append(CameraKeyframe(
            time=max(0.0, total_duration - 2.0),
            cx=960.0, cy=540.0, zoom=1.0,
            transition_ms=ZOOM_TRANSITION_MS,
        ))

    return keyframes



def _build_zoom_filter_complex(
    keyframes: list[CameraKeyframe],
    fps: int = 30,
    src_w: int = 1920,
    src_h: int = 1080,
) -> str:
    """Build FFmpeg filter_complex for segment-based zoom.

    Strategy: split the video into segments at each keyframe, apply a static
    crop+scale per segment, and concat them back.  Transition segments get a
    linear interpolation of crop parameters from the previous state to the
    current state using the 'n' frame counter.  This is orders of magnitude
    faster than zoompan because crop+scale is a trivial per-frame operation.
    """
    if not keyframes or len(keyframes) < 2:
        return ""

    parts: list[str] = []
    seg_labels: list[str] = []

    for i in range(1, len(keyframes)):
        prev = keyframes[i - 1]
        curr = keyframes[i]
        t0 = prev.time
        t1 = curr.time
        seg_dur = t1 - t0
        if seg_dur <= 0:
            continue

        trans_dur = min(curr.transition_ms / 1000.0, seg_dur)
        trans_frames = max(1, int(trans_dur * fps))
        seg_frames = max(1, int(seg_dur * fps))

        # Trim this segment from the input
        seg_label = f"seg{i}"
        aseg_label = f"aseg{i}"
        parts.append(
            f"[0:v]trim=start={t0:.4f}:end={t1:.4f},setpts=PTS-STARTPTS[{seg_label}_raw]"
        )
        parts.append(
            f"[0:a]atrim=start={t0:.4f}:end={t1:.4f},asetpts=PTS-STARTPTS[{aseg_label}]"
        )

        # Compute crop parameters — zoom determines crop window size
        # During the transition region (first trans_frames), interpolate
        # from prev state to curr state.  After that, hold at curr state.
        # crop w/h: floor(src_dim / zoom / 2) * 2  (even dimensions)
        # crop x/y: clip(cx - w/2, 0, src_dim - w)
        #
        # Use 'n' (frame count in the segment, 0-indexed) for interpolation.
        if abs(prev.zoom - curr.zoom) < 0.001 and abs(prev.cx - curr.cx) < 1 and abs(prev.cy - curr.cy) < 1:
            # Static segment — no interpolation needed (fastest path)
            w = max(2, int(src_w / curr.zoom) // 2 * 2)
            h = max(2, int(src_h / curr.zoom) // 2 * 2)
            x = max(0, min(int(curr.cx - w / 2), src_w - w))
            y = max(0, min(int(curr.cy - h / 2), src_h - h))
            parts.append(
                f"[{seg_label}_raw]crop={w}:{h}:{x}:{y},"
                f"scale={src_w}:{src_h}:flags=lanczos,setsar=1[{seg_label}]"
            )
        else:
            # Animated segment — smoothstep interpolation via 'n' frame counter
            # Commas inside expressions must be escaped as \, in filter_complex
            # p = clip(n / trans_frames, 0, 1)
            p = f"clip(n/{trans_frames}\\,0\\,1)"
            # Smoothstep: s = p*p*(3-2*p)
            s = f"({p}*{p}*(3-2*{p}))"

            z_expr = f"{prev.zoom:.3f}+{curr.zoom - prev.zoom:.3f}*{s}"
            cx_expr = f"{prev.cx:.1f}+{curr.cx - prev.cx:.1f}*{s}"
            cy_expr = f"{prev.cy:.1f}+{curr.cy - prev.cy:.1f}*{s}"

            w_expr = f"max(2\\,floor({src_w}/({z_expr})/2)*2)"
            h_expr = f"max(2\\,floor({src_h}/({z_expr})/2)*2)"
            x_expr = f"clip(({cx_expr})-({w_expr})/2\\,0\\,{src_w}-({w_expr}))"
            y_expr = f"clip(({cy_expr})-({h_expr})/2\\,0\\,{src_h}-({h_expr}))"

            parts.append(
                f"[{seg_label}_raw]crop=w='{w_expr}':h='{h_expr}':x='{x_expr}':y='{y_expr}',"
                f"scale={src_w}:{src_h}:flags=lanczos,setsar=1[{seg_label}]"
            )

        seg_labels.append(f"[{seg_label}][{aseg_label}]")

    if not seg_labels:
        return ""

    # Concat all segments back together
    concat_inputs = "".join(seg_labels)
    parts.append(
        f"{concat_inputs}concat=n={len(seg_labels)}:v=1:a=1[vout][aout]"
    )

    return ";".join(parts)


def apply_zoom_pan(
    video_path: Path,
    keyframes: list[CameraKeyframe],
    assembly_dir: Path,
) -> Path:
    """Apply dynamic zoom/pan to assembled video using segment-based crop+scale.

    Splits the video at keyframe boundaries, applies static or interpolated
    crop+scale per segment, then concatenates them back.  Much faster than
    zoompan because crop+scale is a trivial per-frame operation.
    """
    if not keyframes or len(keyframes) < 2:
        log("Phase E: skipping zoom (no keyframes)")
        return video_path

    fc_expr = _build_zoom_filter_complex(keyframes)
    if not fc_expr:
        return video_path

    # Write filter_complex to a script file (can be very long)
    filter_script = assembly_dir / "zoom-filter.txt"
    filter_script.write_text(fc_expr, encoding="utf-8")

    zoomed_path = assembly_dir / f"{video_path.stem}-zoomed{video_path.suffix}"
    cmd = [
        FFMPEG_BIN, "-y",
        "-i", str(video_path),
        "-/filter_complex", str(filter_script),
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(zoomed_path),
    ]
    run_cmd(cmd, "Apply zoom/pan virtual camera")

    # Replace original with zoomed version (may cross filesystem boundaries)
    shutil.copy2(zoomed_path, video_path)
    zoomed_path.unlink(missing_ok=True)
    log(f"Phase E: zoom/pan applied ({ffprobe_duration(video_path):.2f}s)")
    return video_path


def _maybe_apply_zoom(
    video_path: Path,
    spec: dict[str, Any],
    results: list["StepResult"],
    args: argparse.Namespace,
    assembly_dir: Path,
) -> Path:
    """Apply zoom/pan if enabled via --zoom flag."""
    zoom_mode = getattr(args, "zoom", "off")
    if zoom_mode == "off":
        return video_path

    log(f"Phase E: generating camera path (zoom={zoom_mode})")
    total_duration = ffprobe_duration(video_path)
    keyframes = build_camera_path(spec, results, total_duration)

    if zoom_mode == "mobile":
        # Increase zoom levels for smaller screens
        for kf in keyframes:
            if kf.zoom > 1.0:
                kf.zoom = min(kf.zoom * 1.15, 3.0)

    log(f"Phase E: {len(keyframes)} keyframes over {total_duration:.1f}s")
    return apply_zoom_pan(video_path, keyframes, assembly_dir)

def _coerce_audio_paths(step_audio: dict[str, Any]) -> dict[str, Path]:
    audio_paths: dict[str, Path] = {}
    for step_id, audio_info in step_audio.items():
        if isinstance(audio_info, dict):
            audio_value = audio_info.get("path")
        else:
            audio_value = audio_info
        if audio_value is None:
            raise TourError(f"Missing audio path for {step_id}")
        audio_paths[step_id] = Path(audio_value)
    return audio_paths


def _dispatch_colab_f5_tts(
    spec: dict[str, Any],
    audio_dir: Path,
    args: argparse.Namespace,
) -> dict[str, dict[str, Any]]:
    """Dispatch F5-TTS voice cloning generation to Google Colab via Drive sync."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from colab.colab_dispatcher import create_f5_dispatcher_from_args

    log("Phase B: dispatching F5-TTS (voice cloning) to Colab GPU worker")
    dispatcher = create_f5_dispatcher_from_args(
        drive_path=getattr(args, "colab_drive_path", None),
        timeout=getattr(args, "colab_timeout", 600.0),
    )
    return dispatcher.dispatch_and_wait(spec, audio_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Shot-based autonomous narrated website tour recorder"
    )
    parser.add_argument("spec", help="Path to tour spec JSON")
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate and prerender TTS only"
    )
    parser.add_argument(
        "--skip-tts",
        action="store_true",
        help="Reuse existing WAV files in work_dir/audio",
    )
    parser.add_argument(
        "--work-dir",
        help="Use an existing work directory (required for --skip-tts reuse)",
    )
    parser.add_argument(
        "--tts-backend",
        choices=["local", "colab", "colab-f5"],
        default="local",
        help="TTS backend: 'local' (Kokoro CPU), 'colab' (Kokoro GPU), 'colab-f5' (F5-TTS voice cloning GPU)",
    )
    parser.add_argument(
        "--colab-drive-path",
        help="Path to Google Drive sync dir for Colab TTS (default: ~/gdrive/autonomous-recording/tts-jobs)",
    )
    parser.add_argument(
        "--colab-timeout",
        type=float,
        default=600.0,
        help="Max seconds to wait for Colab TTS worker (default: 600)",
    )
    parser.add_argument(
        "--encode-backend",
        choices=["local", "colab-nvenc"],
        default="local",
        help="Video encoding backend: 'local' (libx264 CPU) or 'colab-nvenc' (T4 GPU NVENC)",
    )
    parser.add_argument(
        "--nvenc-drive-path",
        help="Path to Google Drive sync dir for NVENC encoding jobs (default: ~/gdrive/autonomous-recording/encode-jobs)",
    )
    parser.add_argument(
        "--nvenc-timeout",
        type=float,
        default=1200.0,
        help="Max seconds to wait for Colab NVENC worker (default: 1200)",
    )
    parser.add_argument(
        "--zoom",
        choices=["off", "auto", "mobile"],
        default="off",
        help="Dynamic zoom/pan: 'off' (disabled), 'auto' (standard zoom), 'mobile' (stronger zoom for phones)",
    )
    return parser.parse_args()


def main() -> int:
    started = time.time()
    args = parse_args()

    try:
        ensure_tooling()
        spec_path = Path(args.spec).resolve()
        spec = load_tour_spec(spec_path)
        selected_work_dir = (
            Path(args.work_dir).expanduser().resolve() if args.work_dir else None
        )
        if args.skip_tts and selected_work_dir is None:
            raise TourError("--skip-tts requires --work-dir pointing to previous run")
        dirs = create_work_dirs(selected_work_dir)

        log(f"Work directory: {dirs['base']}")
        log("Phase A: spec loaded and validated")

        # Determine workflow: mixed segments or traditional steps
        use_segments = "segments" in spec

        if use_segments:
            # New mixed slide/demo workflow
            log("Phase A: detected segment-based workflow (slides + demos)")

            # Generate/prepare slides if configured
            slides_dir = None
            if spec.get("slides"):
                log("Phase A: preparing slides")
                slides_dir = generate_slides_via_gamma(spec, dirs["base"])

            # Log segments
            for seg in spec["segments"]:
                seg_type = seg.get("type", "demo")
                log(f"Phase A: segment {seg['id']} ({seg_type})")

            run_pre_setup(spec)

            # Prerender TTS for all segments
            if args.tts_backend == "colab":
                step_audio = _coerce_audio_paths(
                    _dispatch_colab_tts(spec, dirs["audio"], args)
                )
            elif args.tts_backend == "colab-f5":
                step_audio = _coerce_audio_paths(
                    _dispatch_colab_f5_tts(spec, dirs["audio"], args)
                )
            else:
                step_audio = prerender_tts_mixed(
                    spec, dirs["audio"], skip_tts=args.skip_tts
                )

            log("Phase C: running mixed capture (slides + demos)")
            results = run_mixed_capture(
                spec, step_audio, dirs, slides_dir, dry_run=args.dry_run
            )

            final_path: Path | None = None
            if not args.dry_run:
                final_path = assemble_mixed_video(spec, results, step_audio, dirs)
                if final_path is not None:
                    final_path = _maybe_apply_zoom(
                        final_path, spec, results, args, dirs["assembly"]
                    )
                if final_path is not None:
                    final_path = _maybe_nvenc_reencode(
                        final_path, args, dirs["assembly"]
                    )
                # Apply intro/outro overlays if configured
                if final_path is not None:
                    final_path = apply_overlays(spec, final_path, dirs["assembly"])
        else:
            # Traditional step-based workflow
            for step in spec["steps"]:
                log(
                    f"Phase A: step {step['id']} budget {step['time_budget_seconds']:.2f}s"
                )

            run_pre_setup(spec)

            if args.tts_backend == "colab":
                step_audio = _dispatch_colab_tts(spec, dirs["audio"], args)
            elif args.tts_backend == "colab-f5":
                step_audio = _dispatch_colab_f5_tts(spec, dirs["audio"], args)
            else:
                step_audio = prerender_tts(spec, dirs["audio"], skip_tts=args.skip_tts)
            mode = str(spec["settings"].get("mode", "independent"))
            if mode == "continuous":
                log("Phase C: running in continuous capture mode")
                results = run_continuous_capture(
                    spec, step_audio, dirs, dry_run=args.dry_run
                )
            else:
                log("Phase C: running in independent capture mode")
                results = run_capture_phase(
                    spec, step_audio, dirs, dry_run=args.dry_run
                )

            final_path: Path | None = None
            if not args.dry_run:
                if mode == "continuous":
                    final_path = assemble_continuous_video(spec, results, dirs)
                else:
                    final_path = assemble_video(spec, results, dirs)
                if final_path is not None:
                    final_path = _maybe_apply_zoom(
                        final_path, spec, results, args, dirs["assembly"]
                    )
                if final_path is not None:
                    final_path = _maybe_nvenc_reencode(
                        final_path, args, dirs["assembly"]
                    )
                # Apply intro/outro overlays if configured
                if final_path is not None:
                    final_path = apply_overlays(spec, final_path, dirs["assembly"])

        print_report(spec, results, final_path, started)
        return 0
    except Exception as exc:
        log(f"ERROR: {exc}")
        return 1


def generate_slides_via_gamma(spec: dict[str, Any], work_dir: Path) -> Path | None:
    """Generate or retrieve cached slides via Gamma API."""
    slides_config = spec.get("slides")
    if not slides_config or not slides_config.get("generate", False):
        return None

    cache_key = slides_config.get("cache_key", "default")
    slides_dir = work_dir / "slides" / cache_key

    # Check if already cached
    if slides_dir.exists() and any(slides_dir.glob("slide-*.png")):
        log(f"[Slides] Using cached slides: {slides_dir}")
        return slides_dir

    slides_dir.mkdir(parents=True, exist_ok=True)

    # Try to use Gamma API if available
    try:
        import importlib.util

        gamma_client_path = Path(__file__).parent / "gamma_client.py"
        spec_obj = importlib.util.spec_from_file_location(
            "tour_recorder_gamma_client", gamma_client_path
        )
        if spec_obj is None or spec_obj.loader is None:
            raise TourError(
                f"Unable to load gamma_client module from {gamma_client_path}"
            )
        gamma_module = importlib.util.module_from_spec(spec_obj)
        spec_obj.loader.exec_module(gamma_module)
        GammaClient = gamma_module.GammaClient

        client = GammaClient()

        content = slides_config.get("content", [])
        theme = slides_config.get("theme", "Chisel")

        result_dir = client.generate_presentation(
            title=spec["meta"]["title"], content=content, theme=theme
        )

        # Copy generated slides to cache directory
        for slide_file in result_dir.glob("slide-*.png"):
            shutil.copy(slide_file, slides_dir / slide_file.name)

        log(f"[Slides] Generated {len(list(slides_dir.glob('slide-*.png')))} slides")
        return slides_dir

    except Exception as e:
        log(f"[Slides] Gamma API unavailable ({e}), creating placeholder slides")
        # Create placeholder text-based slides
        return create_placeholder_slides(slides_config, slides_dir)


def create_placeholder_slides(slides_config: dict[str, Any], slides_dir: Path) -> Path:
    """Create placeholder slide images when Gamma API is unavailable."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        content = slides_config.get("content", [])

        for i, slide in enumerate(content, 1):
            # Create 1920x1080 image with dark background
            img = Image.new("RGB", (1920, 1080), "#0D1117")
            draw = ImageDraw.Draw(img)

            # Title
            title = slide.get("title", "Slide")
            try:
                font_title = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72
                )
                font_body = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 48
                )
            except:
                font_title = ImageFont.load_default()
                font_body = font_title

            # Draw title
            draw.text((960, 200), title, fill="#F7C948", font=font_title, anchor="mt")

            # Draw bullet points
            y = 400
            for point in slide.get("bullet_points", []):
                draw.text((200, y), f"• {point}", fill="#FFFFFF", font=font_body)
                y += 80

            # Save
            num = str(i).zfill(3)
            img.save(slides_dir / f"slide-{num}.png")

        log(f"[Slides] Created {len(content)} placeholder slides")
        return slides_dir

    except ImportError:
        log("[Slides] Pillow not available, cannot create placeholder slides")
        return slides_dir


def record_slide_segment(
    browser, slides_dir: Path, segment: dict[str, Any], audio_path: Path, work_dir: Path
) -> Path:
    """Record a slide segment showing Gamma-generated slides."""
    slide_range = segment.get("slides", {}).get("range", [1, 1])
    advance_interval = segment.get("slides", {}).get("advance_interval", 5000)

    # Build slide viewer URL
    slide_viewer_path = Path(__file__).parent / "slide-viewer.html"
    slide_count = slide_range[1] - slide_range[0] + 1

    viewer_url = (
        f"file://{slide_viewer_path.absolute()}?"
        f"slides={slides_dir.absolute()}&"
        f"count={slide_count}&"
        f"interval={advance_interval}"
    )

    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        record_video_dir=str(work_dir / "clips"),
        record_video_size={"width": 1920, "height": 1080},
    )

    try:
        page = context.new_page()
        page.goto(viewer_url)
        page.wait_for_selector("#slide-image", state="visible", timeout=10000)

        # Navigate to starting slide
        start_slide = slide_range[0]
        page.evaluate(f"window.slideViewer.goToSlide({start_slide})")

        # Start auto-advance
        page.evaluate("window.slideViewer.startAutoAdvance()")

        # Wait for audio duration + padding
        import soundfile as sf

        audio_duration, _ = sf.read(audio_path)
        duration = len(audio_duration) / sf.info(audio_path).samplerate

        page.wait_for_timeout(int(duration * 1000) + 1000)

        context.close()

        # Get video path
        video_path = page.video.path()
        return Path(video_path)

    except Exception as e:
        context.close()
        raise TourError(f"Slide segment recording failed: {e}")


def run_mixed_capture(
    spec: dict[str, Any],
    step_audio: dict[str, Path],
    dirs: dict[str, Path],
    slides_dir: Path | None,
    dry_run: bool = False,
) -> list[StepResult]:
    """Run mixed slide/demo capture for segment-based specs."""
    if dry_run:
        return []

    results: list[StepResult] = []
    segments = spec.get("segments", spec.get("steps", []))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        try:
            for idx, segment in enumerate(segments, 1):
                seg_id = segment.get("id", f"segment-{idx}")
                seg_type = segment.get("type", "demo")
                audio_path = step_audio.get(seg_id)

                if not audio_path or not audio_path.exists():
                    log(f"Segment {seg_id}: missing audio, skipping")
                    continue

                log(f"Segment {idx}/{len(segments)}: {seg_id} ({seg_type})")

                try:
                    if seg_type == "slides" and slides_dir:
                        clip_path = record_slide_segment(
                            browser, slides_dir, segment, audio_path, dirs["base"]
                        )
                    else:
                        # Demo segment - use existing capture logic
                        clip_path = record_demo_segment(
                            browser, segment, audio_path, dirs
                        )

                    results.append(
                        StepResult(
                            step_id=seg_id,
                            attempt_count=1,
                            success=True,
                            clip_path=clip_path,
                            audio_path=audio_path,
                            audio_duration=0.0,
                            step_elapsed=0.0,
                        )
                    )

                except Exception as e:
                    log(f"Segment {seg_id} failed: {e}")
                    results.append(
                        StepResult(
                            step_id=seg_id,
                            attempt_count=1,
                            success=False,
                            clip_path=None,
                            audio_path=audio_path,
                            audio_duration=0.0,
                            step_elapsed=0.0,
                        )
                    )

        finally:
            browser.close()

    return results


def record_demo_segment(
    browser, segment: dict[str, Any], audio_path: Path, dirs: dict[str, Path]
) -> Path:
    """Record a demo segment (existing code-server capture logic)."""
    # Reuse existing continuous capture logic for a single segment
    # This is a simplified version - full implementation would reuse
    # the existing run_continuous_capture logic

    url = segment.get("url", "http://127.0.0.1:8080")
    actions = segment.get("actions", [])

    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        record_video_dir=str(dirs["clips"]),
        record_video_size={"width": 1920, "height": 1080},
    )

    try:
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Run actions
        for action in actions:
            run_action(page, action)

        # Wait for audio to finish
        import soundfile as sf

        audio_duration, _ = sf.read(audio_path)
        duration = len(audio_duration) / sf.info(audio_path).samplerate
        page.wait_for_timeout(int(duration * 1000))

        context.close()

        video_path = page.video.path()
        return Path(video_path)

    except Exception as e:
        context.close()
        raise TourError(f"Demo segment recording failed: {e}")


def run_action(page, action: dict[str, Any]) -> None:
    """Execute a single action on the page."""
    action_type = action.get("type", "pause")

    if action_type == "pause":
        page.wait_for_timeout(int(action.get("duration", 1.0) * 1000))
    elif action_type == "wait_for_load":
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except:
            page.wait_for_timeout(3000)
    elif action_type == "wait_for_selector":
        page.wait_for_selector(
            action["selector"],
            state=action.get("state", "visible"),
            timeout=action.get("timeout", 10000),
        )
    elif action_type == "press_key":
        page.keyboard.press(action["key"])
        page.wait_for_timeout(300)
    elif action_type == "type_text":
        page.keyboard.type(action["text"], delay=action.get("delay", 40))
    elif action_type == "focus_editor":
        page.locator(".monaco-editor .view-lines").first.click()
    elif action_type == "select_all_and_delete":
        page.keyboard.press("Control+a")
        page.wait_for_timeout(200)
        page.keyboard.press("Delete")
    elif action_type == "command_palette":
        page.keyboard.press("Control+Shift+p")
        page.wait_for_timeout(800)
        page.keyboard.type(action["command"], delay=40)
        page.wait_for_timeout(500)
        page.keyboard.press("Enter")
    elif action_type == "terminal_type":
        page.evaluate("""() => {
            const active = document.querySelector('.terminal-wrapper.active');
            if (active) {
                const ta = active.querySelector('textarea.xterm-helper-textarea');
                if (ta) ta.focus();
            }
        }""")
        page.wait_for_timeout(300)
        page.keyboard.type(action["text"])
        if action.get("press_enter", False):
            page.keyboard.press("Enter")
    elif action_type == "dismiss_popups":
        for selector in POPUP_SELECTORS:
            try:
                page.locator(selector).first.click(timeout=1000)
                page.wait_for_timeout(500)
            except:
                pass
    elif action_type == "hide_secondary_sidebar":
        page.evaluate("""() => {
            const aux = document.querySelector('.auxiliarybar');
            if (aux) {
                aux.remove();
                window.dispatchEvent(new Event('resize'));
            }
        }""")


def prerender_tts_mixed(
    spec: dict[str, Any], audio_dir: Path, skip_tts: bool = False
) -> dict[str, Path]:
    """Prerender TTS audio for segment-based specs."""
    segments = spec.get("segments", [])
    audio_paths: dict[str, Path] = {}

    voice = spec["settings"].get("voice", "am_michael")
    speed = float(spec["settings"].get("speech_speed", 1.0))

    kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)

    for segment in segments:
        seg_id = segment["id"]
        narration = segment.get("narration", "")

        if not narration:
            log(f"Phase B: segment {seg_id} has no narration")
            continue

        output_path = audio_dir / f"segment-{seg_id}.wav"

        if skip_tts and output_path.exists():
            log(f"Phase B: reusing segment-{seg_id}.wav")
        else:
            log(f"Phase B: generating TTS for segment {seg_id}")
            try:
                samples, sample_rate = kokoro.create(
                    narration, voice=voice, speed=speed, lang="en-us"
                )
                sf.write(output_path, samples, sample_rate)
                log(f"Phase B: saved {output_path.name}")
            except Exception as exc:
                raise TourError(f"TTS failed for segment {seg_id}: {exc}") from exc

        audio_paths[seg_id] = output_path

    return audio_paths


def assemble_mixed_video(
    spec: dict[str, Any],
    results: list[StepResult],
    step_audio: dict[str, Path],
    dirs: dict[str, Path],
) -> Path:
    """Assemble mixed slide/demo segments with audio into final video."""
    log("Phase D: assembling mixed segment video with audio")

    if not results:
        raise TourError("No segments to assemble")

    assembly_dir = dirs["assembly"]

    # Build segment list with audio
    segments_with_audio: list[tuple[Path, Path]] = []
    for result in results:
        if not result.success or not result.clip_path:
            log(f"Phase D: skipping failed segment {result.step_id}")
            continue

        audio_path = step_audio.get(result.step_id)
        if not audio_path or not audio_path.exists():
            log(f"Phase D: missing audio for {result.step_id}, using silent")
            # Create silent audio
            silent_audio = assembly_dir / f"{result.step_id}-silent.wav"
            cmd = [
                FFMPEG_BIN,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=24000:cl=mono",
                "-t",
                "10",
                "-acodec",
                "pcm_s16le",
                str(silent_audio),
            ]
            subprocess.run(cmd, capture_output=True)
            audio_path = silent_audio

        segments_with_audio.append((result.clip_path, audio_path))

    if not segments_with_audio:
        raise TourError("No successful segments to assemble")

    # Normalize and mux each segment with its audio
    normalized_clips: list[Path] = []
    for i, (video_path, audio_path) in enumerate(segments_with_audio):
        normalized = assembly_dir / f"segment-{i:03d}-normalized.mp4"

        # Mux video with audio, normalize to consistent format
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-vf",
            "fps=30,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(normalized),
        ]
        run_cmd(cmd, f"Normalize segment {i} with audio")
        normalized_clips.append(normalized)

    # Concatenate all normalized segments
    concat_list = assembly_dir / "mixed-concat.txt"
    with concat_list.open("w", encoding="utf-8") as f:
        for clip in normalized_clips:
            f.write(f"file '{clip.as_posix()}'\n")

    final_path = Path(os.path.expanduser(str(spec["output"]["path"]))).resolve()
    final_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        FFMPEG_BIN,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(final_path),
    ]

    run_cmd(cmd, "Concatenate mixed segments")

    # Cleanup
    for clip in normalized_clips:
        clip.unlink(missing_ok=True)

    log(f"Phase D: final video assembled ({ffprobe_duration(final_path):.2f}s)")
    return final_path


if __name__ == "__main__":
    sys.exit(main())
