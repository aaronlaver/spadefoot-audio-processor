#!/bin/bash

INBOX=/home/geoace/birdnet/audio/inbox/spadefoot-1
WAV_DIR=/home/geoace/birdnet/audio/wav
RESULTS_DIR=/home/geoace/birdnet/results
COMPOSE_FILE=/home/geoace/birdnet/docker-compose.yml

find $INBOX -name "*.opus" | while read opus_file; do
    relative="${opus_file#$INBOX/}"
    wav_file="$WAV_DIR/${relative%.opus}.wav"
    result_check="$RESULTS_DIR/${relative%.opus}.BirdNET.selection.table.txt"

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
        "/audio/wav/${relative%.opus}.wav" 2>/dev/null

    echo "Analyzing: $relative"
    mkdir -p "$RESULTS_DIR/$(dirname $relative)"
    docker compose -f $COMPOSE_FILE run --rm analyzer \
        python -m birdnet_analyzer.analyze \
        --lat 40.1259 \
        --lon -82.9071 \
        -o "$RESULTS_DIR/$(dirname $relative)" \
        "/audio/wav/${relative%.opus}.wav"

    rm -f "$wav_file"
    echo "Done: $relative"
done

echo "All files processed."