#!/usr/bin/env python3

import os
import csv
import uuid
import time
import subprocess
import psycopg2
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
import urllib.request
import urllib.parse
import json as json_lib

app = Flask(__name__)

INBOX_DIR = "/audio/inbox"
WAV_DIR = "/audio/wav"
RESULTS_DIR = "/results"
CREATED_BY = "spadefoot"
AUDIO_EXTENSIONS = ["opus", "flac", "wav", "mp3", "m4a", "ogg", "aac"]
LABELS_FILE = "/usr/local/lib/python3.11/site-packages/birdnet_analyzer/labels/V2.4/BirdNET_GLOBAL_6K_V2.4_Labels_en_uk.txt"

TZ_MAP = {
    "EDT": "America/New_York",
    "EST": "America/New_York",
    "CDT": "America/Chicago",
    "CST": "America/Chicago",
    "MDT": "America/Denver",
    "MST": "America/Denver",
    "PDT": "America/Los_Angeles",
    "PST": "America/Los_Angeles",
    "AKDT": "America/Anchorage",
    "AKST": "America/Anchorage",
    "HST": "Pacific/Honolulu",
    "HDT": "Pacific/Honolulu",
    "ADT": "America/Halifax",
    "AST": "America/Halifax",
    "UTC": "UTC",
    "GMT": "UTC",
    "CET": "Europe/Paris",
    "CEST": "Europe/Paris",
    "AEST": "Australia/Sydney",
    "AEDT": "Australia/Sydney",
    "ACST": "Australia/Darwin",
    "ACDT": "Australia/Adelaide",
    "AWST": "Australia/Perth",
    "JST": "Asia/Tokyo",
    "IST": "Asia/Kolkata",
    "SAST": "Africa/Johannesburg",
    "BRT": "America/Sao_Paulo",
    "BRST": "America/Sao_Paulo",
    "ART": "America/Argentina/Buenos_Aires",
    "NZST": "Pacific/Auckland",
    "NZDT": "Pacific/Auckland",
}

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
        tz_str = parts[3]
        rec_type = parts[4]
    except (IndexError, ValueError) as e:
        print(f"Could not parse filename {result_file.name}: {e}")
        return 0

    tz = ZoneInfo(TZ_MAP.get(tz_str, "UTC"))
    try:
        recorded_at = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S").replace(tzinfo=tz)
    except ValueError as e:
        print(f"Could not parse timestamp from {result_file.name}: {e}")
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

def fetch_gbif(scientific_name):
    try:
        url = f"https://api.gbif.org/v1/species/match?name={urllib.parse.quote(scientific_name)}&verbose=false"
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json_lib.loads(r.read())
        if data.get("matchType") == "NONE":
            return None
        return {
            "gbif_taxon_key": data.get("usageKey") or data.get("speciesKey"),
            "scientific_name": data.get("species") or data.get("canonicalName"),
            "kingdom": data.get("kingdom"),
            "phylum": data.get("phylum"),
            "class": data.get("class"),
            "order": data.get("order"),
            "family": data.get("family"),
            "common_name": data.get("canonicalName") or scientific_name,
        }
    except:
        return None

def fetch_wikipedia_image(scientific_name, common_name):
    for q in [common_name, scientific_name]:
        if not q:
            continue
        try:
            url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(q)}"
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json_lib.loads(r.read())
            thumb = data.get("thumbnail", {})
            if thumb.get("source"):
                return {
                    "image_url": thumb["source"],
                    "image_attribution": data.get("content_urls", {}).get("desktop", {}).get("page")
                }
        except:
            continue
    return None

def fetch_inat_image(scientific_name, common_name):
    for q in [scientific_name, common_name]:
        if not q:
            continue
        try:
            url = f"https://api.inaturalist.org/v1/taxa?q={urllib.parse.quote(q)}&per_page=1&photos=true"
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json_lib.loads(r.read())
            taxon = (data.get("results") or [None])[0]
            if not taxon:
                continue
            photo = taxon.get("default_photo", {})
            if photo.get("medium_url"):
                return {
                    "image_url": photo["medium_url"],
                    "image_attribution": f"© {photo.get('attribution', 'iNaturalist contributors')} via iNaturalist"
                }
        except:
            continue
    return None

def enrich_species(cur, conn, scientific_name, common_name):
    lookup = scientific_name or common_name
    cur.execute("""
        SELECT gbif_fetched_at FROM core.species
        WHERE scientific_name = %s OR (scientific_name = '' AND common_name = %s)
    """, (lookup, common_name))
    row = cur.fetchone()
    if row and row[0]:
        return

    gbif = fetch_gbif(scientific_name) if scientific_name else None
    image = fetch_wikipedia_image(scientific_name, common_name) or \
            fetch_inat_image(scientific_name, common_name) or \
            {"image_url": None, "image_attribution": None}

    sci = scientific_name if scientific_name else (gbif or {}).get("scientific_name") or common_name
    common = common_name or (gbif or {}).get("common_name") or scientific_name

    cur.execute("""
        INSERT INTO core.species (
            scientific_name, common_name, gbif_taxon_key,
            kingdom, phylum, class, "order", family,
            image_url, image_attribution, gbif_fetched_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (scientific_name) DO UPDATE SET
            common_name = EXCLUDED.common_name,
            gbif_taxon_key = EXCLUDED.gbif_taxon_key,
            kingdom = EXCLUDED.kingdom,
            phylum = EXCLUDED.phylum,
            class = EXCLUDED.class,
            "order" = EXCLUDED."order",
            family = EXCLUDED.family,
            image_url = EXCLUDED.image_url,
            image_attribution = EXCLUDED.image_attribution,
            gbif_fetched_at = NOW()
    """, (
        sci, common,
        (gbif or {}).get("gbif_taxon_key"),
        (gbif or {}).get("kingdom"),
        (gbif or {}).get("phylum"),
        (gbif or {}).get("class"),
        (gbif or {}).get("order"),
        (gbif or {}).get("family"),
        image["image_url"],
        image["image_attribution"]
    ))
    conn.commit()
    print(f"Enriched: {sci}")

def enrich_all(cur, conn):
    cur.execute("""
        SELECT DISTINCT
            COALESCE(machine_scientific_name, '') AS machine_scientific_name,
            machine_common_name
        FROM core.observations
        WHERE machine_common_name IS NOT NULL
            AND machine_common_name NOT IN (
                SELECT common_name FROM core.species WHERE gbif_fetched_at IS NOT NULL
            )
    """)
    rows = cur.fetchall()
    print(f"Enriching {len(rows)} new species...")
    for row in rows:
        enrich_species(cur, conn, row[0], row[1])
        time.sleep(0.2)
    print(f"Enrichment complete.")

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/process", methods=["POST"])
def process():
    data = request.get_json()
    object_name = data.get("filename", "")

    if not object_name:
        return jsonify({"error": "filename required"}), 400

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

    # ── Enrich new species ────────────────────────────────────────────────────
    print(f"Enriching species...")
    enrich_all(cur, conn)

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