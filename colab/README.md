# Colab GPU Offloading

Offload TTS narration generation (and optionally video encoding) to a Google Colab T4 GPU.

## Architecture

```
Local Machine                         Google Colab T4
─────────────                         ───────────────
record-tour.py                        tts_worker.ipynb
  --tts-backend colab                   ├─ Mount Google Drive
  │                                     ├─ Load Kokoro + onnxruntime-gpu
  ├─ Write request.json ──► GDrive ──►  ├─ Watch for jobs
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
# ~/gdrive/autonomous-recording/tts-jobs/
```

**Option B: Google Drive for Desktop (macOS/Windows)**
- Install from https://www.google.com/drive/download/
- The path will be auto-detected, or specify with `--colab-drive-path`

### 2. Colab Notebook Setup

1. Open `colab/tts_worker.ipynb` in Google Colab
2. Set runtime to **GPU → T4** (Runtime → Change runtime type)
3. Run all cells in order (1 through 8)
4. Cell 8 starts the job watcher loop — leave it running

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

## Environment Variable

Instead of `--colab-drive-path`, you can set:

```bash
export COLAB_TTS_DRIVE_PATH=~/gdrive/autonomous-recording/tts-jobs
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
| `tts_worker.ipynb` | Colab notebook — run on T4 GPU |
| `colab_dispatcher.py` | Local-side job dispatch and result collection |
| `__init__.py` | Python package marker |
| `README.md` | This file |
