#!/bin/bash

echo "[INIT] Fixing permissions..."
chmod -R 777 /app/data || true

echo "[INIT] Starting Xvfb..."
Xvfb :99 -screen 0 1920x1080x24 +extension RANDR &

sleep 2

echo "[INIT] Starting parser..."
exec python parser.py