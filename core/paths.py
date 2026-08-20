"""Rutas y constantes de ubicacion. Unica fuente de verdad.

Extraido de detector_empresarial.py sin modificar la logica.
"""
from __future__ import annotations

import os
from pathlib import Path

# BASE_DIR apunta a la raiz del proyecto (core/ esta un nivel adentro).
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EVIDENCE_DIR = DATA_DIR / "evidencias"
MODEL_DIR = BASE_DIR / "modelos"
for directory in (DATA_DIR, EVIDENCE_DIR, MODEL_DIR, DATA_DIR / "ultralytics"):
    directory.mkdir(parents=True, exist_ok=True)

# Evita que Ultralytics intente escribir configuración fuera de la aplicación.
os.environ.setdefault("YOLO_CONFIG_DIR", str(DATA_DIR / "ultralytics"))
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

APP_NAME = "Arzyz Vision"
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "detecciones.db"
ERROR_LOG_PATH = DATA_DIR / "errores.log"
DEFAULT_MODEL = MODEL_DIR / "yolov8n.pt"
TRACKER_CONFIG = BASE_DIR / "config" / "bytetrack_arzyz.yaml"
