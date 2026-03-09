from __future__ import annotations

# app.py — bootstrap Flask app

# Gevent: patch before any other imports so ssl/urllib3/aiohttp are patched (avoids MonkeyPatchWarning with gunicorn -k gevent)
try:
    import gevent.monkey
    gevent.monkey.patch_all()
except ImportError:
    pass

import os

# Tell transformers to skip torchvision-dependent features
os.environ.setdefault("TRANSFORMERS_NO_TORCHVISION", "1")

from app_pkg import create_app

app = create_app()

def run_websocket_server() -> None:
    """Start app with gunicorn+gevent (WebSocket-capable)."""
    from gunicorn.app.base import BaseApplication

    class StandaloneApplication(BaseApplication):
        def __init__(self, flask_app, options=None):
            self.options = options or {}
            self.application = flask_app
            super().__init__()

        def load_config(self):
            for key, value in self.options.items():
                self.cfg.set(key.lower(), value)

        def load(self):
            return self.application

    StandaloneApplication(
        app,
        {"bind": "0.0.0.0:5000", "workers": 1, "worker_class": "gevent", "timeout": 120},
    ).run()

if __name__ == "__main__":
    # Always use gunicorn+gevent so Live Mic / WebSocket (STT) works
    try:
        run_websocket_server()
    except ImportError:
        app.run(debug=app.config.get("DEBUG", False), host="0.0.0.0", port=5000)
