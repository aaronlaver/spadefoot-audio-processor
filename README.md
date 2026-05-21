# BirdNET-Go Field Audio Processing Stack

Containerized pipeline for processing field bioacoustics recordings from Wasabi S3 through BirdNET-Go species detection.

## Architecture

```
Wasabi S3 (Opus)
    ↓  rclone
/audio/inbox
    ↓  ffmpeg
/audio/wav
    ↓  BirdNET-Go API
SQLite DB + Web UI
```

## Services

- **birdnet-go** — BirdNET-Go species detection, web UI at `http://<host>:8081`
- **tools** — Custom image with rclone + ffmpeg + sqlite3 for audio retrieval and conversion

## First-Time Setup

### 1. Clone and configure

```bash
git clone <repo>
cd birdnet
cp .env.example .env
# Edit .env with your Wasabi credentials
```

### 2. Create directories

```bash
mkdir -p audio/inbox audio/wav
```

### 3. Start services

```bash
docker compose up -d
```

### 4. Configure rclone

```bash
docker exec -it birdnet-tools rclone config
```

Walk through the interactive setup:
- `n` — new remote
- Name: `wasabi`
- Storage: `s3`
- Provider: `Wasabi`
- Enter access key and secret key
- Region: `us-east-2`
- Endpoint: `s3.us-east-2.wasabisys.com`
- Default everything else, `n` to advanced, `y` to confirm

### 5. Configure BirdNET-Go location

Edit `config/config.yaml`:

```yaml
birdnet:
    longitude: -82.9071
    latitude: 40.1259
    locationconfigured: true
```

Then restart:

```bash
docker compose restart birdnet-go
```

## Environment Variables

Copy `.env.example` to `.env` and fill in values:

```
WASABI_ACCESS_KEY=
WASABI_SECRET_KEY=
WASABI_REGION=us-east-2
WASABI_ENDPOINT=s3.us-east-2.wasabisys.com
WASABI_BUCKET=spadefoot
TZ=America/New_York
```

## Testing the Pipeline

### Test bucket connectivity

```bash
docker exec -it birdnet-tools rclone ls wasabi:spadefoot --max-depth 1
```

### Pull a single file from Wasabi

```bash
docker exec -it birdnet-tools rclone copy \
  "wasabi:spadefoot/spadefoot-1/2026-05-17/spadefoot-1_20260517_204200_dusk.opus" \
  /audio/inbox
```

### Convert Opus to WAV

```bash
docker run --rm \
  -v ~/birdnet/audio:/audio \
  jrottenberg/ffmpeg \
  -i /audio/inbox/spadefoot-1_20260517_204200_dusk.opus \
  -ar 48000 -ac 1 \
  /audio/wav/spadefoot-1_20260517_204200_dusk.wav
```

### Submit to BirdNET-Go for analysis

```bash
docker exec -it birdnet-go wget -q -O - \
  --post-file=/audio/wav/spadefoot-1_20260517_204200_dusk.wav \
  http://localhost:8080/api/v1/analyze
```

### Query detections

```bash
docker exec -it birdnet-tools sqlite3 /data/birdnet.db \
  "SELECT datetime(d.detected_at, 'unixepoch'), l.scientific_name, d.confidence \
   FROM detections d JOIN labels l ON d.label_id = l.id \
   ORDER BY d.detected_at DESC LIMIT 20;"
```

## Bucket Structure

```
spadefoot/
└── spadefoot-1/
    └── YYYY-MM-DD/
        └── spadefoot-1_YYYYMMDD_HHMMSS_<type>.opus
```

File types: `hourly`, `dawn`, `dusk`, `test`