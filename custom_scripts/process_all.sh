#!/bin/bash
trap 'echo "Aborted."; exit 1' INT TERM

INBOX=/home/geoace/birdnet/audio/inbox
WAV_DIR=/home/geoace/birdnet/audio/wav
RESULTS_DIR=/home/geoace/birdnet/results

mapfile -t FILES < <(find $INBOX \( -name "*.opus" -o -name "*.flac" -o -name "*.wav" -o -name "*.mp3" -o -name "*.m4a" -o -name "*.ogg" -o -name "*.aac" \))
echo "Found ${#FILES[@]} files to process"

declare -A DEVICE_COORDS

for audio_file in "${FILES[@]}"; do
    relative="${audio_file#$INBOX/}"
    device_name=$(echo "$relative" | cut -d'/' -f1)
    file_relative="${relative#$device_name/}"
    basename_noext="${file_relative%.*}"
    wav_file="$WAV_DIR/${basename_noext}.wav"
    result_check="$RESULTS_DIR/${basename_noext}.BirdNET.selection.table.txt"

    if [ -f "$result_check" ]; then
        echo "Skipping (already processed): $relative"
        continue
    fi

    # ── Get coords (cached per device) ───────────────────────────────────────
    if [ -z "${DEVICE_COORDS[$device_name]+x}" ]; then
        COORDS=$(docker exec birdnet-analyzer psql \
            "postgresql://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/${PG_DB}" \
            -t -c "SELECT ST_Y(geom::geometry), ST_X(geom::geometry) FROM core.devices WHERE unit_name = '${device_name}'")
        LAT=$(echo $COORDS | cut -d'|' -f1 | tr -d ' ')
        LON=$(echo $COORDS | cut -d'|' -f2 | tr -d ' ')
        if [ -z "$LAT" ] || [ -z "$LON" ]; then
            echo "WARNING: Could not get coords for $device_name, using defaults"
            LAT=40.1259
            LON=-82.9071
        fi
        DEVICE_COORDS[$device_name]="$LAT $LON"
    else
        LAT=$(echo ${DEVICE_COORDS[$device_name]} | cut -d' ' -f1)
        LON=$(echo ${DEVICE_COORDS[$device_name]} | cut -d' ' -f2)
    fi

    # ── Convert to WAV ────────────────────────────────────────────────────────
    echo "Converting: $relative"
    mkdir -p "$(dirname $wav_file)"
    docker exec birdnet-analyzer ffmpeg -y \
        -i "/audio/inbox/${device_name}/${file_relative}" \
        -ar 48000 -ac 1 \
        "/audio/wav/${basename_noext}.wav" 2>/dev/null

    # ── Analyze ───────────────────────────────────────────────────────────────
    echo "Analyzing: $relative"
    mkdir -p "$RESULTS_DIR/$(dirname $file_relative)"
    docker exec birdnet-analyzer python -m birdnet_analyzer.analyze \
        --lat $LAT \
        --lon $LON \
        -o "/results/$(dirname $file_relative)" \
        "/audio/wav/${basename_noext}.wav"

    rm -f "$wav_file"
    echo "Done: $relative"
done

echo "All files processed."

# ── Ingest and enrich ─────────────────────────────────────────────────────────
echo "--- Ingesting results ---"
docker exec birdnet-analyzer python /custom_scripts/ingest_results.py

echo "--- Enriching species ---"
docker exec birdnet-analyzer python -c "
import psycopg2, os, sys
sys.path.insert(0, '/app')
from api import enrich_all, get_db
conn = get_db()
cur = conn.cursor()
enrich_all(cur, conn)
cur.close()
conn.close()
"

echo "All done."