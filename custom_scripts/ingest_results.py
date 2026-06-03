#!/usr/bin/env python3

import os
import csv
import uuid
import psycopg2
from pathlib import Path
from datetime import datetime, timedelta

RESULTS_DIR = "/results"
INBOX_DIR = "/audio/inbox"
CREATED_BY = "spadefoot"
AUDIO_EXTENSIONS = ["opus", "flac", "wav", "mp3", "m4a", "ogg", "aac"]

# ── Build common name → scientific name lookup ────────────────────────────────
LABELS_FILE = "/usr/local/lib/python3.11/site-packages/birdnet_analyzer/labels/V2.4/BirdNET_GLOBAL_6K_V2.4_Labels_en_uk.txt"
common_to_scientific = {}
with open(LABELS_FILE, "r") as f:
    for line in f:
        line = line.strip()
        if "_" in line:
            sci, common = line.split("_", 1)
            common_to_scientific[common.lower()] = sci

conn = psycopg2.connect(
    host=os.environ["PG_HOST"],
    port=os.environ["PG_PORT"],
    dbname=os.environ["PG_DB"],
    user=os.environ["PG_USER"],
    password=os.environ["PG_PASSWORD"]
)
cur = conn.cursor()

# ── Cache device lookups ──────────────────────────────────────────────────────
device_cache = {}

def get_device(device_name):
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

# ── Process result files ──────────────────────────────────────────────────────
result_files = sorted(Path(RESULTS_DIR).rglob("*.BirdNET.selection.table.txt"))
print(f"Found {len(result_files)} result files")

for result_file in result_files:
    ingested_flag = result_file.with_suffix(".ingested")
    if ingested_flag.exists():
        print(f"Skipping (already ingested): {result_file.name}")
        continue

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
        continue

    unit_id, LON, LAT = get_device(device_name)

    # ── Derive source file extension ──────────────────────────────────────────
    date_folder = recorded_at.strftime('%Y-%m-%d')
    source_ext = "opus"
    for ext in AUDIO_EXTENSIONS:
        candidate = f"{INBOX_DIR}/{device_name}/{date_folder}/{stem}.{ext}"
        if os.path.exists(candidate):
            source_ext = ext
            break

    # ── Ensure activity_log entry exists ─────────────────────────────────────
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
        """, (
            activity_id,
            'acoustic_monitoring',
            unit_id,
            recorded_at.date(),
            f"{stem} | type={rec_type}",
            CREATED_BY
        ))
        conn.commit()

    # ── Ensure media entry exists ─────────────────────────────────────────────
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

    # ── Insert observations ───────────────────────────────────────────────────
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
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    'automated', 'machine',
                    %s,
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                    %s
                )
            """, (
                observation_id, activity_id, media_id, offset_s,
                common_name, scientific_name, confidence,
                detected_at,
                LON, LAT,
                CREATED_BY
            ))
            observations_inserted += 1

    conn.commit()
    ingested_flag.touch()
    print(f"Ingested {observations_inserted} observations from {result_file.name}")

cur.close()
conn.close()
print("Done.")