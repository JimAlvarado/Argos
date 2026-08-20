"""Conteo de cruces de linea con direccion A -> B y B -> A.

Extraido de DetectionWorker sin modificar la logica. Es un mixin: conserva el
acceso a los atributos del worker (self.config, self.store, contadores...).
DetectionWorker lo compone; no se instancia por separado.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from core.utils import format_timestamp_12h


class CrossingMixin:
    """Conteo de cruces de linea con direccion A -> B y B -> A."""

    def _process_crossings(self, result, frame_shape, timestamp: str):
        boxes = self._result_detections(result)
        if boxes is None or len(boxes) == 0:
            self.current_effective_track_ids = []
            return

        height, width = frame_shape[:2]
        xyxy = boxes.xyxy.detach().cpu().numpy()
        class_ids = boxes.cls.detach().cpu().numpy().astype(int)
        confidences = boxes.conf.detach().cpu().numpy()
        # El contador usa una asociación geométrica propia y tolerante a
        # interrupciones. ByteTrack continúa proporcionando los IDs visuales,
        # pero un parpadeo del detector ya no rompe la trayectoria de conteo.
        track_ids = self._fallback_track_ids(
            xyxy, class_ids, result.names, width, height
        )
        self.current_effective_track_ids = list(track_ids)
        self._update_preview_tracks(
            result, frame_shape, track_ids, xyxy, class_ids
        )
        with self._state_lock:
            if not self.line_enabled:
                return
            points = [point[:] for point in self.line_points]
        (x1, y1), (x2, y2) = points
        line_length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 0.001)
        crossing_candidates = []

        for index, track_id in enumerate(track_ids):
            center_x = ((xyxy[index][0] + xyxy[index][2]) / 2) / width
            center_y = ((xyxy[index][1] + xyxy[index][3]) / 2) / height
            line_length_squared = max(
                (x2 - x1) ** 2 + (y2 - y1) ** 2, 0.000001
            )
            projection = (
                (center_x - x1) * (x2 - x1)
                + (center_y - y1) * (y2 - y1)
            ) / line_length_squared
            # La línea es un segmento, no una línea infinita. Objetos fuera de
            # sus extremos no deben generar cruces.
            if projection < -0.05 or projection > 1.05:
                continue
            signed_distance = (
                (x2 - x1) * (center_y - y1)
                - (y2 - y1) * (center_x - x1)
            ) / line_length
            # Banda muerta de 1.5% para evitar dobles conteos por vibración.
            if abs(signed_distance) < 0.015:
                continue
            is_vertical = abs(y2 - y1) >= abs(x2 - x1)
            if is_vertical:
                side = "A" if signed_distance > 0 else "B"
            else:
                side = "A" if signed_distance < 0 else "B"
            class_name = str(
                result.names.get(int(class_ids[index]), int(class_ids[index]))
            )

            with self._state_lock:
                previous = self.track_states.get(track_id)
                minimum = min(
                    previous.get("minimum", signed_distance)
                    if previous else signed_distance,
                    signed_distance,
                )
                maximum = max(
                    previous.get("maximum", signed_distance)
                    if previous else signed_distance,
                    signed_distance,
                )
                age = previous.get("age", 0) + 1 if previous else 1
                movement_span = maximum - minimum
                confirmed_side = side
                if (
                    previous
                    and previous["side"] != side
                    and age >= 2
                    and movement_span >= 0.06
                    and self.frame_number - previous.get("last_cross", -999) >= 12
                ):
                    direction = f"{previous['side']} → {side}"
                    last_cross = self.frame_number
                    crossing_event = {
                        "crossed_at": timestamp,
                        "source": self.source_name,
                        "track_id": int(track_id),
                        "class_name": class_name,
                        "direction": direction,
                        "confidence": float(confidences[index]),
                        "model_name": Path(self.config["model_path"]).name,
                    }
                    crossing_candidates.append(
                        (crossing_event, center_x, center_y)
                    )
                else:
                    last_cross = (
                        previous.get("last_cross", -999) if previous else -999
                    )
                    if previous and previous["side"] != side:
                        confirmed_side = previous["side"]
                self.track_states[track_id] = {
                    "side": confirmed_side,
                    "last_seen": self.frame_number,
                    "last_cross": last_cross,
                    "minimum": minimum,
                    "maximum": maximum,
                    "age": age,
                }

        # Algunos modelos producen dos cajas casi idénticas para el mismo objeto
        # (por ejemplo, truck y car). Si cruzan en el mismo cuadro y punto se
        # contabilizan una sola vez, sin fusionar objetos realmente separados.
        accepted = []
        for event, center_x, center_y in crossing_candidates:
            duplicate = any(
                event["direction"] == accepted_event["direction"]
                and (center_x - accepted_x) ** 2 + (center_y - accepted_y) ** 2
                < 0.025 ** 2
                for accepted_event, accepted_x, accepted_y in accepted
            )
            if not duplicate:
                accepted.append((event, center_x, center_y))

        for event, _, _ in accepted:
            with self._state_lock:
                self.crossing_total += 1
                if event["direction"] == "A → B":
                    self.crossing_ab += 1
                else:
                    self.crossing_ba += 1
                self.crossing_by_class[event["class_name"]] += 1
                self.last_crossing = (
                    f"{format_timestamp_12h(timestamp, False)} · "
                    f"{event['class_name']} · "
                    f"{event['direction']} · ID {event['track_id']}"
                )
            event["evidence_path"] = ""
            manager = self.store.evidence_manager
            # Un cruce es un evento operativo poco frecuente y siempre debe
            # conservar evidencia para el dashboard, igual que una alerta.
            if manager:
                evidence_frame = result.plot(
                    line_width=2, font_size=11
                ).copy()
                self._draw_counting_overlay(evidence_frame)
                event["evidence_path"] = manager.save_image(
                    evidence_frame,
                    timestamp,
                    self.source_name,
                    "cruces_linea",
                    {event["class_name"]: 1},
                    event["confidence"],
                )
            if not event["evidence_path"]:
                self._publish_status(
                    "error",
                    "Cruce detectado, pero no fue posible guardar su captura. "
                    "Consulta errores.log.",
                )
            try:
                event["id"] = self.store.insert_crossing(event)
                self.event_queue.put(
                    {
                        "id": event["id"],
                        "detected_at": timestamp,
                        "source": self.source_name,
                        "total": 1,
                        "classes": {
                            f"↔ CRUCE · {event['class_name']} · "
                            f"{event['direction']}": 1
                        },
                        "max_confidence": event["confidence"],
                        "evidence_path": event["evidence_path"],
                        "model_name": event["model_name"],
                    }
                )
            except sqlite3.Error as exc:
                self._publish_status(
                    "error", f"No se pudo registrar un cruce: {exc}"
                )

        if self.frame_number % 120 == 0:
            with self._state_lock:
                stale_ids = [
                    track_id
                    for track_id, state in self.track_states.items()
                    if self.frame_number - state["last_seen"] > 240
                ]
                for track_id in stale_ids:
                    self.track_states.pop(track_id, None)
