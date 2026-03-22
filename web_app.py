"""MBES QC — Flask Web Application

Multibeam Echosounder data quality control web interface.
Tabler dark-theme dashboard with file QC, surface, coverage, and crossline modules.

Port: 5016

Copyright (c) 2025-2026 Geoview Co., Ltd.
"""

from flask import Flask, render_template, jsonify
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

app = Flask(__name__)
app.secret_key = "mbesqc-secret"


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "module": "MBES QC", "port": 5016})


if __name__ == "__main__":
    app.run(port=5016, debug=True)
