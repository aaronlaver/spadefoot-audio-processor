#!/usr/bin/env python3

import os
import csv
import uuid
import hmac
import hashlib
import subprocess
import psycopg2
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

app = Flask(__name__)

INBOX_DIR = "/audio/inbox"
WAV_DIR = "/audio/wav"
RESULTS_DIR = "/results"
CREATED_BY = "spadefoot"
AUDIO_EXTENSIONS = ["opus", "flac", "wav", "mp3", "m4a", "ogg", "aac"]
LABELS_FILE = "/usr/local/lib/python3.11/site-packages/birdnet_analyzer/labels/V2.4/BirdNET_GLOBAL_6K_V2.4_Labels_en_uk.txt"

# ── Load labels once at startup ───────────────────────────────────────────────
common_to_scientific = {}
with open(LABELS_FILE, "r") as f:
    for line in f:
        line = line.strip()
        if "_" in line:
            sci, common = line.split("_", 1)
            common_to_scientific[common.lower()] = sci

def get_db():
    return psycopg2.connect(
        host=os.environ["PG_HOST"],
        port=os.environ["PG_PORT"],
        dbname=os.environ["PG_DB"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"]
    )

device_cache = {}

def get_device(cur, conn, device_name):
    if device_name in device_cache:
        return device_cache[device_name]
    cur.execute("""
        SELECT unit_id, ST_X(geom::geometry), ST_Y(geom::geometry)
        FROM core.devices WHERE unit_name = %s
    """, (device_name,))
    row = cur.fetchone()
    if row:
        unit_id, lon, lat = row
    else:
        unit_id = str(uuid.uuid4())
        lon, lat = -82.9071, 40.1259
        cur.execute("""
            INSERT INTO core.devices
                (unit_id, unit_name, device_type, created_by, geom)
            VALUES (%s, %s, 'acoustic_recorder', %s,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326))
        """, (unit_id, device_name, CREATED_BY, lon, lat))
        conn.commit()
        print(f"Registered device {device_name} as {unit_id}")
    device_cache[device_name] = (unit_id, lon, lat)
    return unit_id, lon, lat

def ingest(result_file, conn, cur):
    stem = result_file.stem.replace(".BirdNET.selection.table", "")
    parts = stem.split("_")
    try:
        device_name = parts[0]
        date_str = parts[1]
        time_str = parts[2]
        rec_type = parts[3]
        recorded_at = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
    except (IndexError, ValueError) as e:
        print(f"Could not parse filename {result_file.name}: {e}")
        return 0

    unit_id, LON, LAT = get_device(cur, conn, device_name)

    date_folder = recorded_at.strftime('%Y-%m-%d')
    source_ext = "opus"
    for ext in AUDIO_EXTENSIONS:
        candidate = f"{INBOX_DIR}/{device_name}/{date_folder}/{stem}.{ext}"
        if os.path.exists(candidate):
            source_ext = ext
            break

    cur.execute("""
        SELECT activity_id FROM core.activity_log
        WHERE unit_id = %s AND date = %s AND comments LIKE %s
    """, (unit_id, recorded_at.date(), f"%{stem}%"))
    row = cur.fetchone()
    if row:
        activity_id = row[0]
    else:
        activity_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO core.activity_log
                (activity_id, activity_type, unit_id, date, comments, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (activity_id, 'acoustic_monitoring', unit_id,
              recorded_at.date(), f"{stem} | type={rec_type}", CREATED_BY))
        conn.commit()

    media_url = f"https://s3.us-east-005.backblazeb2.com/spadefoot/{device_name}/{date_folder}/{stem}.{source_ext}"
    media_key = f"{device_name}/{date_folder}/{stem}.{source_ext}"
    cur.execute("SELECT media_id FROM core.media WHERE media_url = %s", (media_url,))
    row = cur.fetchone()
    if row:
        media_id = row[0]
    else:
        media_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO core.media
                (media_id, activity_id, media_url, media_key, media_type, media_use, created_by)
            VALUES (%s, %s, %s, %s, 'audio', 'source', %s)
        """, (media_id, activity_id, media_url, media_key, CREATED_BY))
        conn.commit()

    observations_inserted = 0
    with open(result_file, "r") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            common_name = row.get("Common Name", "").strip()
            common_name_lower = common_name.lower()
            scientific_name = common_to_scientific.get(common_name_lower, "")
            confidence = float(row.get("Confidence", 0))
            offset_s = float(row.get("Begin Time (s)", 0))
            if common_name_lower == "nocall":
                continue
            detected_at = recorded_at + timedelta(seconds=offset_s)
            observation_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO core.observations
                    (observation_id, activity_id, media_id, media_offset,
                     machine_common_name, machine_scientific_name,
                     machine_confidence, mode_of_entry, mode_of_identification,
                     detected_at, geom, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s,
                    'automated', 'machine', %s,
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s)
            """, (observation_id, activity_id, media_id, offset_s,
                  common_name, scientific_name, confidence,
                  detected_at, LON, LAT, CREATED_BY))
            observations_inserted += 1

    conn.commit()
    result_file.with_suffix(".ingested").touch()
    return observations_inserted

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/process", methods=["POST"])
def process():
    data = request.get_json()
    object_name = data.get("filename", "")

    if not object_name:
        return jsonify({"error": "filename required"}), 400

    # ── Audio check ───────────────────────────────────────────────────────────
    ext = object_name.rsplit(".", 1)[-1].lower()
    if ext not in AUDIO_EXTENSIONS:
        return jsonify({"status": "skipped", "reason": "not audio"}), 200

    device_name = object_name.split("/")[0]
    relative = "/".join(object_name.split("/")[1:])
    basename_noext = relative.rsplit(".", 1)[0]
    wav_file = f"{WAV_DIR}/{basename_noext}.wav"
    result_check = Path(f"{RESULTS_DIR}/{basename_noext}.BirdNET.selection.table.txt")

    if result_check.exists():
        return jsonify({"status": "skipped", "reason": "already processed"}), 200

    # ── Sync from B2 ─────────────────────────────────────────────────────────
    inbox_dir = f"{INBOX_DIR}/{device_name}/{'/'.join(relative.split('/')[:-1])}"
    os.makedirs(inbox_dir, exist_ok=True)
    print(f"Syncing: {object_name}")
    subprocess.run([
        "rclone", "copy",
        "--config", "/config/rclone/rclone.conf",
        f"b2:spadefoot/{object_name}",
        inbox_dir
    ], check=True)

    # ── Convert to WAV ────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(wav_file), exist_ok=True)
    print(f"Converting: {relative}")
    subprocess.run([
        "ffmpeg", "-y",
        "-i", f"{INBOX_DIR}/{device_name}/{relative}",
        "-ar", "48000", "-ac", "1",
        wav_file
    ], check=True, capture_output=True)

    # ── Get device coords ─────────────────────────────────────────────────────
    conn = get_db()
    cur = conn.cursor()
    _, LON, LAT = get_device(cur, conn, device_name)

    # ── Analyze ───────────────────────────────────────────────────────────────
    result_dir = f"{RESULTS_DIR}/{'/'.join(relative.split('/')[:-1])}"
    os.makedirs(result_dir, exist_ok=True)
    print(f"Analyzing: {relative}")
    subprocess.run([
        "python", "-m", "birdnet_analyzer.analyze",
        "--lat", str(LAT),
        "--lon", str(LON),
        "-o", result_dir,
        wav_file
    ], check=True)

    # ── Ingest ────────────────────────────────────────────────────────────────
    print(f"Ingesting: {relative}")
    observations = ingest(result_check, conn, cur)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    os.remove(wav_file)
    cur.close()
    conn.close()

    print(f"Done: {relative} — {observations} observations")
    return jsonify({
        "status": "ok",
        "file": object_name,
        "observations": observations
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)