"""Production-mode local runner (no Flask debug/reloader).

Use this instead of run.py when you want to test the app the way it will
behave in production (waitress is a real WSGI server, not the dev one).

    python serve.py
"""
import os

from waitress import serve

from app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    serve(app, host="0.0.0.0", port=port)
