#!/usr/bin/env python3
"""
Run the app with gunicorn + gevent so WebSocket (Live Mic / STT) works.
Use this instead of `python app.py` when you need the Live transcription feature.

  python run_with_websocket.py

Or from project root with venv active:
  gunicorn -k gevent -w 1 -b 0.0.0.0:5000 "app:app"
"""
import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    from gunicorn.app.base import BaseApplication
    from app import app

    class StandaloneApplication(BaseApplication):
        def __init__(self, app, options=None):
            self.options = options or {}
            self.application = app
            super().__init__()

        def load_config(self):
            for key, value in self.options.items():
                self.cfg.set(key.lower(), value)

        def load(self):
            return self.application

    options = {
        "bind": "0.0.0.0:5000",
        "workers": 1,
        "worker_class": "gevent",
        "timeout": 120,
    }
    StandaloneApplication(app, options).run()
