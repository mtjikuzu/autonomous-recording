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

    for key in ("meta", "settings", "steps", "output"):
        if key not in spec:
            raise TourError(f"Spec missing required field: {key}")
    if not isinstance(spec["steps"], list) or not spec["steps"]:
        raise TourError("Spec 'steps' must be a non-empty array")

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

    step_ids: set[str] = set()
    for idx, step in enumerate(spec["steps"], start=1):
        for field in ("id", "url", "narration"):
            if field not in step:
                raise TourError(f"Step {idx} missing required field: {field}")
        step_id = str(step["id"])
        if step_id in step_ids:
            raise TourError(f"Duplicate step id: {step_id}")
        step_ids.add(step_id)

    target = float(meta["target_duration_seconds"])
    if target <= 0:
        raise TourError("target_duration_seconds must be > 0")
    max_dur = float(meta["max_duration_seconds"])
    if max_dur < target:
        raise TourError("max_duration_seconds must be >= target_duration_seconds")

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
                if focused != 'focused':
                    log(f"Step {step['id']}: JS terminal focus returned: {focused}, trying fallback")
                    # Fallback: click the panel area with force
                    page.locator('.terminal-wrapper.active').first.click(force=True)
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
        for step in spec["steps"]:
            log(f"Phase A: step {step['id']} budget {step['time_budget_seconds']:.2f}s")


        run_pre_setup(spec)

        step_audio = prerender_tts(spec, dirs["audio"], skip_tts=args.skip_tts)
        mode = str(spec["settings"].get("mode", "independent"))
        if mode == "continuous":
            log("Phase C: running in continuous capture mode")
            results = run_continuous_capture(
                spec, step_audio, dirs, dry_run=args.dry_run
            )
        else:
            log("Phase C: running in independent capture mode")
            results = run_capture_phase(spec, step_audio, dirs, dry_run=args.dry_run)

        final_path: Path | None = None
        if not args.dry_run:
            if mode == "continuous":
                final_path = assemble_continuous_video(spec, results, dirs)
            else:
                final_path = assemble_video(spec, results, dirs)
        else:
            log("Dry-run complete: recording and assembly skipped")

        print_report(spec, results, final_path, started)
        return 0
    except Exception as exc:
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
