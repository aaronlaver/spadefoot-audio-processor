#!/usr/bin/env python3

import os
import csv
import uuid
import psycopg2
from pathlib import Path
from datetime import datetime, timezone

RESULTS_DIR = "/results"
DEVICE_NAME = "spadefoot-1"
LAT = 40.1259
LON = -82.9071
CREATED_BY = "spadefoot"

conn = psycopg2.connect(
    host=os.environ["PG_HOST"],
    port=os.environ["PG_PORT"],
    dbname=os.environ["PG_DB"],
    user=os.environ["PG_USER"],
    password=os.environ["PG_PASSWORD"]
)
cur = conn.cursor()

# ── Ensure device exists ──────────────────────────────────────────────────────
cur.execute("SELECT unit_id FROM core.devices WHERE unit_name = %s", (DEVICE_NAME,))
row = cur.fetchone()
if row:
    unit_id = row[0]
else:
    unit_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO core.devices (unit_id, unit_name, device_type, created_by)
        VALUES (%s, %s, 'acoustic_monitor', %s)
    """, (unit_id, DEVICE_NAME, CREATED_BY))
    conn.commit()
    print(f"Registered device {DEVICE_NAME} as {unit_id}")

# ── Process result files ──────────────────────────────────────────────────────
result_files = sorted(Path(RESULTS_DIR).rglob("*.BirdNET.selection.table.txt"))
print(f"Found {len(result_files)} result files")

for result_file in result_files:
    ingested_flag = result_file.with_suffix(".ingested")
    if ingested_flag.exists():
        print(f"Skipping (already ingested): {result_file.name}")
        continue

    # Parse filename: spadefoot-1_YYYYMMDD_HHMMSS_type
    stem = result_file.stem.replace(".BirdNET.selection.table", "")
    parts = stem.split("_")
    try:
        date_str = parts[1]   # 20260517
        time_str = parts[2]   # 204200
        rec_type = parts[3]   # dusk / hourly / dawn
        recorded_at = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
    except (IndexError, ValueError) as e:
        print(f"Could not parse filename {result_file.name}: {e}")
        continue

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
    opus_url = f"wasabi:spadefoot/spadefoot-1/{recorded_at.strftime('%Y-%m-%d')}/{stem}.opus"
    cur.execute("SELECT media_id FROM core.media WHERE media_url = %s", (opus_url,))
    row = cur.fetchone()

    if row:
        media_id = row[0]
    else:
        media_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO core.media
                (media_id, activity_id, media_url, media_type, media_use, created_by)
            VALUES (%s, %s, %s, 'audio', 'source', %s)
        """, (media_id, activity_id, opus_url, CREATED_BY))
        conn.commit()

    # ── Insert observations ───────────────────────────────────────────────────
    observations_inserted = 0
    with open(result_file, "r") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            common_name = row.get("Common Name", "").strip()
            scientific_name = row.get("Species Code", "").strip()
            confidence = float(row.get("Confidence", 0))
            offset_s = float(row.get("Begin Time (s)", 0))

            if common_name.lower() == "nocall":
                continue

            observation_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO core.observations
                    (observation_id, activity_id, media_id, media_offset,
                     machine_common_name, machine_scientific_name,
                     machine_confidence, mode_of_entry, mode_of_identification,
                     geom, created_by)
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    'automated', 'machine',
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                    %s
                )
            """, (
                observation_id, activity_id, media_id, offset_s,
                common_name, scientific_name, confidence,
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