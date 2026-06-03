
# If needed, make directories
mkdir /home/geoace/birdnet/audio
mkdir /home/geoace/birdnet/audio/wav

docker compose up -d

docker exec -it birdnet-tools rclone config
# Go through walkthrough

# Walk through:

# n — new remote
# Name: wasabi
# Storage: 5 (backblaze)
# 1 (next step credentials, then...)
#B2_ACCESS_KEY=005581ae08ceb7b0000000001
#B2_SECRET_KEY=K005gBKw5lYX3BkVOdjwQhcOHGQhgbQ
#B2_REGION=us-east-005
#B2_ENDPOINT=s3.us-east-005.backblazeb2.com
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

# Finally, set up the daily script. 
mkdir -p ~/birdnet/logs
chmod +x ~/birdnet/custom_scripts/daily_run.sh

crontab -e 
# paste this line in:
0 14 * * * /home/geoace/birdnet/custom_scripts/daily_run.sh

# Test
~/birdnet/custom_scripts/daily_run.sh

# Set up networking for B2 communication via cloudflare
# Install cloudflared on geoace-2
curl -L https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared -y
cloudflared tunnel login
cloudflared tunnel create spadefoot-analyzer # Copy the .json path for use below

nano ~/.cloudflared/config.yml

# Then Paste this in:
# START FILE
tunnel: 1d2b3fa6-47a9-4752-a507-ce8e53ed8bf1
credentials-file: /home/geoace/.cloudflared/1d2b3fa6-47a9-4752-a507-ce8e53ed8bf1.json # put path here a

ingress:
  - hostname: analyze.habitrax.net # change as appropriate
    service: http://localhost:5000
  - service: http_status:404

# END FILE

cloudflared tunnel route dns spadefoot-analyzer analyze.habitrax.net

# Install as a service so it survives reboots
sudo mkdir -p /etc/cloudflared
sudo cp ~/.cloudflared/config.yml /etc/cloudflared/
sudo cp ~/.cloudflared/1d2b3fa6-47a9-4752-a507-ce8e53ed8bf1.json /etc/cloudflared/

sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
sudo systemctl status cloudflared

# Set up flask receiver
mkdir -p ~/birdnet/receiver

# Fill out B2 Webhook Secret Env variable, then...
docker compose up -d --build receiver

docker ps # check to make sure it's working

curl -s https://analyze.habitrax.net/health
# should get: {"status":"ok"}