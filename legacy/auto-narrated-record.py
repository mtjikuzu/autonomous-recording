#!/usr/bin/env python3
"""
Autonomous narrated screen recording system.

Orchestrates OpenClaw agent calls step-by-step — each step is a focused agent
turn (navigate + narrate). TTS file watcher plays audio through PipeWire.
wf-recorder captures fullscreen + audio simultaneously.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time

AUDIO_MONITOR = "alsa_output.pci-0000_05_00.6.analog-stereo.monitor"
TTS_PLAY_DIR = "/tmp/tts-playback"
TTS_WATCH_DIR = "/tmp/openclaw"
OPENCLAW_ENV = os.path.expanduser("~/.openclaw/.env")


# ---------------------------------------------------------------------------
# Recording infrastructure
# ---------------------------------------------------------------------------


def get_monitor_geometry():
    result = subprocess.run(
        ["hyprctl", "monitors", "-j"], capture_output=True, text=True
    )
    monitors = json.loads(result.stdout)
    for m in monitors:
        if m.get("focused"):
            x, y = m["x"], m["y"]
            w = int(m["width"] / m.get("scale", 1))
            h = int(m["height"] / m.get("scale", 1))
            return f"{x},{y} {w}x{h}"
    result = subprocess.run(
        ["hyprctl", "activewindow", "-j"], capture_output=True, text=True
    )
    w = json.loads(result.stdout)
    x, y = w["at"]
    width, height = w["size"]
    return f"{x},{y} {width}x{height}"


_idle_watchdog = None


def _watchdog_loop():
    while True:
        subprocess.run(["killall", "-9", "hyprlock"], capture_output=True)
        subprocess.run(["killall", "-9", "hypridle"], capture_output=True)
        subprocess.run(["loginctl", "unlock-session"], capture_output=True)
        time.sleep(10)


def inhibit_idle():
    global _idle_watchdog
    subprocess.run(["killall", "-9", "hypridle"], capture_output=True)
    subprocess.run(["killall", "-9", "hyprlock"], capture_output=True)
    subprocess.run(["loginctl", "unlock-session"], capture_output=True)
    subprocess.run(["hyprctl", "dispatch", "dpms", "on"], capture_output=True)
    _idle_watchdog = threading.Thread(target=_watchdog_loop, daemon=True)
    _idle_watchdog.start()
    print("  [infra] Idle inhibitor active")


def restore_idle():
    global _idle_watchdog
    _idle_watchdog = None
    subprocess.Popen(["hypridle"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("  [infra] hypridle restarted")


def ensure_fullscreen(window_selector, selector_type="class"):
    if selector_type == "pid":
        subprocess.run(
            ["hyprctl", "dispatch", "focuswindow", f"pid:{window_selector}"],
            capture_output=True,
        )
    elif selector_type == "class":
        subprocess.run(
            ["hyprctl", "dispatch", "focuswindow", f"class:{window_selector}"],
            capture_output=True,
        )
    time.sleep(0.3)
    result = subprocess.run(
        ["hyprctl", "activewindow", "-j"], capture_output=True, text=True
    )
    w = json.loads(result.stdout)
    if w.get("fullscreen", 0) == 0:
        subprocess.run(["hyprctl", "dispatch", "fullscreen", "0"], capture_output=True)
        time.sleep(0.5)
    print(f"  [infra] Fullscreen: {selector_type}:{window_selector}")


def start_recorder(geometry, output_path):
    cmd = [
        "wf-recorder",
        "-g",
        geometry,
        "-f",
        output_path,
        "-c",
        "libx264",
        "-p",
        "preset=ultrafast",
        "-p",
        "crf=18",
        "-D",
        f"-a={AUDIO_MONITOR}",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  [infra] Recorder started (PID {proc.pid})")
    return proc


def stop_recorder(proc):
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    time.sleep(0.5)
    print("  [infra] Recorder stopped")


# ---------------------------------------------------------------------------
# TTS file watcher — copies + plays OpenClaw TTS MP3s through PipeWire
# ---------------------------------------------------------------------------


_tts_watcher_stop = threading.Event()
_tts_played_files = set()
_tts_play_count = 0


def _collect_tts_files():
    found = set()
    try:
        for entry in os.scandir(TTS_WATCH_DIR):
            if entry.is_dir() and entry.name.startswith("tts-"):
                for f in os.scandir(entry.path):
                    if f.name.endswith(".mp3") and f.is_file():
                        found.add(f.path)
    except FileNotFoundError:
        pass
    return found


def _wait_for_stable_file(path, timeout=30):
    """Wait for file to have non-zero size that stops changing."""
    prev_size = -1
    stable_count = 0
    for _ in range(timeout * 10):
        try:
            size = os.path.getsize(path)
        except OSError:
            time.sleep(0.1)
            continue
        if size > 0 and size == prev_size:
            stable_count += 1
            if stable_count >= 3:
                return True
        else:
            stable_count = 0
        prev_size = size
        time.sleep(0.1)
    return False


def _tts_watcher_loop():
    import shutil

    global _tts_play_count
    os.makedirs(TTS_PLAY_DIR, exist_ok=True)
    while not _tts_watcher_stop.is_set():
        current = _collect_tts_files()
        new_files = sorted(current - _tts_played_files)
        for mp3 in new_files:
            _tts_played_files.add(mp3)
            if not _wait_for_stable_file(mp3, timeout=30):
                print(f"  [tts] File never stabilized: {mp3}")
                continue
            stable_copy = os.path.join(TTS_PLAY_DIR, f"tts-{_tts_play_count:03d}.mp3")
            try:
                shutil.copy2(mp3, stable_copy)
            except (FileNotFoundError, OSError) as exc:
                print(f"  [tts] Copy failed: {exc}")
                continue
            size = os.path.getsize(stable_copy)
            dur_est = size / 6000
            print(f"  [tts] Playing #{_tts_play_count} ({size}B, ~{dur_est:.0f}s)")
            try:
                subprocess.run(
                    ["pw-play", stable_copy],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                print(f"  [tts] Playback timeout #{_tts_play_count}")
            except Exception as exc:
                print(f"  [tts] Playback error: {exc}")
            _tts_play_count += 1
        _tts_watcher_stop.wait(0.2)


def start_tts_watcher():
    global _tts_played_files, _tts_play_count
    import shutil

    if os.path.exists(TTS_PLAY_DIR):
        shutil.rmtree(TTS_PLAY_DIR)
    os.makedirs(TTS_PLAY_DIR, exist_ok=True)
    _tts_played_files = _collect_tts_files()
    _tts_play_count = 0
    _tts_watcher_stop.clear()
    t = threading.Thread(target=_tts_watcher_loop, daemon=True)
    t.start()
    print(f"  [tts] Watcher started (ignoring {len(_tts_played_files)} existing)")
    return t


def stop_tts_watcher():
    _tts_watcher_stop.set()
    time.sleep(0.5)
    print(f"  [tts] Watcher stopped ({_tts_play_count} files played)")


# ---------------------------------------------------------------------------
# OpenClaw agent — one call per step
# ---------------------------------------------------------------------------


def _shell_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def call_agent(prompt, timeout=120):
    """Send a single focused prompt to OpenClaw and return response text."""
    cmd = (
        f"source {OPENCLAW_ENV} && "
        f"openclaw agent --agent main "
        f"-m {_shell_quote(prompt)} "
        f"--json --timeout {timeout}"
    )
    proc = subprocess.Popen(
        cmd,
        shell=True,
        executable="/bin/bash",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout + 30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return None

    if proc.returncode != 0:
        return None

    try:
        response = json.loads(stdout.decode("utf-8", errors="replace"))
        payloads = response.get("result", {}).get("payloads", [])
        if payloads:
            return payloads[0].get("text", "")
    except json.JSONDecodeError:
        pass
    return None


def run_step(step_num, total, instruction, wait_after=5):
    """Execute one recording step: agent does action + TTS, then we wait."""
    print(f"\n  [{step_num}/{total}] {instruction[:80]}...")
    resp = call_agent(instruction, timeout=120)
    if resp:
        print(f"  [{step_num}/{total}] Agent: {resp[:120]}...")
    else:
        print(f"  [{step_num}/{total}] Agent returned no response")
    print(f"  [{step_num}/{total}] Waiting {wait_after}s for TTS playback + viewing...")
    time.sleep(wait_after)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Autonomous narrated screen recording with OpenClaw"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prompt", help="High-level recording goal (inline)")
    group.add_argument("--prompt-file", help="Path to file with recording goal")
    group.add_argument(
        "--steps-file",
        help="JSON file with explicit steps array",
    )

    parser.add_argument(
        "--output",
        default=os.path.expanduser("~/auto-narrated.mp4"),
    )
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--browser-class", default="chromium")
    parser.add_argument("--pre-delay", type=float, default=3.0)
    parser.add_argument("--post-delay", type=float, default=5.0)
    parser.add_argument(
        "--step-wait",
        type=float,
        default=5.0,
        help="Seconds to wait after each step for TTS playback",
    )

    args = parser.parse_args()

    user_prompt = None
    steps = None
    if args.steps_file:
        with open(args.steps_file) as f:
            steps_data = json.load(f)
        steps = (
            steps_data if isinstance(steps_data, list) else steps_data.get("steps", [])
        )
    elif args.prompt_file:
        with open(args.prompt_file) as f:
            user_prompt = f.read().strip()
    else:
        user_prompt = args.prompt

    print("=" * 60)
    print("AUTONOMOUS NARRATED SCREEN RECORDING")
    print("=" * 60)
    print(f"  Output:  {args.output}")
    print(f"  Browser: {args.browser_class}")
    if steps:
        print(f"  Steps:   {len(steps)}")
    print()

    print("[Phase 1] Recording infrastructure...")
    inhibit_idle()

    recorder = None
    try:
        ensure_fullscreen(args.browser_class, "class")
        time.sleep(1)

        geometry = get_monitor_geometry()
        print(f"  Geometry: {geometry}")

        recorder = start_recorder(geometry, args.output)
        time.sleep(args.pre_delay)

        start_tts_watcher()

        print("\n[Phase 2] Recording...")

        if steps:
            for i, step in enumerate(steps, 1):
                if isinstance(step, str):
                    instruction = step
                    wait = args.step_wait
                else:
                    instruction = step.get("instruction", step.get("prompt", ""))
                    wait = step.get("wait", args.step_wait)
                run_step(i, len(steps), instruction, wait_after=wait)
        else:
            full_prompt = (
                "You are recording a narrated screen tour. A screen recorder is "
                "capturing everything on screen and all system audio right now. "
                "An audio playback system will automatically play your TTS output "
                "through the speakers.\n\n"
                f"YOUR TASK:\n{user_prompt}\n\n"
                "Use your browser tool to navigate and your tts tool to narrate. "
                "After each tts call, use exec to sleep for the estimated audio "
                "duration (1 second per 2-3 words). "
                f"The browser ({args.browser_class}) is already fullscreen. "
                "Do NOT resize or open new windows. "
                "When done, respond with RECORDING_COMPLETE."
            )
            print(f"  Single-prompt mode (timeout={args.timeout}s)")
            resp = call_agent(full_prompt, timeout=args.timeout)
            if resp:
                print(f"  Agent: {resp[:200]}")

        print(f"\n[Phase 3] Buffer ({args.post_delay}s)...")
        time.sleep(args.post_delay)

    except KeyboardInterrupt:
        print("\n  Interrupted.")
    except Exception as e:
        print(f"  Error: {e}")
    finally:
        stop_tts_watcher()
        if recorder:
            stop_recorder(recorder)
        restore_idle()

    if os.path.exists(args.output):
        size = os.path.getsize(args.output)
        dur = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                args.output,
            ],
            capture_output=True,
            text=True,
        ).stdout.strip()
        print(f"\n{'=' * 60}")
        print("RECORDING COMPLETE")
        print(f"{'=' * 60}")
        print(f"  File:     {args.output}")
        print(f"  Size:     {size / 1024 / 1024:.1f} MB")
        if dur:
            print(f"  Duration: {float(dur):.1f}s ({float(dur) / 60:.1f} min)")
    else:
        print("\nWARNING: No output file.")
        sys.exit(1)


if __name__ == "__main__":
    main()
