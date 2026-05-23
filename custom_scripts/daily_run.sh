#!/bin/bash
cd /home/geoace/birdnet
echo "=== $(date) === Starting run ===" >> /home/geoace/birdnet/logs/daily.log

echo "--- Syncing from B2 ---" >> /home/geoace/birdnet/logs/daily.log
docker exec birdnet-tools rclone sync b2:spadefoot/spadefoot-1 /audio/inbox/spadefoot-1 >> /home/geoace/birdnet/logs/daily.log 2>&1

echo "--- Processing audio ---" >> /home/geoace/birdnet/logs/daily.log
/home/geoace/birdnet/custom_scripts/process_all.sh >> /home/geoace/birdnet/logs/daily.log 2>&1

echo "--- Ingesting results ---" >> /home/geoace/birdnet/logs/daily.log
docker compose run --rm -T analyzer python /custom_scripts/ingest_results.py >> /home/geoace/birdnet/logs/daily.log 2>&1

echo "=== $(date) === Run complete ===" >> /home/geoace/birdnet/logs/daily.log