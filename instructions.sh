
# If needed, make directories
mkdir /home/geoace/birdnet/audio
mkdir /home/geoace/birdnet/audio/wav

docker compose up -d

docker exec -it birdnet-tools rclone config
# Go through walkthrough

# Walk through:

# n — new remote
# Name: wasabi
# Storage: 4 (s3)
# Provider: 48 (Wasabi)
# 1 (next step credentials, then...)
# Access key: DIISSWY01DX4BS6J3L1J
# secret key: 66ut7zvCSWVwBLAZCtecQvLW3leRyyLjoTmjvNVE
# Region: us-east-2
# Endpoint: s3.us-east-2.wasabisys.com
# Default everything else, n to advanced, y to confirm

################### TESTING START ###############

# Test Bucket Connectivity
docker exec -it birdnet-tools rclone ls wasabi:spadefoot --max-depth 1

# Copy single file from wasabi
docker exec -it birdnet-tools rclone copy \
  "wasabi:spadefoot/spadefoot-1/2026-05-17/spadefoot-1_20260517_204200_dusk.opus" \
  /audio/inbox


# convert to birdnet friendly format WAV
docker run --rm \
  -v ~/birdnet/audio:/audio \
  jrottenberg/ffmpeg \
  -i /audio/inbox/spadefoot-1_20260517_204200_dusk.opus \
  -ar 48000 -ac 1 \
  /audio/wav/spadefoot-1_20260517_204200_dusk.wav

# Copy full audio library from Wasabi
docker exec -it birdnet-tools rclone copy \
  wasabi:spadefoot/spadefoot-1 \
  /audio/inbox/spadefoot-1 \
  --progress

# BATCH PROCESSING
# Install screen if needed
sudo apt install screen -y

# Make script executable
chmod +x ~/birdnet/custom_scripts/process_all.sh


docker compose down
docker compose up -d
# Start screen session and kick off bulk processing
screen -S birdnet
~/birdnet/custom_scripts/process_all.sh
# Detach: Ctrl+A then D

# Add observations to postgres:
docker compose run --rm -T analyzer python /custom_scripts/ingest_results.py

# If you need to wipe already ingested flags:
# find ~/birdnet/results -name "*.ingested" -delete
