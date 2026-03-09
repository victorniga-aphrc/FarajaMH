#!/usr/bin/env python3
"""
Run the app with the WebSocket-capable server (same runtime as python app.py).
"""
from app import app, run_websocket_server

if __name__ == "__main__":
    try:
        run_websocket_server()
    except ImportError:
        app.run(debug=app.config.get("DEBUG", False), host="0.0.0.0", port=5000)
