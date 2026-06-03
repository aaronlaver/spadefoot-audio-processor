import os
import json
import hmac
import hashlib
import queue
import threading
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

SIGNING_SECRET = os.environ.get("B2_WEBHOOK_SECRET", "")
ANALYZER_URL = "http://analyzer:5001/process"
MAX_WORKERS = 1

job_queue = queue.Queue()

def worker():
    while True:
        filename = job_queue.get()
        try:
            print(f"Processing: {filename}")
            resp = requests.post(ANALYZER_URL, json={"filename": filename}, timeout=3600)
            print(f"Analyzer response: {resp.status_code} {resp.json()}")
        except Exception as e:
            print(f"Error processing {filename}: {e}")
        finally:
            job_queue.task_done()

for _ in range(MAX_WORKERS):
    t = threading.Thread(target=worker, daemon=True)
    t.start()

def verify_signature(payload, signature):
    if not SIGNING_SECRET:
        return True
    expected = hmac.new(
        SIGNING_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"v1={expected}", signature)

@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Bz-Event-Notification-Signature", "")
    payload = request.get_data()
    if not verify_signature(payload, signature):
        return jsonify({"error": "Invalid signature"}), 401
    try:
        body = json.loads(payload)
        events = body.get("events", [])
        for event in events:
            if event.get("eventType", "").startswith("b2:ObjectCreated"):
                filename = event.get("objectName", "")
                print(f"Queuing: {filename}")
                job_queue.put(filename)
    except Exception as e:
        print(f"Error parsing webhook: {e}")
    return jsonify({"status": "ok"}), 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/queue", methods=["GET"])
def queue_status():
    return jsonify({"queued": job_queue.qsize()}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)