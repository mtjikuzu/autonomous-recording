#!/usr/bin/env python3
"""
Narrated screen recording system.

Pre-synthesizes narration with Kokoro TTS, then plays audio segments
through PipeWire during wf-recorder capture. The recorder picks up
both screen and system audio simultaneously.

Usage:
  python3 narrated-record.py --config demo.json
  python3 narrated-record.py --config demo.json --voice am_michael --speed 1.0
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time

KOKORO_MODEL = os.path.expanduser("~/.openclaw/models/kokoro-v1.0.onnx")
KOKORO_VOICES = os.path.expanduser("~/.openclaw/models/voices-v1.0.bin")
AUDIO_MONITOR = "alsa_output.pci-0000_05_00.6.analog-stereo.monitor"


def get_active_geometry():
    result = subprocess.run(
        ["hyprctl", "activewindow", "-j"], capture_output=True, text=True
    )
    w = json.loads(result.stdout)
    x, y = w["at"]
    width, height = w["size"]
    return f"{x},{y} {width}x{height}"


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
    return get_active_geometry()


_idle_watchdog = None


def _watchdog_loop():
    while True:
        subprocess.run(["killall", "-9", "hyprlock"], capture_output=True)
        subprocess.run(["killall", "-9", "hypridle"], capture_output=True)
        subprocess.run(
            ["loginctl", "unlock-session"], capture_output=True
        )
        time.sleep(10)


def inhibit_idle():
    global _idle_watchdog
    import threading
    subprocess.run(["killall", "-9", "hypridle"], capture_output=True)
    subprocess.run(["killall", "-9", "hyprlock"], capture_output=True)
    subprocess.run(["loginctl", "unlock-session"], capture_output=True)
    subprocess.run(
        ["hyprctl", "dispatch", "dpms", "on"], capture_output=True
    )
    _idle_watchdog = threading.Thread(target=_watchdog_loop, daemon=True)
    _idle_watchdog.start()
    print("  Idle inhibitor: hypridle/hyprlock killed, watchdog active")


def restore_idle():
    global _idle_watchdog
    _idle_watchdog = None
    subprocess.Popen(["hypridle"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("  Idle inhibitor: hypridle restarted")


def ensure_fullscreen(window_selector, selector_type="pid"):
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
    is_fullscreen = w.get("fullscreen", 0)
    if is_fullscreen == 0:
        subprocess.run(["hyprctl", "dispatch", "fullscreen", "0"], capture_output=True)
        time.sleep(0.5)


def focus_and_maximize(pid):
    ensure_fullscreen(pid, "pid")


def synthesize_segments(segments, voice, speed, lang, output_dir):
    from kokoro_onnx import Kokoro
    import soundfile as sf

    print(f"Loading Kokoro model...")
    kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)

    wav_paths = []
    for i, seg in enumerate(segments):
        text = seg["narration"]
        wav_path = os.path.join(output_dir, f"narration-{i:03d}.wav")

        print(
            f'  [{i + 1}/{len(segments)}] Synthesizing: "{text[:60]}{"..." if len(text) > 60 else ""}"'
        )
        samples, sr = kokoro.create(text, voice=voice, speed=speed, lang=lang)
        sf.write(wav_path, samples, sr)

        duration = len(samples) / sr
        wav_paths.append({"path": wav_path, "duration": duration})
        print(f"           -> {duration:.1f}s audio")

    return wav_paths


def play_audio(wav_path):
    return subprocess.Popen(
        ["pw-play", wav_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def wait_for_signal(signal_file, timeout=600):
    for _ in range(timeout):
        if os.path.exists(signal_file):
            return True
        time.sleep(1)
    return False


def start_recorder(geometry, output_path, with_audio=True):
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
    ]
    if with_audio:
        cmd.extend([f"-a={AUDIO_MONITOR}"])

    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_recorder(proc):
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    time.sleep(0.5)


def record_terminal_clip(clip_config, narration_wavs, output_dir):
    clip_name = clip_config["name"]
    script_path = clip_config["script"]
    segments = clip_config["segments"]
    signal_file = os.path.join(output_dir, f"{clip_name}-done")
    clip_output = os.path.join(output_dir, f"{clip_name}.mp4")

    os.makedirs(output_dir, exist_ok=True)

    if os.path.exists(signal_file):
        os.remove(signal_file)

    env = os.environ.copy()
    env["RECORDING_SIGNAL_FILE"] = signal_file

    print(f"\n=== Recording: {clip_name} ===")

    print(f"  Launching terminal...")
    term = subprocess.Popen(
        [
            "ghostty",
            f"--title={clip_config.get('title', clip_name)}",
            "-e",
            script_path,
        ],
        env=env,
    )
    time.sleep(2)

    ensure_fullscreen(term.pid, "pid")

    geometry = get_monitor_geometry()
    print(f"  Geometry (fullscreen): {geometry}")

    print(f"  Starting recorder...")
    recorder = start_recorder(geometry, clip_output, with_audio=True)
    time.sleep(1)

    seg_index = 0
    for seg in segments:
        trigger = seg.get("trigger", "immediate")
        wav_info = narration_wavs[seg_index]



        if trigger == "immediate":
            print(
                f'  Playing narration segment {seg_index}: "{seg["narration"][:50]}..."'
            )
            player = play_audio(wav_info["path"])
            player.wait()
            time.sleep(seg.get("pause_after", 1.0))

        elif trigger == "signal":
            sig_name = seg.get("signal_name", f"{clip_name}-seg-{seg_index}")
            sig_path = os.path.join(output_dir, sig_name)
            print(f"  Waiting for signal: {sig_name}")
            if wait_for_signal(sig_path, timeout=120):
                print(f"  Signal received, playing narration segment {seg_index}")
                player = play_audio(wav_info["path"])
                player.wait()
                time.sleep(seg.get("pause_after", 1.0))
            else:
                print(f"  WARNING: Signal timeout for {sig_name}")

        elif trigger == "delay":
            delay = seg.get("delay", 0)
            print(f"  Waiting {delay}s before narration segment {seg_index}")
            time.sleep(delay)
            player = play_audio(wav_info["path"])
            player.wait()
            time.sleep(seg.get("pause_after", 1.0))

        seg_index += 1

    print(f"  Waiting for clip completion signal...")
    wait_for_signal(signal_file, timeout=300)
    time.sleep(3)

    print(f"  Stopping recorder...")
    stop_recorder(recorder)
    term.kill()
    term.wait()
    time.sleep(0.5)

    print(f"  Clip saved: {clip_output}")
    return clip_output


def record_browser_clip(clip_config, narration_wavs, output_dir):
    clip_name = clip_config["name"]
    segments = clip_config["segments"]
    clip_output = os.path.join(output_dir, f"{clip_name}.mp4")
    url = clip_config["url"]
    cdp_port = clip_config.get("cdp_port", 9222)

    os.makedirs(output_dir, exist_ok=True)

    print(f"\n=== Recording: {clip_name} ===")

    try:
        page_ws = subprocess.run(
            ["curl", "-s", f"http://localhost:{cdp_port}/json"],
            capture_output=True,
            text=True,
        )
        pages = json.loads(page_ws.stdout)
        ws_url = None
        for p in pages:
            if url.split("#")[0].split("?")[0] in p.get("url", ""):
                ws_url = p["webSocketDebuggerUrl"]
                break
        if not ws_url:
            ws_url = pages[0]["webSocketDebuggerUrl"] if pages else None
    except Exception:
        ws_url = None

    if not ws_url:
        print(
            f"  No CDP browser found. Launch chromium with --remote-debugging-port={cdp_port}"
        )
        return None

    browser_pid = clip_config.get("browser_pid")
    browser_class = clip_config.get("browser_class", "chromium")
    if browser_pid:
        ensure_fullscreen(browser_pid, "pid")
    else:
        ensure_fullscreen(browser_class, "class")

    geometry = get_monitor_geometry()
    print(f"  Geometry (fullscreen): {geometry}")

    import asyncio
    import websockets

    async def run_browser_recording():
        async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:

            async def cdp_eval(expr, msg_id=1):
                msg = json.dumps(
                    {
                        "id": msg_id,
                        "method": "Runtime.evaluate",
                        "params": {
                            "expression": expr,
                            "returnByValue": True,
                            "awaitPromise": True,
                        },
                    }
                )
                await ws.send(msg)
                resp = json.loads(await ws.recv())
                return resp.get("result", {}).get("result", {}).get("value")

            enable_msg = json.dumps({"id": 0, "method": "Page.enable", "params": {}})
            await ws.send(enable_msg)
            await asyncio.sleep(1)

            print(f"  Starting recorder...")
            recorder = start_recorder(geometry, clip_output, with_audio=True)
            await asyncio.sleep(2)

            seg_index = 0
            for seg in segments:
                wav_info = narration_wavs[seg_index]
                action = seg.get("action")



                print(f"  Playing narration segment {seg_index}")
                player = play_audio(wav_info["path"])

                if seg.get("play_before_action", True):
                    player.wait()
                    await asyncio.sleep(seg.get("pause_after_narration", 0.5))

                if action == "type_and_send":
                    message = seg["message"]
                    await cdp_eval(
                        f"""
                        (() => {{
                            const ta = document.querySelector('textarea');
                            if (!ta) return 'no textarea';
                            ta.focus(); ta.click();
                            const setter = Object.getOwnPropertyDescriptor(
                                window.HTMLTextAreaElement.prototype, 'value'
                            ).set;
                            setter.call(ta, '{message}');
                            ta.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            return 'typed';
                        }})()
                    """,
                        10 + seg_index,
                    )
                    await asyncio.sleep(1)
                    await cdp_eval(
                        """
                        (() => {
                            const ta = document.querySelector('textarea');
                            ta.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true}));
                            return 'sent';
                        })()
                    """,
                        20 + seg_index,
                    )

                elif action == "wait_for_response":
                    for i in range(60):
                        await asyncio.sleep(1)
                        status = await cdp_eval(
                            """
                            (() => {
                                const btns = Array.from(document.querySelectorAll('button'));
                                if (btns.find(b => b.textContent.includes('Stop'))) return 'streaming';
                                if (btns.find(b => b.textContent.includes('Send'))) return 'done';
                                return 'unknown';
                            })()
                        """,
                            50 + i,
                        )
                        await cdp_eval(
                            """
                            (() => { const l = document.querySelector('[role=log]'); if(l) l.scrollTop = l.scrollHeight; return 'ok'; })()
                        """,
                            80 + i,
                        )
                        if status == "done" and i > 3:
                            break

                elif action == "navigate":
                    nav_url = seg["url"]
                    print(f"  Navigating to: {nav_url}")
                    nav_msg = json.dumps(
                        {
                            "id": 200 + seg_index,
                            "method": "Page.navigate",
                            "params": {"url": nav_url},
                        }
                    )
                    await ws.send(nav_msg)
                    await asyncio.sleep(1)
                    for _ in range(30):
                        try:
                            resp_raw = await asyncio.wait_for(ws.recv(), timeout=1)
                            resp_data = json.loads(resp_raw)
                            if resp_data.get("id") == 200 + seg_index:
                                break
                        except asyncio.TimeoutError:
                            pass
                    load_wait = seg.get("load_wait", 3)
                    await asyncio.sleep(load_wait)

                elif action == "scroll":
                    scroll_to = seg.get("scroll_to", "bottom")
                    scroll_speed = seg.get("scroll_speed", 800)
                    if scroll_to == "bottom":
                        await cdp_eval(
                            f"window.scrollBy({{top: {scroll_speed}, behavior: 'smooth'}})",
                            300 + seg_index,
                        )
                    elif scroll_to == "top":
                        await cdp_eval(
                            "window.scrollTo({top: 0, behavior: 'smooth'})",
                            300 + seg_index,
                        )
                    elif isinstance(scroll_to, int):
                        await cdp_eval(
                            f"window.scrollTo({{top: {scroll_to}, behavior: 'smooth'}})",
                            300 + seg_index,
                        )
                    else:
                        await cdp_eval(
                            f"(() => {{ const el = document.querySelector('{scroll_to}'); if(el) el.scrollIntoView({{behavior: 'smooth', block: 'center'}}); return 'ok'; }})()",
                            300 + seg_index,
                        )
                    await asyncio.sleep(seg.get("scroll_pause", 1.5))

                elif action == "click":
                    selector = seg["selector"]
                    await cdp_eval(
                        f"(() => {{ const el = document.querySelector('{selector}'); if(el) {{ el.click(); return 'clicked'; }} return 'not found'; }})()",
                        400 + seg_index,
                    )
                    await asyncio.sleep(seg.get("click_wait", 2))

                elif action == "wait":
                    wait_time = seg.get("duration", 3)
                    await asyncio.sleep(wait_time)

                if not seg.get("play_before_action", True):
                    player.wait()

                await asyncio.sleep(seg.get("pause_after", 1.0))
                seg_index += 1

            await asyncio.sleep(3)

            print(f"  Stopping recorder...")
            stop_recorder(recorder)

    asyncio.run(run_browser_recording())
    print(f"  Clip saved: {clip_output}")
    return clip_output


def merge_clips(clip_paths, output_path, resolution="1280:720", fps=30):
    print(f"\n=== Merging {len(clip_paths)} clips ===")

    inputs = []
    filter_parts = []
    concat_inputs = []

    for i, path in enumerate(clip_paths):
        inputs.extend(["-i", path])
        filter_parts.append(
            f"[{i}:v]scale={resolution}:force_original_aspect_ratio=decrease,"
            f"pad={resolution}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{i}]"
        )
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "csv=p=0",
                path,
            ],
            capture_output=True,
            text=True,
        )
        has_audio = bool(probe.stdout.strip())

        if has_audio:
            filter_parts.append(
                f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{i}]"
            )
            concat_inputs.append(f"[v{i}][a{i}]")
        else:
            filter_parts.append(
                f"anullsrc=r=48000:cl=stereo:d=1[silence{i}]; "
                f"[silence{i}]atrim=duration=0.001[a{i}]"
            )
            concat_inputs.append(f"[v{i}][a{i}]")

    concat_str = "".join(concat_inputs)
    filter_parts.append(f"{concat_str}concat=n={len(clip_paths)}:v=1:a=1[outv][outa]")

    filter_complex = "; ".join(filter_parts)

    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        output_path,
    ]

    print(f"  Running ffmpeg merge...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ffmpeg error: {result.stderr[-500:]}")
    else:
        size = os.path.getsize(output_path)
        print(f"  Output: {output_path} ({size / 1024 / 1024:.1f}MB)")


def main():
    parser = argparse.ArgumentParser(description="Narrated screen recorder")
    parser.add_argument("--config", required=True, help="JSON config file")
    parser.add_argument(
        "--voice", default="am_michael", help="Kokoro voice (default: am_michael)"
    )
    parser.add_argument("--speed", type=float, default=1.0, help="Speech speed")
    parser.add_argument("--lang", default="en-us", help="Language code")
    parser.add_argument(
        "--output-dir", default="/tmp/narrated-recording", help="Working directory"
    )
    parser.add_argument(
        "--skip-synth", action="store_true", help="Skip TTS synthesis (reuse existing)"
    )
    args = parser.parse_args()

    with open(args.config) as f:
        config = json.load(f)

    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    all_segments = []
    clip_segment_ranges = []
    for clip in config["clips"]:
        start = len(all_segments)
        for seg in clip["segments"]:
            all_segments.append(seg)
        clip_segment_ranges.append((start, len(all_segments)))

    if not args.skip_synth:
        print(f"\n=== Synthesizing {len(all_segments)} narration segments ===")
        all_wavs = synthesize_segments(
            all_segments, args.voice, args.speed, args.lang, output_dir
        )
    else:
        print(f"\n=== Reusing existing narration WAVs ===")
        all_wavs = []
        for i in range(len(all_segments)):
            wav_path = os.path.join(output_dir, f"narration-{i:03d}.wav")
            if os.path.exists(wav_path):
                import soundfile as sf

                data, sr = sf.read(wav_path)
                duration = len(data) / sr
                all_wavs.append({"path": wav_path, "duration": duration})
            else:
                print(f"  WARNING: Missing {wav_path}")
                all_wavs.append({"path": wav_path, "duration": 0})

    inhibit_idle()

    try:
        clip_paths = []
        for i, clip in enumerate(config["clips"]):
            start, end = clip_segment_ranges[i]
            clip_wavs = all_wavs[start:end]

            if clip["type"] == "terminal":
                path = record_terminal_clip(clip, clip_wavs, output_dir)
            elif clip["type"] == "browser":
                path = record_browser_clip(clip, clip_wavs, output_dir)
            else:
                print(f"  Unknown clip type: {clip['type']}")
                continue

            if path:
                clip_paths.append(path)

        if len(clip_paths) > 1:
            final_output = config.get(
                "output", os.path.expanduser("~/demo-narrated.mp4")
            )
            merge_clips(clip_paths, final_output)
        elif len(clip_paths) == 1:
            final_output = config.get(
                "output", os.path.expanduser("~/demo-narrated.mp4")
            )
            subprocess.run(["cp", clip_paths[0], final_output])
            print(f"\n  Single clip copied to: {final_output}")
    finally:
        restore_idle()

    print("\n=== Done! ===")


if __name__ == "__main__":
    main()
