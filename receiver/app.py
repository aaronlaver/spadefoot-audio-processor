import os
import json
import hmac
import hashlib
import subprocess
import threading
from flask import Flask, request, jsonify

app = Flask(__name__)

SIGNING_SECRET = os.environ.get("B2_WEBHOOK_SECRET", "")
PIPELINE_SCRIPT = "/custom_scripts/daily_run.sh"

def verify_signature(payload, signature):
    if not SIGNING_SECRET:
        return True
    expected = hmac.new(
        SIGNING_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)

def run_pipeline(filename):
    print(f"Starting pipeline for {filename}")
    subprocess.run(["bash", PIPELINE_SCRIPT], check=False)

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
                print(f"New file: {filename}")
                thread = threading.Thread(target=run_pipeline, args=(filename,))
                thread.daemon = True
                thread.start()
    except Exception as e:
        print(f"Error processing webhook: {e}")

    return jsonify({"status": "ok"}), 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)