"""Minimal Flask app for Elastic Beanstalk.

Elastic Beanstalk's Python platform looks for a WSGI callable named `application`
in `application.py` by default; the Procfile makes the gunicorn command explicit.
"""

import os
import platform
import socket
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template

application = Flask(__name__)
app = application  # convenience alias for `flask --app application run`

ENVIRONMENT = os.environ.get("APP_ENVIRONMENT", "local")
RELEASE = os.environ.get("APP_RELEASE", "dev")


@application.route("/")
def index():
    return render_template(
        "index.html",
        environment=ENVIRONMENT,
        release=RELEASE,
        hostname=socket.gethostname(),
        python_version=platform.python_version(),
        now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )


@application.route("/health")
def health():
    """Target group health check path — keep it cheap and dependency-free."""
    return jsonify(status="ok", environment=ENVIRONMENT, release=RELEASE), 200


@application.route("/api/info")
def info():
    return jsonify(
        environment=ENVIRONMENT,
        release=RELEASE,
        hostname=socket.gethostname(),
        python=platform.python_version(),
        time=datetime.now(timezone.utc).isoformat(),
    )


@application.errorhandler(404)
def not_found(_):
    return jsonify(error="not found"), 404


if __name__ == "__main__":
    application.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
