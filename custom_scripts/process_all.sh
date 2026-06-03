#!/bin/bash
trap 'echo "Aborted."; exit 1' INT TERM

INBOX=/home/geoace/birdnet/audio/inbox
WAV_DIR=/home/geoace/birdnet/audio/wav
RESULTS_DIR=/home/geoace/birdnet/results
COMPOSE_FILE=/home/geoace/birdnet/docker-compose.yml

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
        COORDS=$(docker exec birdnet-tools psql \
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

    echo "Converting: $relative"
    mkdir -p "$(dirname $wav_file)"
    docker run --rm \
        -v /home/geoace/birdnet/audio:/audio \
        jrottenberg/ffmpeg -y \
        -i "/audio/inbox/${device_name}/${file_relative}" \
        -ar 48000 -ac 1 \
        "/audio/wav/${basename_noext}.wav" 2>/dev/null

    echo "Analyzing: $relative"
    mkdir -p "$RESULTS_DIR/$(dirname $file_relative)"
    docker compose -f $COMPOSE_FILE run --rm -T analyzer \
        python -m birdnet_analyzer.analyze \
        --lat $LAT \
        --lon $LON \
        -o "/results/$(dirname $file_relative)" \
        "/audio/wav/${basename_noext}.wav"

    rm -f "$wav_file"
    echo "Done: $relative"
done

echo "All files processed."