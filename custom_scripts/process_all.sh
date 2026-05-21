#!/bin/bash
trap 'echo "Aborted."; exit 1' INT TERM

INBOX=/home/geoace/birdnet/audio/inbox/spadefoot-1
WAV_DIR=/home/geoace/birdnet/audio/wav
RESULTS_DIR=/home/geoace/birdnet/results
COMPOSE_FILE=/home/geoace/birdnet/docker-compose.yml

mapfile -t FILES < <(find $INBOX -name "*.opus" -o -name "*.flac")

echo "Found ${#FILES[@]} files to process"

for opus_file in "${FILES[@]}"; do
    relative="${opus_file#$INBOX/}"
    basename_noext="${relative%.*}"
    wav_file="$WAV_DIR/${basename_noext}.wav"
    result_check="$RESULTS_DIR/${basename_noext}.BirdNET.selection.table.txt"

    if [ -f "$result_check" ]; then
        echo "Skipping (already processed): $relative"
        continue
    fi

    echo "Converting: $relative"
    mkdir -p "$(dirname $wav_file)"
    docker run --rm \
        -v /home/geoace/birdnet/audio:/audio \
        jrottenberg/ffmpeg -y \
        -i "/audio/inbox/spadefoot-1/${relative}" \
        -ar 48000 -ac 1 \
        "/audio/wav/${basename_noext}.wav" 2>/dev/null

    echo "Analyzing: $relative"
    mkdir -p "$RESULTS_DIR/$(dirname $relative)"
    docker compose -f $COMPOSE_FILE run --rm -T analyzer \
            python -m birdnet_analyzer.analyze \
            --lat 40.1259 \
            --lon -82.9071 \
            -o "/results/$(dirname $relative)" \
            "/audio/wav/${basename_noext}.wav"

    rm -f "$wav_file"
    echo "Done: $relative"
done

echo "All files processed."