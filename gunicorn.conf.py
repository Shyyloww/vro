import os

# --- NEW FIX ---
# Render dynamically assigns a PORT environment variable.
# Gunicorn automatically loads this configuration file if present in the root directory.
# This guarantees your app binds to the correct port so Render health checks pass.
port = os.environ.get("PORT", "10000")
bind = f"0.0.0.0:{port}"

# Recommended production settings for Render
workers = 2
threads = 4
timeout = 120
