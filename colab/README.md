# Colab GPU Offloading

Offload TTS narration generation to a Google Colab T4 GPU. Supports two TTS backends:

- **Kokoro** (`--tts-backend colab`): Fast, lightweight TTS. Same model as local, but on GPU.
- **F5-TTS** (`--tts-backend colab-f5`): Voice cloning TTS. Uses a reference audio clip to clone any voice. Slower but much more realistic.

## Architecture

```
Local Machine                         Google Colab T4
─────────────                         ───────────────
record-tour.py                        tts_worker.ipynb (Kokoro)
  --tts-backend colab                  OR f5_tts_worker.ipynb (F5-TTS)
  │                                     ├─ Mount Google Drive
  ├─ Write request.json ──► GDrive ──►  ├─ Load model (cached on Drive)
  ├─ Wait for done.marker               ├─ Generate WAVs (GPU)
  ├─ Copy WAVs from Drive  ◄── GDrive ◄─┘ Write done.marker
  ├─ Playwright capture (local)
  └─ FFmpeg assembly (local)
```

## Setup

### 1. Google Drive Sync (Local Machine)

You need Google Drive accessible as a local filesystem directory. Options:

**Option A: rclone (recommended for Linux)**
```bash
# Install rclone
sudo pacman -S rclone  # Arch
# or: sudo apt install rclone

# Configure Google Drive remote
rclone config
# → New remote → name: gdrive → type: Google Drive → follow OAuth flow

# Mount (run in background)
mkdir -p ~/gdrive
rclone mount gdrive: ~/gdrive --vfs-cache-mode writes --daemon

# The job directory will be at:
# ~/gdrive/autonomous-recording/tts-jobs/     (Kokoro)
# ~/gdrive/autonomous-recording/f5-tts-jobs/   (F5-TTS)
```

**Option B: Google Drive for Desktop (macOS/Windows)**
- Install from https://www.google.com/drive/download/
- The path will be auto-detected, or specify with `--colab-drive-path`

### 2. Colab Notebook Setup

**For Kokoro** (`--tts-backend colab`):
1. Open `colab/tts_worker.ipynb` in Google Colab
2. Set runtime to **GPU → T4** (Runtime → Change runtime type)
3. Run all cells — the last cell starts the job watcher loop

**For F5-TTS** (`--tts-backend colab-f5`):
1. Open `colab/f5_tts_worker.ipynb` in Google Colab
2. Set runtime to **GPU → T4**
3. Upload your reference voice WAV to `~/gdrive/autonomous-recording/voice-refs/`
4. Run all cells — the last cell starts the F5-TTS job watcher

### 3. Run the Pipeline

```bash
# Traditional steps workflow
python record-tour.py tutorial.json --tts-backend colab

# With custom Drive path
python record-tour.py tutorial.json \
  --tts-backend colab \
  --colab-drive-path ~/gdrive/autonomous-recording/tts-jobs

# With longer timeout (for many steps)
python record-tour.py tutorial.json \
  --tts-backend colab \
  --colab-timeout 900

# Dry run (TTS only, no recording)
python record-tour.py tutorial.json \
  --tts-backend colab \
  --dry-run
```

### F5-TTS Voice Cloning

```bash
# F5-TTS with voice cloning
python record-tour.py tutorial.json --tts-backend colab-f5

# With custom Drive path for F5 jobs
python record-tour.py tutorial.json \
  --tts-backend colab-f5 \
  --colab-drive-path ~/gdrive/autonomous-recording/f5-tts-jobs
```

## Environment Variables

Instead of `--colab-drive-path`, you can set:

```bash
# For Kokoro backend
export COLAB_TTS_DRIVE_PATH=~/gdrive/autonomous-recording/tts-jobs

# For F5-TTS backend
export COLAB_F5_TTS_DRIVE_PATH=~/gdrive/autonomous-recording/f5-tts-jobs
```

## How It Works

### Job Protocol

1. **Local** creates `tts-jobs/<job-id>/request.json`:
   ```json
   {
     "voice": "am_michael",
     "speed": 1.0,
     "language": "en-us",
     "steps": [
       {"id": "step-01", "narration": "Welcome to this tutorial..."},
       {"id": "step-02", "narration": "First, let's create a file..."}
     ]
   }
   ```

2. **Colab** detects the job, generates WAVs to `tts-jobs/<job-id>/audio/`:
   ```
   audio/step-step-01.wav
   audio/step-step-02.wav
   ```

3. **Colab** writes `tts-jobs/<job-id>/done.marker` with metadata:
   ```json
   {
     "status": "completed",
     "total_duration": 45.2,
     "steps_generated": 12,
     "timestamp": "2025-01-15T10:30:00Z"
   }
   ```

4. **Local** detects `done.marker`, copies WAVs to the work directory, continues pipeline.

### F5-TTS Job Protocol

Same structure, different request fields and separate jobs directory (`f5-tts-jobs/`):

1. **Local** creates `f5-tts-jobs/<job-id>/request.json`:
   ```json
   {
     "ref_audio": "teacher-voice.wav",
     "ref_text": "Transcription of the reference audio.",
     "speed": 1.0,
     "seed": 42,
     "nfe_step": 32,
     "steps": [
       {"id": "step-01", "narration": "Welcome to this tutorial..."},
       {"id": "step-02", "narration": "First, let's create a file..."}
     ]
   }
   ```
   If `ref_audio` is a local file path, it's copied into the job directory automatically.

2. **Colab** generates WAVs using the reference voice, writes `done.marker` when complete.

### F5-TTS Spec Settings

Add these optional fields to your spec's `settings` object:

```json
{
  "settings": {
    "f5_ref_audio": "~/voice-samples/my-voice.wav",
    "f5_ref_text": "This is a sample of my voice reading a sentence.",
    "f5_seed": 42,
    "f5_nfe_step": 32
  }
}
```

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `f5_ref_audio` | Yes (for F5) | — | Path to reference voice WAV (10-15s recommended) |
| `f5_ref_text` | Yes (for F5) | — | Exact transcription of the reference audio |
| `f5_seed` | No | random | Seed for reproducible output |
| `f5_nfe_step` | No | 32 | Flow-matching steps (higher = better quality, slower. Range: 1-128) |

### Preparing Reference Audio

For best F5-TTS results:
- Record **10-15 seconds** of clear speech
- Use a quiet environment (no background noise)
- Speak naturally in the tone you want for tutorials
- Save as WAV (16kHz+ sample rate)
- Transcribe the audio exactly into `f5_ref_text`

### Error Handling

- If Colab fails, it writes `error.marker` with details
- Local raises `ColabTTSError` with the error message
- Timeout default: 600s (configurable with `--colab-timeout`)

## Colab Limitations

| Limitation | Free Tier | Colab Pro |
|------------|-----------|-----------|
| Max session | ~12 hours | ~24 hours |
| Idle timeout | ~90 min | ~90 min (longer with activity) |
| GPU throttling | After heavy use | Less aggressive |
| Storage | 15 GB Drive | 100+ GB Drive |

**Tips:**
- Keep the Colab tab open (prevents idle disconnection)
- The model is cached on Drive — subsequent runs skip download
- Use `--colab-timeout` for large specs with many steps

## Files

| File | Description |
|------|-------------|
| `tts_worker.ipynb` | Kokoro GPU Colab notebook |
| `f5_tts_worker.ipynb` | F5-TTS voice cloning Colab notebook |
| `colab_dispatcher.py` | Local-side job dispatch (`ColabTTSDispatcher` + `ColabF5TTSDispatcher`) |
| `__init__.py` | Python package marker |
| `README.md` | This file |
