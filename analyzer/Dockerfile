FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    unzip \
    && curl -O https://downloads.rclone.org/rclone-current-linux-amd64.zip \
    && unzip rclone-current-linux-amd64.zip \
    && mv rclone-*-linux-amd64/rclone /usr/local/bin/ \
    && rm -rf rclone-* \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir birdnet-analyzer psycopg2-binary flask

WORKDIR /app
COPY api.py .

CMD ["python", "api.py"]