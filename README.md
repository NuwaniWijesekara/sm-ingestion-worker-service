# ScanMe — Ingestion Worker

A background worker with no HTTP port. Listens on a Redis Stream for new event jobs, downloads photos from Google Drive, extracts ArcFace face embeddings, uploads photos and thumbnails to AWS S3, and saves results to PostgreSQL.

**Port:** None — this is a pure background process  
**Stack:** Python · Redis Streams · InsightFace ArcFace · Google Drive API · AWS S3 · PostgreSQL

---

## What This Worker Does

1. **Listens** on Redis Stream `photo.ingest` using `XREADGROUP`
2. **Downloads** all images from the photographer's Google Drive folder
3. **Strips EXIF** metadata and uploads original photo to S3
4. **Creates thumbnail** (400×400) and uploads to S3
5. **Extracts face embeddings** using InsightFace ArcFace (512-dimensional vectors)
6. **Saves** S3 URLs + embedding to PostgreSQL `images` table
7. **Updates** event status from `PROCESSING` → `READY` (or `FAILED`)
8. **ACKs** the Redis message only on success — failed messages are redelivered on restart

### Message flow

```
Photographer Service
        ↓ XADD photo.ingest {event_id, drive_url}
    Redis Stream
        ↓ XREADGROUP
    Ingestion Worker
        ↓ downloads from Google Drive
        ↓ uploads to S3
        ↓ extracts embeddings
        ↓ writes to PostgreSQL
        ↓ XACK (marks message complete)
```

---

## Project Structure

```
sm-ingestion-worker/
├── app/
│   ├── worker.py            # Redis Stream consumer loop — entrypoint
│   ├── models.py            # SQLAlchemy: Event, Image (read + write)
│   ├── database.py          # Engine, SessionLocal
│   ├── services/
│   │   ├── drive.py         # Google Drive API — list and download images
│   │   ├── s3.py            # S3 upload, thumbnail creation, EXIF strip
│   │   └── face_engine.py   # InsightFace ArcFace singleton
│   └── config/
│       └── settings.py      # Pydantic settings loaded from .env
├── requirements.txt
├── Dockerfile
└── .env
```

---

## Environment Variables

Create a `.env` file in the root of this service:

```bash
# PostgreSQL
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/scanme_db

# Redis — stream name must match STREAM_NAME in photographer service
REDIS_URL=redis://localhost:6379/0
STREAM_NAME=photo.ingest
CONSUMER_GROUP=ingestion-workers
CONSUMER_NAME=worker-1          # unique per replica — change if running multiple workers

# AWS S3
AWS_ACCESS_KEY_ID=your_aws_access_key_id
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key
AWS_REGION=eu-north-1
S3_BUCKET_NAME=your-scanme-bucket-name

# Google Drive (API Key — not OAuth)
# Create at: console.cloud.google.com → APIs & Services → Credentials → API Key
# Enable: Google Drive API
GOOGLE_DRIVE_API_KEY=your_google_drive_api_key
```

> **Note:** `STREAM_NAME` must be identical to the `STREAM_NAME` set in the Photographer Service `.env`.

---

## Running Manually (Local Development)

### Prerequisites

- Python 3.11+
- PostgreSQL running with `pgvector` extension
- Redis running
- Virtual environment activated
- AWS credentials with S3 write access
- Google Drive API key with Drive API enabled

### Steps

```bash
# 1. Clone and enter the service
cd sm-ingestion-worker

# 2. Create and activate virtual environment
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create .env file and fill in all values
copy .env.example .env     # Windows
cp .env.example .env       # macOS/Linux

# 5. Run the worker
python -m app.worker
```

### Expected output on successful start

```
INFO: Loading ArcFace model...
INFO: InsightFace model loaded
INFO: ✓ Ingestion worker started — listening on Redis Stream
INFO: Consumer group already exists
# ... worker sits here silently waiting for messages
```

The worker will stay running and process jobs as they arrive. It does not exit between jobs.

---

## Running with Docker

### GPU (recommended for production)

```bash
# Build
docker build -t scanme-ingestion-worker .

# Run with GPU
docker run --env-file .env --gpus all scanme-ingestion-worker
```

### CPU only (development)

Change `onnxruntime-gpu` to `onnxruntime` in `requirements.txt` first, then:

```bash
docker build -t scanme-ingestion-worker .
docker run --env-file .env scanme-ingestion-worker
```

> There is no `-p` port mapping — this container has no HTTP server.

### Run with infrastructure

```bash
# Start PostgreSQL and Redis first
docker-compose -f docker-compose-infra.yml up -d

# Then run the worker
docker run --env-file .env --network host scanme-ingestion-worker
```

---

## Scaling

To process multiple events in parallel, run multiple worker containers with different `CONSUMER_NAME` values. Redis consumer groups ensure each message is delivered to exactly one worker:

```bash
# Worker 1
docker run --env-file .env -e CONSUMER_NAME=worker-1 --gpus all scanme-ingestion-worker

# Worker 2 (different consumer name, same group)
docker run --env-file .env -e CONSUMER_NAME=worker-2 --gpus all scanme-ingestion-worker
```

Each worker needs its own GPU or VRAM allocation.

---

## Dependencies

| Package | Purpose |
|---|---|
| `sqlalchemy` | ORM — writes Image rows, updates Event status |
| `psycopg2-binary` | PostgreSQL driver |
| `pgvector` | Stores 512-d embedding in `images.face_embedding` |
| `redis` | `XREADGROUP` + `XACK` stream consumer |
| `google-api-python-client` | Drive API: list files, download to memory |
| `boto3` | S3: upload photo + thumbnail |
| `insightface` | ArcFace: extract all face embeddings per photo |
| `onnxruntime-gpu` | GPU inference (swap to `onnxruntime` for CPU) |
| `opencv-python-headless` | `cv2.cvtColor` RGB→BGR conversion |
| `Pillow` | Image open, thumbnail resize, EXIF strip |
| `numpy` | Embedding L2 normalization |
| `pydantic-settings` | `.env` → Settings class |

> `fastapi`, `uvicorn`, and `python-jose` are intentionally excluded — this worker has no HTTP server and never issues tokens.

---

## Error Handling

- If a single photo fails (download error, no face, S3 error), it is skipped and the worker continues with the remaining photos
- If the entire event fails, the event status is set to `FAILED` and the exception is logged
- Messages are only ACKed (`XACK`) on full success — if the worker crashes mid-job, Redis redelivers the message on next startup
- Redis connection drops are caught and retried after 5 seconds

---

## Google Drive Setup

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project or select existing
3. Enable **Google Drive API**
4. Go to **APIs & Services → Credentials → Create Credentials → API Key**
5. Copy the key to `GOOGLE_DRIVE_API_KEY` in `.env`
6. The photographer's Drive folder must be set to **"Anyone with the link can view"**

---

## AWS S3 Setup

1. Create an S3 bucket in your chosen region
2. Create an IAM user with `AmazonS3FullAccess` or a scoped policy for your bucket
3. Generate access keys and add to `.env`
4. Set bucket CORS policy to allow reads from your frontend domain

---

## Related Services

| Service | Repo | Description |
|---|---|---|
| Photographer Service | `scanme-photographer` | Publishes jobs to this worker via Redis |
| Guest Service | `scanme-guest` | Reads embeddings written by this worker |
| API Gateway | `scanme-gateway` | Nginx routing — not used by this worker |
| Frontend | `scanme-frontend` | Next.js app — not directly related |
