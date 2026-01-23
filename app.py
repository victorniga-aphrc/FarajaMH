from __future__ import annotations

# app.py — bootstrap Flask app

import os

# Tell transformers to skip torchvision-dependent features
os.environ.setdefault("TRANSFORMERS_NO_TORCHVISION", "1")

from app_pkg import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        debug=app.config.get("DEBUG", False),
        host="0.0.0.0",
        port=5000,
    )
