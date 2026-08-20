from __future__ import annotations

import csv
import ctypes
import json
import os
import queue
import re
import sqlite3
import tkinter as tk
import threading
import time
import traceback
import unicodedata
import uuid
from collections import Counter, deque
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import quote

import sys
import types

from core import paths
from core.paths import (
    APP_NAME,
    BASE_DIR,
    CONFIG_PATH,
    DATA_DIR,
    DB_PATH,
    DEFAULT_MODEL,
    ERROR_LOG_PATH,
    EVIDENCE_DIR,
    MODEL_DIR,
    TRACKER_CONFIG,
)
from core.config import (
    DANGER_SOUND_OPTIONS,
    DANGER_SOUND_PATTERNS,
    DEFAULT_CONFIG,
    RTSP_ROUTE_CANDIDATES,
    RTSP_TEMPLATES,
    SUPPORTED_CAMERA_BRANDS,
    SUPPORTED_MODEL_TASKS,
    TRACKABLE_MODEL_TASKS,
    load_config,
    save_config,
)
from core import failures
from core.utils import format_timestamp_12h
from core.evidence import EvidenceManager
from core.storage import EventStore
from core.packets import FramePacket, PreviewPacket
from core.pipeline import (
    CrossingMixin,
    OverlayMixin,
    SceneDeduplicator,
    TrackingMixin,
    ZoneMixin,
)
from core.pipeline.validation import validated_line_points, validated_zone_points
from core import runtime
from ui import AlarmsMixin, GeometryMixin, LayoutMixin, ModelMixin
from ui.widgets import MetricCard
from core.camera import LatestFrameReader


class _CompatModule(types.ModuleType):
    """Reasignar rutas sobre este modulo sigue afectando a core (compatibilidad)."""

    _RUTAS = {
        "APP_NAME", "BASE_DIR", "CONFIG_PATH", "DATA_DIR", "DB_PATH",
        "DEFAULT_MODEL", "ERROR_LOG_PATH", "EVIDENCE_DIR", "MODEL_DIR",
        "TRACKER_CONFIG",
    }

    def __setattr__(self, name, value):
        if name in self._RUTAS:
            setattr(paths, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _CompatModule

import cv2
import customtkinter as ctk
import numpy as np
from PIL import Image, ImageTk

try:
    import winsound
except ImportError:
    winsound = None


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class DetectionWorker(
    TrackingMixin, CrossingMixin, ZoneMixin, OverlayMixin, threading.Thread
):
    def __init__(
        self,
        model,
        source,
        source_name: str,
        config: dict,
        frame_queue: queue.Queue,
        status_queue: queue.Queue,
        event_queue: queue.Queue,
        alert_queue: queue.Queue,
        preview_queue: queue.Queue,
        store: EventStore,
    ):
        super().__init__(daemon=True, name="detection-worker")
        self.model = model
        self.model_task = str(
            config.get("model_task") or getattr(model, "task", "detect")
        ).lower()
        # La eleccion de equipo vive en core/runtime.py. Antes cada detector
        # consultaba CUDA por su cuenta y el de objetos ni siquiera lo hacia:
        # con una sola fuente los dos deciden igual. El comportamiento no
        # cambia (GPU 0 y FP16 con CUDA, CPU y FP32 sin ella).
        runtime.preparar_equipo()
        self.inference_device = runtime.dispositivo_inferencia()
        self.inference_quantization = runtime.cuantizacion()
        self.hardware_label = runtime.nombre_equipo()
        self.source = source
        self.source_name = source_name
        self.config = config
        self.frame_queue = frame_queue
        self.status_queue = status_queue
        self.event_queue = event_queue
        self.alert_queue = alert_queue
        self.preview_queue = preview_queue
        self.store = store
        self.stop_event = threading.Event()
        self.capture = None
        self._last_event_at = 0.0
        self._fps_samples = deque(maxlen=30)
        self.enabled_class_ids = self._resolve_enabled_class_ids()
        self._state_lock = threading.Lock()
        self.line_enabled = bool(
            config.get("line_enabled", True)
            and config.get("line_defined", True)
        )
        self.line_points = validated_line_points(config.get("line_points"))
        self.zone_enabled = bool(config.get("zone_enabled", False))
        self.high_danger_zone = bool(
            config.get("high_danger_zone", False)
        )
        self.zone_points = validated_zone_points(
            config.get("zone_points")
        )
        self.zone_alert_cooldown = float(
            config.get("zone_alert_cooldown", 4.0)
        )
        self.zone_track_states: dict[int, dict] = {}
        self._published_danger_state = (False, ())
        self._last_zone_alert_at = 0.0
        self.save_evidence = bool(config.get("save_evidence", True))
        self.frame_number = 0
        self.track_states: dict[int, dict] = {}
        self.display_tracks: dict[int, dict] = {}
        self.fallback_tracks: dict[int, dict] = {}
        self.next_fallback_id = 1_000_000
        self.current_effective_track_ids = []
        # Prefijo de sesion: evita que el objeto 1 de hoy se confunda
        # con el objeto 1 de un arranque anterior al contar distintos.
        self.session_id = uuid.uuid4().hex[:8]
        self._dedup = SceneDeduplicator(
            refresh_seconds=float(config.get("evidence_refresh_seconds", 300.0)),
            enabled=bool(config.get("evidence_dedup", True)),
        )
        self.preview_tracks: dict[int, dict] = {}
        self.crossing_total = 0
        self.crossing_ab = 0
        self.crossing_ba = 0
        self.crossing_by_class = Counter()
        self.last_crossing = "Sin cruces"

    def _resolve_enabled_class_ids(self):
        enabled_names = self.config.get("enabled_class_names")
        if enabled_names is None:
            return None
        names = getattr(self.model, "names", {})
        if not isinstance(names, dict):
            names = dict(enumerate(names))
        enabled_set = set(enabled_names)
        return [
            int(class_id)
            for class_id, class_name in names.items()
            if str(class_name) in enabled_set
        ]

    def _result_detections(self, result):
        """Devuelve cajas rectas u orientadas con una interfaz común."""
        return result.obb if self.model_task == "obb" else result.boxes

    def stop(self):
        self.stop_event.set()

    def update_line(self, points, enabled: bool):
        with self._state_lock:
            self.line_points = validated_line_points(points)
            self.line_enabled = bool(enabled)
            self.track_states.clear()

    def update_zone(
        self, points, enabled: bool, high_danger: bool | None = None
    ):
        with self._state_lock:
            self.zone_points = validated_zone_points(points)
            self.zone_enabled = bool(enabled)
            if high_danger is not None:
                self.high_danger_zone = bool(high_danger)
            self.zone_track_states.clear()
        self._publish_danger_state(False, [])

    def _publish_danger_state(
        self, active: bool, class_names: list[str]
    ):
        state = (
            bool(active),
            tuple(sorted(set(str(name) for name in class_names))),
        )
        if state == self._published_danger_state:
            return
        self._published_danger_state = state
        self.alert_queue.put(
            {
                "kind": "danger_state",
                "active": state[0],
                "class_names": list(state[1]),
            }
        )

    def reset_crossing_counts(self):
        with self._state_lock:
            self.crossing_total = 0
            self.crossing_ab = 0
            self.crossing_ba = 0
            self.crossing_by_class.clear()
            self.last_crossing = "Sin cruces"
            self.track_states.clear()

    def update_save_evidence(self, enabled: bool):
        with self._state_lock:
            self.save_evidence = bool(enabled)

    def _publish_status(self, state: str, message: str):
        self.status_queue.put({"state": state, "message": message})

    def _put_latest(self, packet: FramePacket):
        try:
            self.frame_queue.put_nowait(packet)
        except queue.Full:
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.frame_queue.put_nowait(packet)
            except queue.Full:
                pass

    def _put_latest_preview(self, packet: PreviewPacket):
        try:
            self.preview_queue.put_nowait(packet)
        except queue.Full:
            try:
                self.preview_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.preview_queue.put_nowait(packet)
            except queue.Full:
                pass

    def _publish_live_preview(self, frame, _captured_at):
        preview = frame.copy()
        now = time.perf_counter()
        with self._state_lock:
            tracks = {
                track_id: {
                    **state,
                    "box": state["box"][:],
                    "velocity": state["velocity"][:],
                }
                for track_id, state in self.preview_tracks.items()
            }
        height, width = preview.shape[:2]
        for track_id, state in tracks.items():
            age = now - state["updated_at"]
            # Una caja antigua no debe perseguir al objeto durante segundos.
            if age > 0.55:
                continue
            # Predice únicamente el centro durante una fracción de segundo y
            # conserva el tamaño observado. Extrapolar las cuatro esquinas por
            # separado deformaba la caja cuando cambiaba de tamaño o de ID.
            velocity_x = float(
                (state["velocity"][0] + state["velocity"][2]) * 0.5
            )
            velocity_y = float(
                (state["velocity"][1] + state["velocity"][3]) * 0.5
            )
            extrapolation = min(age, 0.12)
            delta_x = float(np.clip(velocity_x * extrapolation, -0.018, 0.018))
            delta_y = float(np.clip(velocity_y * extrapolation, -0.018, 0.018))
            predicted = state["box"] + np.array(
                [delta_x, delta_y, delta_x, delta_y], dtype=float
            )
            x1, y1, x2, y2 = predicted
            x1, x2 = max(0, int(x1 * width)), min(width - 1, int(x2 * width))
            y1, y2 = max(0, int(y1 * height)), min(height - 1, int(y2 * height))
            if x2 <= x1 or y2 <= y1:
                continue
            cv2.rectangle(
                preview, (x1, y1), (x2, y2), (40, 209, 124), 2, cv2.LINE_AA
            )
            display_id = (
                f"F{track_id - 999_999}" if track_id >= 1_000_000 else str(track_id)
            )
            label = (
                f"{state['class_name']} {state['confidence']:.0%} "
                f"ID {display_id}"
            )
            cv2.putText(
                preview, label, (x1, max(18, y1 - 5)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.48, (40, 209, 124),
                1, cv2.LINE_AA
            )
        self._draw_counting_overlay(preview)
        self._put_latest_preview(
            PreviewPacket(
                frame=preview,
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        )

    def _open_capture(self):
        if self.config["source_type"] == "Cámara IP / RTSP":
            sources = self.source if isinstance(self.source, list) else [self.source]
            capture = None
            for index, source in enumerate(sources, start=1):
                if len(sources) > 1:
                    self._publish_status(
                        "connecting",
                        f"Buscando flujo Provision {index}/{len(sources)}…",
                    )
                candidate = cv2.VideoCapture(
                    source,
                    cv2.CAP_FFMPEG,
                    [
                        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 4000,
                        cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000,
                    ],
                )
                if candidate.isOpened():
                    capture = candidate
                    # En una reconexión, probar primero el perfil confirmado.
                    if index > 1:
                        self.source = [source] + [
                            item for item in sources if item != source
                        ]
                    break
                candidate.release()
            if capture is None:
                capture = cv2.VideoCapture()
        else:
            capture = cv2.VideoCapture(self.source)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if isinstance(self.source, int):
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        return capture

    def _wait_interruptibly(self, seconds: float) -> bool:
        return self.stop_event.wait(seconds)

    def _reset_model_trackers(self):
        predictor = getattr(self.model, "predictor", None)
        for tracker in getattr(predictor, "trackers", []) if predictor else []:
            if hasattr(tracker, "reset"):
                tracker.reset()

    def run(self):
        # El calentamiento se difiere hasta que el operador inicia la
        # detección. Así la ventana y sus controles quedan disponibles antes,
        # sin abrir la cámara hasta que el motor esté preparado.
        self._publish_status("connecting", "Preparando motor de inferencia…")
        try:
            warmup_size = min(int(self.config.get("image_size", 640)), 640)
            self.model.predict(
                np.zeros((warmup_size, warmup_size, 3), dtype=np.uint8),
                imgsz=warmup_size,
                device=self.inference_device,
                quantize=self.inference_quantization,
                verbose=False,
            )
        except Exception as exc:
            self._publish_status(
                "error", f"No se pudo preparar el modelo: {exc}"
            )
            return
        self._reset_model_trackers()
        reconnect_attempt = 0
        self._publish_status("connecting", "Conectando con la fuente…")
        while not self.stop_event.is_set():
            self.capture = self._open_capture()
            if not self.capture.isOpened():
                self.capture.release()
                reconnect_attempt += 1
                wait = min(2 ** min(reconnect_attempt, 4), 15)
                self._publish_status(
                    "reconnecting",
                    f"Sin conexión. Reintento {reconnect_attempt} en {wait} s",
                )
                if self._wait_interruptibly(wait):
                    break
                continue

            reconnect_attempt = 0
            self._publish_status(
                "online",
                f"Fuente conectada · {self.hardware_label} · "
                f"objetivo {self.config['target_fps']} FPS",
            )
            consecutive_failures = 0
            min_period = 1.0 / max(float(self.config["target_fps"]), 1.0)
            next_inference = time.perf_counter()
            is_video_file = self.config["source_type"] == "Archivo de video"
            latest_reader = None
            last_live_sequence = -1
            if not is_video_file:
                latest_reader = LatestFrameReader(
                    self.capture,
                    self.stop_event,
                    preview_callback=self._publish_live_preview,
                    preview_fps=30.0,
                )
                latest_reader.start()
            source_fps = self.capture.get(cv2.CAP_PROP_FPS) if is_video_file else 0
            file_stride = (
                max(1, round(source_fps / float(self.config["target_fps"])))
                if source_fps > 0
                else 1
            )

            while not self.stop_event.is_set():
                now = time.perf_counter()
                if is_video_file and now < next_inference:
                    if self._wait_interruptibly(min(next_inference - now, 0.05)):
                        break
                    continue
                if is_video_file:
                    ok, frame = self.capture.read()
                    if not ok:
                        consecutive_failures += 1
                    else:
                        consecutive_failures = 0
                else:
                    latest = latest_reader.latest_after(last_live_sequence)
                    if latest is None:
                        if latest_reader.disconnected:
                            consecutive_failures = 20
                        else:
                            self._wait_interruptibly(0.005)
                            continue
                    else:
                        last_live_sequence, frame, _ = latest
                        consecutive_failures = 0

                if consecutive_failures:
                    if consecutive_failures >= 20:
                        self._publish_status(
                            "reconnecting", "Se perdió la señal. Reconectando…"
                        )
                        break
                    self._wait_interruptibly(0.05)
                    continue

                now = time.perf_counter()
                if not is_video_file and now < next_inference:
                    continue
                next_inference = now + min_period

                try:
                    started = time.perf_counter()
                    if self.model_task in TRACKABLE_MODEL_TASKS:
                        result = self.model.track(
                            source=frame,
                            conf=float(self.config["confidence"]),
                            iou=float(self.config["iou"]),
                            imgsz=int(self.config["image_size"]),
                            classes=self.enabled_class_ids,
                            max_det=int(self.config.get("max_detections", 300)),
                            agnostic_nms=bool(
                                self.config.get("agnostic_nms", True)
                            ),
                            device=self.inference_device,
                            quantize=self.inference_quantization,
                            persist=True,
                            tracker=str(TRACKER_CONFIG),
                            verbose=False,
                        )[0]
                    else:
                        result = self.model.predict(
                            source=frame,
                            imgsz=int(self.config["image_size"]),
                            device=self.inference_device,
                            quantize=self.inference_quantization,
                            verbose=False,
                        )[0]
                    self._apply_class_confidence_overrides(result)
                    latency_ms = (time.perf_counter() - started) * 1000
                except Exception as exc:
                    self._publish_status("error", f"Error de inferencia: {exc}")
                    if self._wait_interruptibly(1):
                        break
                    continue

                self.frame_number += 1
                counts, confidences = self._extract_detections(result)
                total = sum(counts.values())
                crop = self._detection_mosaic(frame, result)
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._process_crossings(result, frame.shape, timestamp)
                self._process_zone_alerts(result, frame, timestamp)
                # Algunas tareas (especialmente clasificación) devuelven una
                # matriz de sólo lectura. La copia permite dibujar la línea y
                # las cajas persistentes sin que OpenCV cierre el worker.
                annotated = result.plot(line_width=2, font_size=11).copy()
                self._draw_persistent_tracks(annotated, result)

                frame_time = time.perf_counter()
                self._fps_samples.append(frame_time)
                fps = self._calculate_fps()
                with self._state_lock:
                    crossing_total = self.crossing_total
                    crossing_ab = self.crossing_ab
                    crossing_ba = self.crossing_ba
                    crossing_by_class = dict(self.crossing_by_class)
                    last_crossing = self.last_crossing
                packet = FramePacket(
                    frame=annotated,
                    crop=crop,
                    counts=dict(counts),
                    total=total,
                    fps=fps,
                    latency_ms=latency_ms,
                    timestamp=timestamp,
                    crossing_total=crossing_total,
                    crossing_ab=crossing_ab,
                    crossing_ba=crossing_ba,
                    crossing_by_class=crossing_by_class,
                    last_crossing=last_crossing,
                )
                self._put_latest(packet)

                if total and self._should_record_event(counts):
                    event = self._record_event(
                        annotated, counts, confidences, timestamp
                    )
                    self.event_queue.put(event)

                if is_video_file:
                    for _ in range(file_stride - 1):
                        if not self.capture.grab():
                            break

            if latest_reader:
                latest_reader.stop()
                latest_reader.join(timeout=3.5)
            if self.capture and not latest_reader:
                self.capture.release()
            self.capture = None

            # Un archivo finaliza normalmente; no debe reproducirse en bucle.
            if (
                self.config["source_type"] == "Archivo de video"
                and consecutive_failures >= 20
            ):
                self._publish_status("finished", "El video llegó al final")
                break

            if not self.stop_event.is_set():
                self._wait_interruptibly(1)

        if self.capture:
            self.capture.release()
        self._publish_danger_state(False, [])
        self._publish_status("stopped", "Detección detenida")

    def _extract_detections(self, result):
        counts = Counter()
        confidences = []
        if self.model_task == "classify":
            if result.probs is None:
                return counts, confidences
            class_id = int(result.probs.top1)
            if (
                self.enabled_class_ids is not None
                and class_id not in self.enabled_class_ids
            ):
                return counts, confidences
            confidence = float(result.probs.top1conf.item())
            if confidence < float(self.config["confidence"]):
                return counts, confidences
            name = str(result.names.get(class_id, class_id))
            counts[name] = 1
            confidences.append(confidence)
            return counts, confidences
        detections = self._result_detections(result)
        if detections is None:
            return counts, confidences
        for box in detections:
            class_id = int(box.cls[0].item())
            name = str(result.names.get(class_id, class_id))
            counts[name] += 1
            confidences.append(float(box.conf[0].item()))
        return counts, confidences

    def _apply_class_confidence_overrides(self, result):
        overrides = self.config.get("class_confidence_overrides", {})
        detections = self._result_detections(result)
        if not overrides or detections is None or len(detections) == 0:
            return
        keep_indices = []
        for index, box in enumerate(detections):
            class_id = int(box.cls[0].item())
            class_name = str(result.names.get(class_id, class_id))
            minimum = float(overrides.get(class_name, 0.0))
            if float(box.conf[0].item()) >= minimum:
                keep_indices.append(index)
        if len(keep_indices) == len(detections):
            return
        if self.model_task == "obb":
            result.obb = result.obb[keep_indices]
        else:
            result.boxes = result.boxes[keep_indices]
        if result.masks is not None:
            result.masks = result.masks[keep_indices]

    def _calculate_fps(self) -> float:
        if len(self._fps_samples) < 2:
            return 0.0
        elapsed = self._fps_samples[-1] - self._fps_samples[0]
        return (len(self._fps_samples) - 1) / elapsed if elapsed else 0.0

    def _should_record_event(self, counts=None) -> bool:
        now = time.monotonic()
        if now - self._last_event_at < float(self.config["event_interval"]):
            return False
        # El intervalo marca el ritmo maximo; el deduplicador decide si la
        # escena cambio lo suficiente para justificar una evidencia nueva.
        if not self._dedup.should_record(
            self.current_effective_track_ids, counts or {}, now
        ):
            return False
        self._last_event_at = now
        return True

    def _record_event(self, annotated, counts, confidences, timestamp):
        evidence_path = ""
        with self._state_lock:
            save_evidence = self.save_evidence
        if save_evidence:
            manager = self.store.evidence_manager
            if manager:
                evidence_path = manager.save_image(
                    annotated,
                    timestamp,
                    self.source_name,
                    "detecciones",
                    dict(counts),
                    max(confidences, default=0.0),
                )
        event = {
            "detected_at": timestamp,
            "source": self.source_name,
            "total": sum(counts.values()),
            "classes": dict(counts),
            "max_confidence": max(confidences, default=0.0),
            "evidence_path": evidence_path,
            "model_name": Path(self.config["model_path"]).name,
            "track_ids": [
                f"{self.session_id}:{track}"
                for track in self.current_effective_track_ids
                if track is not None
            ],
        }
        try:
            event["id"] = self.store.insert(event)
        except sqlite3.Error as exc:
            event["id"] = 0
            self._publish_status(
                "error", f"No se pudo registrar una detección: {exc}"
            )
        return event


class DetectorApp(
    LayoutMixin, GeometryMixin, AlarmsMixin, ModelMixin, ctk.CTk
):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} · Detección de personas")
        # Reemplaza el icono azul predeterminado de Tk en la barra de título.
        self._window_icon = tk.PhotoImage(width=1, height=1)
        self.iconphoto(True, self._window_icon)
        self.geometry("1480x860")
        self.minsize(1180, 700)
        self.configure(fg_color="#0b1118")
        if os.environ.get("ARZYZ_LAUNCHED_FROM_HUB") == "1":
            # El Centro de Control abre cada módulo como una herramienta de
            # trabajo completa, sin incrustar la inferencia dentro del menú.
            self.after(80, lambda: self.state("zoomed"))

        self.config_data = load_config()
        self.evidence_manager = EvidenceManager(EVIDENCE_DIR)
        self.store = EventStore(DB_PATH, self.evidence_manager)
        self.model = None
        self.model_task = str(self.config_data.get("model_task", "detect"))
        self.model_path = Path(self.config_data["model_path"])
        self.enabled_class_names = self.config_data.get("enabled_class_names")
        self.available_classes: dict[int, str] = {}
        self.worker: DetectionWorker | None = None
        self.frame_queue = queue.Queue(maxsize=2)
        self.preview_queue = queue.Queue(maxsize=2)
        self.status_queue = queue.Queue()
        self.event_queue = queue.Queue()
        self.alert_queue = queue.Queue()
        self.maintenance_queue = queue.Queue()
        self.model_queue = queue.Queue()
        self.live_image = None
        self.crop_image = None
        self.last_raw_frame = None
        self.line_points = validated_line_points(
            self.config_data.get("line_points")
        )
        self.line_defined = bool(
            self.config_data.get("line_defined", True)
        )
        self.zone_points = validated_zone_points(
            self.config_data.get("zone_points")
        )
        self.video_display_rect = (0, 0, 1, 1)
        self._drawing_line_points: list[list[float]] | None = None
        self._drawing_zone_points: list[list[float]] | None = None
        self._drag_target: tuple[str, int] | None = None
        self._drag_original_points: list[list[float]] | None = None
        self._active_alert_popup = None
        self._active_alert_class_label = None
        self._last_alert_sound_at = 0.0
        self._danger_alarm_active = False
        self._danger_alarm_generation = 0
        self._danger_mp3_alias = f"arzyz_danger_{os.getpid()}"
        self._danger_mp3_playing = False
        self._video_resize_pending = False
        self._video_resize_after_id = None
        self._stable_video_canvas_size = (0, 0)
        self._deferred_frame_packet = None
        self._deferred_preview_packet = None
        self._closing = False

        self._build_ui()
        self._load_recent_events()
        self.after(33, self._poll_queues)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Map>", self._on_window_map, add="+")
        # CustomTkinter aplica su icono unos milisegundos después de crear la
        # ventana; se vuelve a establecer el icono transparente al final.
        self.after(300, lambda: self.iconphoto(True, self._window_icon))
        self.after(150, lambda: self.load_model(self.model_path, initial=True))

    def _on_window_map(self, _event=None):
        if not self._video_resize_pending or self._closing:
            return
        if self._video_resize_after_id is not None:
            try:
                self.after_cancel(self._video_resize_after_id)
            except tk.TclError:
                pass
        self._video_resize_after_id = self.after(
            80, self._finish_video_resize
        )

    def _start_evidence_maintenance(self):
        def maintain():
            try:
                result = self.store.maintain_evidence()
                self.maintenance_queue.put(("ok", result))
            except Exception as exc:
                self.maintenance_queue.put(("error", str(exc)))

        threading.Thread(
            target=maintain, daemon=True, name="evidence-maintenance"
        ).start()

    def report_callback_exception(self, exc_type, exc_value, exc_traceback):
        """Evita perder la aplicación por un error de un control de Tk."""
        details = "".join(
            traceback.format_exception(exc_type, exc_value, exc_traceback)
        )
        failures.record("interfaz", "error en un control de Tk", exc=exc_value)
        try:
            with ERROR_LOG_PATH.open("a", encoding="utf-8") as log:
                log.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{details}")
        except OSError:
            pass
        if hasattr(self, "message_label"):
            self._set_message(
                f"Se evitó un cierre inesperado. Revisa {ERROR_LOG_PATH.name}.",
                error=True,
            )


    def _evidence_changed(self):
        enabled = bool(self.evidence_var.get())
        self.config_data["save_evidence"] = enabled
        save_config(self.config_data)
        if self.worker:
            self.worker.update_save_evidence(enabled)
        self._set_message(
            "Evidencias automáticas activadas"
            if enabled
            else "Evidencias automáticas desactivadas"
        )

    def reset_crossing_counts(self):
        if self.worker:
            self.worker.reset_crossing_counts()
        self.cross_total_label.configure(text="0")
        self.cross_ab_label.configure(text="0")
        self.cross_ba_label.configure(text="0")
        self.cross_classes_label.configure(text="Por clase: —")
        self.last_crossing_label.configure(text="Último: sin cruces")
        self._set_message("Conteos de la sesión reiniciados.")

    def _build_source(self):
        source_type = self.source_var.get()
        if source_type == "Cámara local":
            try:
                source = int(self.camera_index_entry.get().strip() or "0")
            except ValueError as exc:
                raise ValueError("El índice de cámara debe ser un número.") from exc
            return source, f"Cámara local {source}"
        if source_type == "Archivo de video":
            path = Path(self.file_entry.get().strip())
            if not path.is_file():
                raise ValueError("Selecciona un archivo de video válido.")
            return str(path), path.name

        ip = self.ip_entry.get().strip()
        if not ip:
            raise ValueError("Ingresa la dirección IP de la cámara.")
        port = "554"
        brand = self.brand_var.get()
        routes = RTSP_ROUTE_CANDIDATES.get(
            brand, [RTSP_TEMPLATES.get(brand, "/profile1")]
        )
        username = quote(self.user_entry.get().strip(), safe="")
        password = quote(self.password_entry.get(), safe="")
        credentials = f"{username}:{password}@" if username or password else ""
        sources = []
        for route in routes:
            if not route.startswith("/"):
                route = "/" + route
            sources.append(f"rtsp://{credentials}{ip}:{port}{route}")
        source = sources if len(sources) > 1 else sources[0]
        return source, f"RTSP {ip}"

    def _collect_config(self):
        self.config_data.update(
            {
                "source_type": self.source_var.get(),
                "camera_index": self.camera_index_entry.get().strip() or "0",
                "brand": self.brand_var.get(),
                "ip": self.ip_entry.get().strip(),
                "port": "554",
                "username": self.user_entry.get().strip(),
                "route": RTSP_TEMPLATES.get(
                    self.brand_var.get(), "/profile1"
                ),
                "video_file": self.file_entry.get().strip(),
                "model_path": str(self.model_path),
                "model_task": self.model_task,
                "confidence": round(float(self.confidence_slider.get()), 2),
                "image_size": int(self.image_size_var.get()),
                "target_fps": int(self.target_fps_var.get()),
                "save_evidence": bool(self.evidence_var.get()),
                "enabled_class_names": self.enabled_class_names,
                "line_enabled": bool(self.line_enabled_var.get()),
                "line_defined": bool(self.line_defined),
                "line_points": self.line_points,
                "zone_enabled": bool(self.zone_enabled_var.get()),
                "high_danger_zone": bool(self.high_danger_var.get()),
                "danger_sound_mode": self.danger_sound_var.get(),
                "danger_mp3_path": self.config_data.get(
                    "danger_mp3_path", ""
                ),
                "zone_points": self.zone_points,
                "zone_alert_cooldown": 4.0,
                "config_version": 8,
            }
        )
        save_config(self.config_data)

    def start_detection(self):
        if self.worker and self.worker.is_alive():
            return
        if self.model is None:
            self._set_message("Primero carga un modelo válido.", error=True)
            return
        try:
            source, source_name = self._build_source()
            self._collect_config()
        except ValueError as exc:
            messagebox.showerror("Configuración incompleta", str(exc))
            return

        self.worker = DetectionWorker(
            self.model, source, source_name, self.config_data.copy(),
            self.frame_queue, self.status_queue, self.event_queue,
            self.alert_queue,
            self.preview_queue, self.store
        )
        self.reset_crossing_counts()
        self.worker.start()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.snapshot_button.configure(state="normal")
        self.source_combo.configure(state="disabled")
        self.load_model_button.configure(state="disabled")
        self.select_classes_button.configure(state="disabled")
        self.people_only_button.configure(state="disabled")
        self.image_size_combo.configure(state="disabled")
        self.target_fps_combo.configure(state="disabled")
        if self.model_task in TRACKABLE_MODEL_TASKS:
            # Estas reglas son operativas y deben permanecer disponibles
            # durante la detección para aplicarse al worker en caliente.
            self.line_enabled_check.configure(state="normal")
            self.zone_enabled_check.configure(state="normal")
            self.high_danger_check.configure(state="normal")
        self.status_card.set("CONECTANDO", "#f4b942")
        self._set_message("Iniciando flujo de detección…")

    def stop_detection(self):
        worker = self.worker
        if worker:
            worker.stop()
            worker.join(timeout=5.0)
        self.worker = None
        self._set_danger_alarm(False)
        self.start_button.configure(state="normal" if self.model else "disabled")
        self.stop_button.configure(state="disabled")
        self.snapshot_button.configure(state="disabled")
        self.source_combo.configure(state="normal")
        self.load_model_button.configure(state="normal")
        self.select_classes_button.configure(state="normal")
        self.people_only_button.configure(state="normal")
        self.image_size_combo.configure(state="normal")
        self.target_fps_combo.configure(state="normal")
        self.status_card.set("DETENIDO", "#8292a2")
        self.fps_card.set("0.0 FPS")
        self.objects_card.set("0")

    def save_manual_snapshot(self):
        if self.last_raw_frame is None:
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        source = self.worker.source_name if self.worker else "captura_manual"
        evidence_path = self.evidence_manager.save_image(
            self.last_raw_frame,
            timestamp,
            source,
            "capturas_manuales",
        )
        if evidence_path:
            self.store.record_manual_capture(
                {
                    "captured_at": timestamp,
                    "source": source,
                    "model_name": self.model_path.name,
                    "evidence_path": evidence_path,
                }
            )
            self._set_message(
                f"Captura guardada:\n{Path(evidence_path).name}"
            )
        else:
            self._set_message("No fue posible guardar la captura.", error=True)

    def _poll_queues(self):
        if self._closing:
            return
        latest_packet = None
        while True:
            try:
                latest_packet = self.frame_queue.get_nowait()
            except queue.Empty:
                break

        latest_preview = None
        while True:
            try:
                latest_preview = self.preview_queue.get_nowait()
            except queue.Empty:
                break
        can_render = (
            self.state() != "iconic"
            and not self._video_resize_pending
        )
        if can_render:
            packet = latest_packet or self._deferred_frame_packet
            preview = latest_preview or self._deferred_preview_packet
            self._deferred_frame_packet = None
            self._deferred_preview_packet = None
            if packet:
                self._display_packet(
                    packet, render_main=preview is None
                )
            if preview:
                self._display_preview(preview)
        else:
            if latest_packet:
                self._deferred_frame_packet = latest_packet
                self.last_raw_frame = latest_packet.frame
            if latest_preview:
                self._deferred_preview_packet = latest_preview
                self.last_raw_frame = latest_preview.frame

        while True:
            try:
                status = self.status_queue.get_nowait()
                self._apply_status(status)
            except queue.Empty:
                break
        while True:
            try:
                event = self.event_queue.get_nowait()
                self._insert_event_row(event, at_top=True)
            except queue.Empty:
                break
        latest_alert = None
        while True:
            try:
                alert_message = self.alert_queue.get_nowait()
                if alert_message.get("kind") == "danger_state":
                    self._set_danger_alarm(
                        bool(alert_message.get("active"))
                    )
                else:
                    latest_alert = alert_message
            except queue.Empty:
                break
        if latest_alert:
            self._show_zone_alert(latest_alert)
        while True:
            try:
                state, payload = self.maintenance_queue.get_nowait()
                if state == "ok":
                    for item in self.event_tree.get_children():
                        self.event_tree.delete(item)
                    self._load_recent_events()
                    self._set_message(
                        "Evidencias organizadas · "
                        f"{payload['moved']} imágenes migradas · "
                        f"{payload['csv_files']} CSV diarios"
                    )
                else:
                    self._set_message(
                        f"No se pudo organizar la evidencia: {payload}",
                        error=True,
                    )
            except queue.Empty:
                break
        while True:
            try:
                result = self.model_queue.get_nowait()
                if result[0] == "loaded":
                    self._model_loaded(
                        result[1], result[2], result[3], result[4],
                        result[5], result[6]
                    )
                else:
                    self._model_failed(result[1])
            except queue.Empty:
                break
        self.after(33, self._poll_queues)

        if not self._danger_mp3_playing:
            return
        alias = self._danger_mp3_alias
        self._mci(f"stop {alias}")
        self._mci(f"close {alias}")
        self._danger_mp3_playing = False

    def _danger_alarm_step(self, generation: int, step: int):
        if (
            self._closing
            or not self._danger_alarm_active
            or generation != self._danger_alarm_generation
        ):
            return
        mode = self.danger_sound_var.get()
        pattern = DANGER_SOUND_PATTERNS.get(
            mode, DANGER_SOUND_PATTERNS["Doble pitido"]
        )
        frequency, duration, pause = pattern[step % len(pattern)]
        self._play_beep_async(frequency, duration)
        delay = duration + pause
        next_step = (step + 1) % len(pattern)
        self.after(
            delay,
            lambda: self._danger_alarm_step(generation, next_step),
        )

    def _show_zone_alert(self, alert: dict):
        now = time.monotonic()
        if (
            not alert.get("high_danger")
            and winsound
            and now - self._last_alert_sound_at >= 1.5
        ):
            self._play_beep_async()
            self._last_alert_sound_at = now
        if (
            self._active_alert_popup
            and self._active_alert_popup.winfo_exists()
        ):
            if self._active_alert_class_label is not None:
                self._active_alert_class_label.configure(
                    text=str(alert["class_name"])
                )
            self._active_alert_popup.deiconify()
            self._active_alert_popup.lift()
            return
        popup = ctk.CTkToplevel(self)
        self._active_alert_popup = popup
        popup.title("Alerta de zona")
        popup.geometry("330x150")
        popup.resizable(False, False)
        popup.attributes("-topmost", True)
        try:
            popup.attributes("-alpha", 0.93)
        except tk.TclError:
            pass
        popup.configure(fg_color="#b32632")
        popup.update_idletasks()
        x = self.winfo_x() + self.winfo_width() - 350
        y = self.winfo_y() + 82
        popup.geometry(f"+{max(x, 0)}+{max(y, 0)}")

        ctk.CTkLabel(
            popup, text="Objeto detectado",
            font=("Segoe UI", 18, "bold"), text_color="#ffffff",
            fg_color="transparent",
        ).pack(pady=(17, 1))
        self._active_alert_class_label = ctk.CTkLabel(
            popup, text=str(alert["class_name"]),
            font=("Segoe UI", 14), text_color="#ffffff",
            fg_color="transparent",
        )
        self._active_alert_class_label.pack(pady=(0, 8))

        def close_popup():
            if popup.winfo_exists():
                popup.destroy()
            if self._active_alert_popup is popup:
                self._active_alert_popup = None
                self._active_alert_class_label = None

        ctk.CTkButton(
            popup, text="CERRAR", width=88, height=28,
            fg_color="#b32632", hover_color="#b32632",
            border_width=1, border_color="#ffffff",
            command=close_popup,
        ).pack(pady=(0, 12))
        popup.protocol("WM_DELETE_WINDOW", close_popup)
        popup.bind("<Escape>", lambda _event: close_popup())

    def _display_preview(self, packet: PreviewPacket):
        self.last_raw_frame = packet.frame
        self.live_image = self._render_canvas_frame(
            self.video_canvas,
            self.video_image_item,
            self.video_placeholder_item,
            packet.frame,
            self.live_image,
            main_video=True,
        )
        self.frame_time_label.configure(
            text=f"EN VIVO · {format_timestamp_12h(packet.timestamp)}"
        )

    def _display_packet(
        self, packet: FramePacket, render_main: bool = True
    ):
        self.last_raw_frame = packet.frame
        if render_main:
            self.live_image = self._render_canvas_frame(
                self.video_canvas,
                self.video_image_item,
                self.video_placeholder_item,
                packet.frame,
                self.live_image,
                main_video=True,
            )

        if packet.crop is not None:
            self.crop_image = self._render_canvas_frame(
                self.crop_canvas,
                self.crop_image_item,
                self.crop_placeholder_item,
                packet.crop,
                self.crop_image,
            )

        summary = " · ".join(
            f"{name}: {count}"
            for name, count in sorted(packet.counts.items(), key=lambda item: -item[1])
        )
        self.class_summary_label.configure(
            text=summary or "No hay objetos detectados"
        )
        self.frame_time_label.configure(
            text=format_timestamp_12h(packet.timestamp)
        )
        self.latency_label.configure(text=f"{packet.latency_ms:.0f} ms")
        self.fps_card.set(f"{packet.fps:.1f} FPS")
        self.objects_card.set(str(packet.total))
        self._set_label_text(self.cross_total_label, str(packet.crossing_total))
        self._set_label_text(self.cross_ab_label, str(packet.crossing_ab))
        self._set_label_text(self.cross_ba_label, str(packet.crossing_ba))
        by_class = " · ".join(
            f"{name}: {count}"
            for name, count in sorted(
                packet.crossing_by_class.items(), key=lambda item: -item[1]
            )
        )
        self._set_label_text(
            self.cross_classes_label, f"Por clase: {by_class or '—'}"
        )
        self._set_label_text(
            self.last_crossing_label, f"Último: {packet.last_crossing}"
        )

    def _render_canvas_frame(
        self,
        canvas,
        image_item,
        placeholder_item,
        bgr_frame,
        current_photo,
        main_video: bool = False,
    ):
        canvas_width = max(canvas.winfo_width(), 2)
        canvas_height = max(canvas.winfo_height(), 2)
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        ratio = min(canvas_width / image.width, canvas_height / image.height)
        display_size = (
            max(1, int(image.width * ratio)),
            max(1, int(image.height * ratio)),
        )
        resized = image.resize(display_size, Image.Resampling.BILINEAR)
        background = Image.new(
            "RGB", (canvas_width, canvas_height), color="#070b10"
        )
        offset_x = (canvas_width - display_size[0]) // 2
        offset_y = (canvas_height - display_size[1]) // 2
        background.paste(resized, (offset_x, offset_y))

        if (
            current_photo is None
            or current_photo.width() != canvas_width
            or current_photo.height() != canvas_height
        ):
            current_photo = ImageTk.PhotoImage(background)
            canvas.itemconfigure(image_item, image=current_photo)
        else:
            # Conserva el mismo PhotoImage y cambia sólo sus píxeles.
            current_photo.paste(background)
        canvas.coords(image_item, 0, 0)
        canvas.itemconfigure(placeholder_item, state="hidden")
        if main_video:
            self.video_display_rect = (
                offset_x, offset_y, display_size[0], display_size[1]
            )
            self._refresh_zone_overlay()
            self._refresh_line_overlay()
            overlay_items = [
                self.zone_overlay_item,
                *self.zone_vertex_items,
                self.line_shadow_item,
                self.line_overlay_item,
                *self.line_endpoint_items,
                *[
                    item
                    for badge in self.line_badge_items
                    for item in badge
                ],
                self.line_first_point_item,
            ]
            for item in overlay_items:
                canvas.tag_raise(item)
        else:
            canvas.coords(
                placeholder_item, canvas_width / 2, canvas_height / 2
            )
        return current_photo

    @staticmethod
    def _set_label_text(widget, text: str):
        if widget.cget("text") != text:
            widget.configure(text=text)

    def _apply_status(self, status):
        states = {
            "connecting": ("CONECTANDO", "#f4b942"),
            "reconnecting": ("RECONECTANDO", "#f4b942"),
            "online": ("EN LÍNEA", "#28d17c"),
            "error": ("ERROR", "#ff6572"),
            "finished": ("FINALIZADO", "#43a9ff"),
            "stopped": ("DETENIDO", "#8292a2"),
        }
        label, color = states.get(status["state"], ("DESCONOCIDO", "#8292a2"))
        self.status_card.set(label, color)
        self._set_message(
            status["message"], error=status["state"] == "error"
        )
        if status["state"] in {"finished", "stopped"}:
            self._set_danger_alarm(False)
            self.start_button.configure(state="normal" if self.model else "disabled")
            self.stop_button.configure(state="disabled")
            self.snapshot_button.configure(state="disabled")
            self.source_combo.configure(state="normal")
            self.load_model_button.configure(state="normal")
            self.select_classes_button.configure(state="normal")
            self.people_only_button.configure(state="normal")
            self.image_size_combo.configure(state="normal")
            self.target_fps_combo.configure(state="normal")

    def _load_recent_events(self):
        for event in reversed(self.store.recent(100)):
            self._insert_event_row(event, at_top=True)

    def _insert_event_row(self, event: dict, at_top: bool):
        classes = ", ".join(
            f"{name} ×{count}" for name, count in event["classes"].items()
        )
        values = (
            format_timestamp_12h(event["detected_at"]),
            classes[:34] or str(event["total"]),
            (
                f"{event['max_confidence']:.0%}"
                if event["max_confidence"] > 0
                else "—"
            ),
        )
        item = self.event_tree.insert(
            "", 0 if at_top else "end", values=values
        )
        self.event_tree.set(item, "confidence", values[2])
        self.event_tree.item(
            item, tags=(event.get("evidence_path", ""),)
        )
        children = self.event_tree.get_children()
        if len(children) > 200:
            self.event_tree.delete(children[-1])

    def _open_selected_evidence(self, _event=None):
        selection = self.event_tree.selection()
        if not selection:
            return
        tags = self.event_tree.item(selection[0], "tags")
        if tags and tags[0] and Path(tags[0]).exists():
            os.startfile(tags[0])

    def open_evidence_folder(self):
        os.startfile(EVIDENCE_DIR)

    def open_event_csv(self):
        path = self.evidence_manager.csv_path(
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        if path.exists():
            os.startfile(path)
        else:
            self._set_message(
                "El CSV de hoy se creará con el primer evento.", error=True
            )

    def _set_message(self, text: str, error: bool = False):
        self.message_label.configure(
            text=text, text_color="#ff6572" if error else "#aeb9c4"
        )

    def _update_clock(self):
        if self._closing:
            return
        self.clock_label.configure(
            text=datetime.now().strftime("%d/%m/%Y  %I:%M:%S %p")
        )
        self.after(1000, self._update_clock)

    def on_close(self):
        self._closing = True
        self._set_danger_alarm(False)
        self._stop_danger_mp3()
        if self.worker:
            self.worker.stop()
            self.worker.join(timeout=5.0)
        self.destroy()


if __name__ == "__main__":
    # El centro de control indica con que nombre debe latir este proceso.
    # Al abrirlo por su cuenta no hay supervisor, y "detector" es suficiente.
    MODULO = os.environ.get("ARZYZ_MODULE_ID", "detector")
    failures.configure(MODULO)
    from core.heartbeat import HeartbeatWriter
    HeartbeatWriter(MODULO).start()
    app = DetectorApp()
    app.mainloop()
