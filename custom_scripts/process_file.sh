#!/bin/bash

trap 'echo "Aborted."; exit 1' INT TERM

OBJECT_NAME="$1"
AUDIO_EXTENSIONS=("opus" "flac" "wav" "mp3" "m4a" "ogg" "aac")
DEVICE_NAME=$(echo "$OBJECT_NAME" | cut -d'/' -f1)
INBOX=/home/geoace/birdnet/audio/inbox/$DEVICE_NAME
WAV_DIR=/home/geoace/birdnet/audio/wav
RESULTS_DIR=/home/geoace/birdnet/results
COMPOSE_FILE=/home/geoace/birdnet/docker-compose.yml
LOG=/home/geoace/birdnet/logs/daily.log

if [ -z "$OBJECT_NAME" ]; then
    echo "Usage: process_file.sh <object_name>" | tee -a $LOG
    exit 1
fi

# ── Get device coords from DB ─────────────────────────────────────────────────
COORDS=$(docker exec birdnet-tools psql \
    "postgresql://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/${PG_DB}" \
    -t -c "SELECT ST_Y(geom::geometry), ST_X(geom::geometry) FROM core.devices WHERE unit_name = '${DEVICE_NAME}'")

LAT=$(echo $COORDS | cut -d'|' -f1 | tr -d ' ')
LON=$(echo $COORDS | cut -d'|' -f2 | tr -d ' ')

if [ -z "$LAT" ] || [ -z "$LON" ]; then
    echo "$(date) WARNING: Could not get coords for $DEVICE_NAME, using defaults" | tee -a $LOG
    LAT=40.1259
    LON=-82.9071
fi

# ── Audio check ───────────────────────────────────────────────────────────────
EXT="${OBJECT_NAME##*.}"
EXT_LOWER=$(echo "$EXT" | tr '[:upper:]' '[:lower:]')
IS_AUDIO=false
for AUD_EXT in "${AUDIO_EXTENSIONS[@]}"; do
    if [ "$EXT_LOWER" = "$AUD_EXT" ]; then
        IS_AUDIO=true
        break
    fi
done

if [ "$IS_AUDIO" = false ]; then
    echo "$(date) Skipping non-audio file: $OBJECT_NAME" | tee -a $LOG
    exit 0
fi

echo "$(date) Processing: $OBJECT_NAME" | tee -a $LOG

# ── Derive paths ──────────────────────────────────────────────────────────────
RELATIVE="${OBJECT_NAME#$DEVICE_NAME/}"
BASENAME_NOEXT="${RELATIVE%.*}"
WAV_FILE="$WAV_DIR/${BASENAME_NOEXT}.wav"
RESULT_CHECK="$RESULTS_DIR/${BASENAME_NOEXT}.BirdNET.selection.table.txt"

if [ -f "$RESULT_CHECK" ]; then
    echo "$(date) Already processed: $RELATIVE" | tee -a $LOG
    exit 0
fi

# ── Sync single file from B2 ─────────────────────────────────────────────────
echo "$(date) Syncing from B2: $OBJECT_NAME" | tee -a $LOG
docker exec birdnet-tools rclone copy \
    "b2:spadefoot/${OBJECT_NAME}" \
    "$INBOX/$(dirname $RELATIVE)" >> $LOG 2>&1

# ── Convert to WAV ────────────────────────────────────────────────────────────
echo "$(date) Converting: $RELATIVE" | tee -a $LOG
mkdir -p "$(dirname $WAV_FILE)"
docker run --rm \
    -v /home/geoace/birdnet/audio:/audio \
    jrottenberg/ffmpeg -y \
    -i "/audio/inbox/${DEVICE_NAME}/${RELATIVE}" \
    -ar 48000 -ac 1 \
    "/audio/wav/${BASENAME_NOEXT}.wav" >> $LOG 2>&1

# ── Analyze ───────────────────────────────────────────────────────────────────
echo "$(date) Analyzing: $RELATIVE" | tee -a $LOG
mkdir -p "$RESULTS_DIR/$(dirname $RELATIVE)"
docker compose -f $COMPOSE_FILE run --rm -T analyzer \
    python -m birdnet_analyzer.analyze \
    --lat $LAT \
    --lon $LON \
    -o "/results/$(dirname $RELATIVE)" \
    "/audio/wav/${BASENAME_NOEXT}.wav" >> $LOG 2>&1

# ── Ingest ────────────────────────────────────────────────────────────────────
echo "$(date) Ingesting: $RELATIVE" | tee -a $LOG
docker compose -f $COMPOSE_FILE run --rm -T analyzer \
    python /custom_scripts/ingest_results.py >> $LOG 2>&1

# ── Cleanup ───────────────────────────────────────────────────────────────────
rm -f "$WAV_FILE"
echo "$(date) Done: $RELATIVE" | tee -a $LOG