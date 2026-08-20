"""Alertas de zona poligonal, incluida la zona de alto peligro.

Extraido de DetectionWorker sin modificar la logica. Es un mixin: conserva el
acceso a los atributos del worker (self.config, self.store, contadores...).
DetectionWorker lo compone; no se instancia por separado.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import cv2
import numpy as np


class ZoneMixin:
    """Alertas de zona poligonal, incluida la zona de alto peligro."""

    def _process_zone_alerts(self, result, frame, timestamp: str):
        with self._state_lock:
            zone_enabled = self.zone_enabled
            high_danger = self.high_danger_zone
            zone_points = [point[:] for point in self.zone_points]
        if not zone_enabled:
            self._publish_danger_state(False, [])
            return

        detections = self._result_detections(result)
        height, width = frame.shape[:2]
        contour = np.asarray(zone_points, dtype=np.float32)
        has_detections = detections is not None and len(detections) > 0
        if has_detections:
            xyxy = detections.xyxy.detach().cpu().numpy()
            class_ids = detections.cls.detach().cpu().numpy().astype(int)
            confidences = detections.conf.detach().cpu().numpy()
            track_ids = self.current_effective_track_ids
            if len(track_ids) != len(xyxy):
                track_ids = list(
                    range(
                        self.frame_number * 10_000,
                        self.frame_number * 10_000 + len(xyxy),
                    )
                )
        else:
            xyxy = np.empty((0, 4), dtype=np.float32)
            class_ids = np.empty((0,), dtype=np.int32)
            confidences = np.empty((0,), dtype=np.float32)
            track_ids = []

        alert_events = []
        now = time.monotonic()
        seen_ids = set()
        with self._state_lock:
            for index, track_id in enumerate(track_ids):
                track_id = int(track_id)
                seen_ids.add(track_id)
                center = (
                    float((xyxy[index][0] + xyxy[index][2]) / 2 / width),
                    float((xyxy[index][1] + xyxy[index][3]) / 2 / height),
                )
                inside = cv2.pointPolygonTest(contour, center, False) >= 0
                previous = self.zone_track_states.get(
                    track_id,
                    {
                        "inside": False,
                        "last_alert_at": -1e9,
                        "class_name": "",
                    },
                )
                class_id = int(class_ids[index])
                class_name = str(result.names.get(class_id, class_id))
                should_alert = (
                    inside
                    and not previous["inside"]
                    and now - previous["last_alert_at"]
                    >= self.zone_alert_cooldown
                )
                last_alert_at = now if should_alert else previous["last_alert_at"]
                self.zone_track_states[track_id] = {
                    "inside": inside,
                    "last_seen": self.frame_number,
                    "last_alert_at": last_alert_at,
                    "class_name": class_name,
                }
                if should_alert:
                    alert_events.append(
                        {
                            "kind": "zone_alert",
                            "alerted_at": timestamp,
                            "source": self.source_name,
                            "track_id": track_id,
                            "class_name": class_name,
                            "confidence": float(confidences[index]),
                            "high_danger": high_danger,
                            "model_name": Path(
                                self.config["model_path"]
                            ).name,
                        }
                    )
            for track_id, state in list(self.zone_track_states.items()):
                if track_id in seen_ids:
                    continue
                missing = self.frame_number - state.get("last_seen", 0)
                if missing > 3:
                    state["inside"] = False
                if missing > 180:
                    self.zone_track_states.pop(track_id, None)
            danger_classes = [
                state.get("class_name", "objeto")
                for state in self.zone_track_states.values()
                if state.get("inside")
            ]

        self._publish_danger_state(
            high_danger and bool(danger_classes),
            danger_classes,
        )

        for alert in alert_events:
            evidence_frame = result.plot(
                line_width=2, font_size=11
            ).copy()
            self._draw_zone_overlay(evidence_frame)
            manager = self.store.evidence_manager
            if manager:
                alert["evidence_path"] = manager.save_image(
                    evidence_frame,
                    timestamp,
                    self.source_name,
                    "alertas_zona",
                    {alert["class_name"]: 1},
                    alert["confidence"],
                )
            else:
                alert["evidence_path"] = ""
            try:
                alert["id"] = self.store.insert_zone_alert(alert)
            except sqlite3.Error as exc:
                alert["id"] = 0
                self._publish_status(
                    "error", f"No se pudo registrar la alerta de zona: {exc}"
                )
            self.alert_queue.put(alert)
            self.event_queue.put(
                {
                    "id": alert["id"],
                    "detected_at": timestamp,
                    "source": self.source_name,
                    "total": 1,
                    "classes": {f"⚠ ZONA · {alert['class_name']}": 1},
                    "max_confidence": alert["confidence"],
                    "evidence_path": alert["evidence_path"],
                    "model_name": alert["model_name"],
                }
            )
