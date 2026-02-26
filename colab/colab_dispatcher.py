"""
Google Drive-based TTS job dispatcher for Colab offloading.

This module provides the local-side interface for dispatching TTS jobs
to a Google Colab T4 worker via Google Drive sync.

Usage from record-tour.py:
    from colab.colab_dispatcher import ColabTTSDispatcher
    dispatcher = ColabTTSDispatcher(drive_base="~/Google Drive/autonomous-recording/tts-jobs")
    step_audio = dispatcher.dispatch_and_wait(spec, audio_dir)
"""

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ColabTTSConfig:
    """Configuration for Colab TTS offloading."""

    # Local path to the Google Drive sync directory.
    # This is where Google Drive for Desktop mounts on your machine.
    # Common paths:
    #   Linux (drive CLI):  ~/gdrive/autonomous-recording/tts-jobs
    #   Linux (rclone):     ~/gdrive/autonomous-recording/tts-jobs
    #   macOS:              ~/Library/CloudStorage/GoogleDrive-*/My Drive/autonomous-recording/tts-jobs
    #   Windows:            G:/My Drive/autonomous-recording/tts-jobs
    drive_base: Path

    # How long to wait for Colab to finish (seconds)
    timeout: float = 600.0

    # How often to check for completion (seconds)
    poll_interval: float = 5.0

    # How long to wait for Drive sync after writing job (seconds)
    sync_delay: float = 10.0


class ColabTTSError(Exception):
    """Raised when Colab TTS dispatch fails."""

    pass


class ColabTTSDispatcher:
    """Dispatches TTS jobs to Google Colab via Google Drive.

    Protocol:
        1. Local creates job_dir/<job-id>/request.json
        2. Google Drive syncs to Colab
        3. Colab worker picks up job, generates WAVs
        4. Colab writes job_dir/<job-id>/done.marker
        5. Google Drive syncs back to local
        6. Local reads WAV files from job_dir/<job-id>/audio/
    """

    def __init__(self, config: ColabTTSConfig) -> None:
        self.config = config
        self.drive_base = Path(os.path.expanduser(str(config.drive_base))).resolve()

    def _log(self, message: str) -> None:
        from datetime import datetime

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] [colab-tts] {message}", flush=True)

    def dispatch_and_wait(
        self,
        spec: dict[str, Any],
        audio_dir: Path,
    ) -> dict[str, dict[str, Any]]:
        """Dispatch TTS job to Colab and wait for results.

        Args:
            spec: Tour spec with steps[].narration and settings (voice, speed, language)
            audio_dir: Local directory to copy finished WAV files into

        Returns:
            Dict mapping step_id -> {"path": Path, "duration": float}
            (same format as prerender_tts)
        """
        # Validate Drive directory is accessible
        if not self.drive_base.parent.exists():
            raise ColabTTSError(
                f"Google Drive sync directory not found: {self.drive_base.parent}\n"
                "Make sure Google Drive is mounted/synced on this machine.\n"
                "See colab/README.md for setup instructions."
            )

        self.drive_base.mkdir(parents=True, exist_ok=True)

        # Create job
        job_id = self._create_job_id()
        job_dir = self.drive_base / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        self._log(f"Creating TTS job: {job_id}")

        # Build request
        settings = spec["settings"]
        items = spec.get("steps") or spec.get("segments", [])

        steps_data = []
        for item in items:
            narration = str(item.get("narration", "")).strip()
            if narration:
                steps_data.append({"id": str(item["id"]), "narration": narration})

        if not steps_data:
            raise ColabTTSError("No narration text found in spec")

        request = {
            "voice": str(settings.get("voice", "am_michael")),
            "speed": float(settings.get("speech_speed", 1.0)),
            "language": str(settings.get("language", "en-us")),
            "steps": steps_data,
        }

        # Write request file
        request_path = job_dir / "request.json"
        with request_path.open("w", encoding="utf-8") as f:
            json.dump(request, f, indent=2)

        self._log(f"Job {job_id}: {len(steps_data)} steps dispatched to Drive")
        self._log(f"Waiting {self.config.sync_delay:.0f}s for Drive sync...")
        time.sleep(self.config.sync_delay)

        # Wait for completion
        step_audio = self._wait_for_completion(job_id, job_dir, audio_dir, items)
        return step_audio

    def _create_job_id(self) -> str:
        """Generate a unique job ID based on timestamp."""
        from datetime import datetime

        return datetime.now().strftime("job-%Y%m%d-%H%M%S")

    def _wait_for_completion(
        self,
        job_id: str,
        job_dir: Path,
        audio_dir: Path,
        items: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Poll for job completion and copy results."""
        done_marker = job_dir / "done.marker"
        error_marker = job_dir / "error.marker"
        remote_audio_dir = job_dir / "audio"

        deadline = time.time() + self.config.timeout
        last_status = ""

        self._log(f"Waiting for Colab worker (timeout: {self.config.timeout:.0f}s)...")

        while time.time() < deadline:
            # Check for error
            if error_marker.exists():
                try:
                    with error_marker.open("r") as f:
                        error_info = json.load(f)
                    raise ColabTTSError(
                        f"Colab worker reported error: {error_info.get('error', 'unknown')}"
                    )
                except json.JSONDecodeError:
                    raise ColabTTSError(
                        "Colab worker reported error (could not read details)"
                    )

            # Check for completion
            if done_marker.exists():
                self._log("Job completed by Colab worker!")
                break

            # Status update
            elapsed = self.config.timeout - (deadline - time.time())
            status = f"waiting... ({elapsed:.0f}s elapsed)"
            if status != last_status:
                # Check if any WAVs have appeared (progress indicator)
                wav_count = 0
                if remote_audio_dir.exists():
                    wav_count = len(list(remote_audio_dir.glob("*.wav")))
                if wav_count > 0:
                    status = f"generating... ({wav_count} WAVs so far, {elapsed:.0f}s elapsed)"
                self._log(status)
                last_status = status

            time.sleep(self.config.poll_interval)
        else:
            raise ColabTTSError(
                f"Timeout waiting for Colab worker ({self.config.timeout:.0f}s).\n"
                "Check that:\n"
                "  1. The Colab notebook is running (tts_worker.ipynb)\n"
                "  2. The watcher cell (cell 8) is executing\n"
                "  3. Google Drive sync is working on both ends\n"
                f"  Job directory: {job_dir}"
            )

        # Read completion metadata
        try:
            with done_marker.open("r") as f:
                completion = json.load(f)
            self._log(
                f"Results: {completion.get('steps_generated', '?')} steps, "
                f"{completion.get('total_duration', 0):.2f}s total audio"
            )
        except (json.JSONDecodeError, KeyError):
            pass

        # Copy WAV files to local audio directory
        return self._copy_results(job_dir, audio_dir, items)

    def _copy_results(
        self,
        job_dir: Path,
        audio_dir: Path,
        items: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Copy WAV files from Drive job dir to local audio dir."""
        import soundfile as sf

        remote_audio_dir = job_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        step_audio: dict[str, dict[str, Any]] = {}
        total_duration = 0.0

        for item in items:
            step_id = str(item["id"])
            narration = str(item.get("narration", "")).strip()
            if not narration:
                continue

            remote_wav = remote_audio_dir / f"step-{step_id}.wav"
            local_wav = audio_dir / f"step-{step_id}.wav"

            if not remote_wav.exists():
                raise ColabTTSError(
                    f"Expected WAV file not found: {remote_wav}\n"
                    "The Colab worker may not have generated all steps."
                )

            # Copy to local audio directory
            shutil.copy2(remote_wav, local_wav)

            # Read duration
            data, sample_rate = sf.read(str(local_wav), always_2d=False)
            sample_count = data.shape[0] if hasattr(data, "shape") else len(data)
            duration = float(sample_count) / float(sample_rate)

            step_audio[step_id] = {"path": local_wav, "duration": duration}
            total_duration += duration

            self._log(f"  ✓ {local_wav.name} ({duration:.2f}s)")

        self._log(f"Total: {total_duration:.2f}s of audio copied to {audio_dir}")
        return step_audio

    def cleanup_job(self, job_id: str) -> None:
        """Remove a completed job from Drive (optional cleanup)."""
        job_dir = self.drive_base / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir)
            self._log(f"Cleaned up job: {job_id}")


def create_dispatcher_from_args(
    drive_path: str | None = None,
    timeout: float = 600.0,
    poll_interval: float = 3.0,
) -> ColabTTSDispatcher:
    """Create a dispatcher from CLI arguments or environment variables.

    Priority: explicit argument > COLAB_TTS_DRIVE_PATH env var > default path
    """
    if drive_path is None:
        drive_path = os.environ.get("COLAB_TTS_DRIVE_PATH")

    if drive_path is None:
        # Try common Google Drive mount points
        candidates = [
            Path.home() / "gdrive" / "autonomous-recording" / "tts-jobs",
            Path.home()
            / "Google Drive"
            / "My Drive"
            / "autonomous-recording"
            / "tts-jobs",
        ]
        # Also check for rclone-style mounts
        for candidate in candidates:
            if candidate.parent.exists():
                drive_path = str(candidate)
                break

        if drive_path is None:
            drive_path = str(
                Path.home() / "gdrive" / "autonomous-recording" / "tts-jobs"
            )

    config = ColabTTSConfig(
        drive_base=Path(drive_path),
        timeout=timeout,
        poll_interval=poll_interval,
    )
    return ColabTTSDispatcher(config)


class ColabF5TTSDispatcher(ColabTTSDispatcher):
    """Dispatches F5-TTS voice cloning jobs to Google Colab via Google Drive.

    Same protocol as ColabTTSDispatcher but with additional fields:
    - ref_audio: reference voice clip filename
    - ref_text: transcription of the reference audio
    - seed: reproducibility seed
    - nfe_step: number of flow-matching steps (quality vs speed)
    """

    def dispatch_and_wait(
        self,
        spec: dict[str, Any],
        audio_dir: Path,
    ) -> dict[str, dict[str, Any]]:
        """Dispatch F5-TTS job to Colab and wait for results."""
        if not self.drive_base.parent.exists():
            raise ColabTTSError(
                f"Google Drive sync directory not found: {self.drive_base.parent}\n"
                "Make sure Google Drive is mounted/synced on this machine.\n"
                "See colab/README.md for setup instructions."
            )

        self.drive_base.mkdir(parents=True, exist_ok=True)

        job_id = self._create_job_id()
        job_dir = self.drive_base / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        self._log(f"Creating F5-TTS job: {job_id}")

        # Build request
        settings = spec["settings"]
        items = spec.get("steps") or spec.get("segments", [])

        steps_data = []
        for item in items:
            narration = str(item.get("narration", "")).strip()
            if narration:
                steps_data.append({"id": str(item["id"]), "narration": narration})

        if not steps_data:
            raise ColabTTSError("No narration text found in spec")

        # F5-TTS specific fields
        ref_audio = str(settings.get("f5_ref_audio", ""))
        ref_text = str(settings.get("f5_ref_text", ""))
        seed = settings.get("f5_seed", None)
        nfe_step = int(settings.get("f5_nfe_step", 32))

        # Copy reference audio to job directory if it's a local file
        if ref_audio and Path(ref_audio).expanduser().exists():
            ref_path = Path(ref_audio).expanduser().resolve()
            dest = job_dir / ref_path.name
            shutil.copy2(ref_path, dest)
            ref_audio = ref_path.name  # Just the filename in the job dir
            self._log(f"Copied reference audio: {ref_path.name}")

        request = {
            "ref_audio": ref_audio,
            "ref_text": ref_text,
            "speed": float(settings.get("speech_speed", 1.0)),
            "seed": seed,
            "nfe_step": nfe_step,
            "steps": steps_data,
        }

        request_path = job_dir / "request.json"
        with request_path.open("w", encoding="utf-8") as f:
            json.dump(request, f, indent=2)

        self._log(
            f"Job {job_id}: {len(steps_data)} steps dispatched "
            f"(ref={ref_audio or 'default'}, nfe={nfe_step})"
        )
        self._log(f"Waiting {self.config.sync_delay:.0f}s for Drive sync...")
        time.sleep(self.config.sync_delay)

        step_audio = self._wait_for_completion(job_id, job_dir, audio_dir, items)
        return step_audio


def create_f5_dispatcher_from_args(
    drive_path: str | None = None,
    timeout: float = 600.0,
    poll_interval: float = 5.0,
) -> ColabF5TTSDispatcher:
    """Create an F5-TTS dispatcher from CLI arguments or environment variables."""
    if drive_path is None:
        drive_path = os.environ.get("COLAB_F5_TTS_DRIVE_PATH")

    if drive_path is None:
        candidates = [
            Path.home() / "gdrive" / "autonomous-recording" / "f5-tts-jobs",
            Path.home()
            / "Google Drive"
            / "My Drive"
            / "autonomous-recording"
            / "f5-tts-jobs",
        ]
        for candidate in candidates:
            if candidate.parent.exists():
                drive_path = str(candidate)
                break

        if drive_path is None:
            drive_path = str(
                Path.home() / "gdrive" / "autonomous-recording" / "f5-tts-jobs"
            )

    config = ColabTTSConfig(
        drive_base=Path(drive_path),
        timeout=timeout,
        poll_interval=poll_interval,
    )
    return ColabF5TTSDispatcher(config)
