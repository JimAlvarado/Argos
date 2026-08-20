"""Identidad de objetos: respaldo de ids, tracks de vista previa y persistencia.

Extraido de DetectionWorker sin modificar la logica. Es un mixin: conserva el
acceso a los atributos del worker (self.config, self.store, contadores...).
DetectionWorker lo compone; no se instancia por separado.
"""
from __future__ import annotations

import time

import cv2
import numpy as np


class TrackingMixin:
    """Identidad de objetos: respaldo de ids, tracks de vista previa y persistencia."""

    def _fallback_track_ids(
        self, xyxy, class_ids, names, width: int, height: int
    ):
        vehicle_classes = {
            "car", "truck", "bus", "van", "tram", "motorcycle",
            "motorbike", "bicycle", "vehicle",
        }
        assigned_tracks = set()
        output_ids = []
        for index, class_id in enumerate(class_ids):
            center = (
                float((xyxy[index][0] + xyxy[index][2]) / 2 / width),
                float((xyxy[index][1] + xyxy[index][3]) / 2 / height),
            )
            class_name = str(names.get(int(class_id), int(class_id)))
            tracking_group = (
                "vehicle"
                if class_name.strip().casefold() in vehicle_classes
                else class_name.strip().casefold()
            )
            best_id = None
            best_distance = float("inf")
            for track_id, state in self.fallback_tracks.items():
                missing_frames = self.frame_number - state["last_seen"]
                if (
                    track_id in assigned_tracks
                    or state.get("tracking_group") != tracking_group
                    or missing_frames > 18
                ):
                    continue
                predicted_center = (
                    state["center"][0] + state["velocity"][0] * missing_frames,
                    state["center"][1] + state["velocity"][1] * missing_frames,
                )
                distance = (
                    (center[0] - predicted_center[0]) ** 2
                    + (center[1] - predicted_center[1]) ** 2
                ) ** 0.5
                # La ventana crece durante oclusiones, pero la predicción de
                # velocidad evita asociar dos vehículos cercanos por error.
                maximum_distance = min(0.45, 0.28 + missing_frames * 0.02)
                if distance < maximum_distance and distance < best_distance:
                    best_id, best_distance = track_id, distance
            if best_id is None:
                best_id = self.next_fallback_id
                self.next_fallback_id += 1
                velocity = (0.0, 0.0)
            else:
                previous = self.fallback_tracks[best_id]
                elapsed = max(
                    self.frame_number - previous["last_seen"], 1
                )
                observed_velocity = (
                    (center[0] - previous["center"][0]) / elapsed,
                    (center[1] - previous["center"][1]) / elapsed,
                )
                velocity = (
                    previous["velocity"][0] * 0.55
                    + observed_velocity[0] * 0.45,
                    previous["velocity"][1] * 0.55
                    + observed_velocity[1] * 0.45,
                )
            self.fallback_tracks[best_id] = {
                "center": center,
                "velocity": velocity,
                "class_name": class_name,
                "tracking_group": tracking_group,
                "last_seen": self.frame_number,
            }
            assigned_tracks.add(best_id)
            output_ids.append(best_id)

        stale_ids = [
            track_id
            for track_id, state in self.fallback_tracks.items()
            if self.frame_number - state["last_seen"] > 36
        ]
        for track_id in stale_ids:
            self.fallback_tracks.pop(track_id, None)
        return output_ids

    def _update_preview_tracks(
        self, result, frame_shape, track_ids, xyxy, class_ids
    ):
        """Actualiza cajas normalizadas para dibujarlas sobre el video fluido."""
        height, width = frame_shape[:2]
        detections = self._result_detections(result)
        confidences = detections.conf.detach().cpu().numpy()
        now = time.perf_counter()
        with self._state_lock:
            current_ids = set()
            for index, track_id in enumerate(track_ids):
                track_id = int(track_id)
                current_ids.add(track_id)
                box = xyxy[index].astype(float) / np.array(
                    [width, height, width, height], dtype=float
                )
                previous = self.preview_tracks.get(track_id)
                elapsed = now - previous["updated_at"] if previous else 0.0
                if previous and 0.02 <= elapsed <= 5.0:
                    observed_velocity = (box - previous["box"]) / elapsed
                    previous_center = np.array(
                        [
                            (previous["box"][0] + previous["box"][2]) * 0.5,
                            (previous["box"][1] + previous["box"][3]) * 0.5,
                        ]
                    )
                    current_center = np.array(
                        [(box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5]
                    )
                    # Un salto grande suele ser un ID reasignado, no velocidad.
                    if np.linalg.norm(current_center - previous_center) > 0.18:
                        velocity = np.zeros(4, dtype=float)
                    else:
                        velocity = (
                            previous["velocity"] * 0.65
                            + observed_velocity * 0.35
                        )
                        velocity = np.clip(velocity, -0.35, 0.35)
                else:
                    velocity = np.zeros(4, dtype=float)
                self.preview_tracks[track_id] = {
                    "box": box,
                    "velocity": velocity,
                    "class_name": str(
                        result.names.get(
                            int(class_ids[index]), int(class_ids[index])
                        )
                    ),
                    "confidence": float(confidences[index]),
                    "updated_at": now,
                }
            stale_ids = [
                track_id
                for track_id, state in self.preview_tracks.items()
                if track_id not in current_ids
                and now - state["updated_at"] > 0.55
            ]
            for track_id in stale_ids:
                self.preview_tracks.pop(track_id, None)

    def _draw_persistent_tracks(self, frame, result):
        boxes = self._result_detections(result)
        current_ids = set()
        if boxes is not None and len(boxes):
            track_ids = (
                boxes.id.detach().cpu().numpy().astype(int)
                if boxes.id is not None
                else self.current_effective_track_ids
            )
            xyxy = boxes.xyxy.detach().cpu().numpy()
            class_ids = boxes.cls.detach().cpu().numpy().astype(int)
            confidences = boxes.conf.detach().cpu().numpy()
            for index, track_id in enumerate(track_ids):
                current_ids.add(int(track_id))
                box = xyxy[index].astype(float)
                previous = self.display_tracks.get(int(track_id))
                velocity = (
                    box - previous["box"]
                    if previous and self.frame_number - previous["last_seen"] == 1
                    else box * 0
                )
                self.display_tracks[int(track_id)] = {
                    "box": box,
                    "velocity": velocity,
                    "class_name": str(
                        result.names.get(
                            int(class_ids[index]), int(class_ids[index])
                        )
                    ),
                    "confidence": float(confidences[index]),
                    "last_seen": self.frame_number,
                }

        height, width = frame.shape[:2]
        stale_ids = []
        for track_id, state in self.display_tracks.items():
            if track_id in current_ids:
                continue
            missing_frames = self.frame_number - state["last_seen"]
            if missing_frames > 3:
                stale_ids.append(track_id)
                continue
            # Mantiene brevemente la última caja confirmada sin proyectarla.
            # La vista fluida ya realiza una predicción de centro acotada.
            predicted = state["box"]
            x1, y1, x2, y2 = predicted.astype(int)
            x1, x2 = max(0, x1), min(width - 1, x2)
            y1, y2 = max(0, y1), min(height - 1, y2)
            if x2 <= x1 or y2 <= y1:
                continue
            cv2.rectangle(
                frame, (x1, y1), (x2, y2), (70, 170, 255), 2, cv2.LINE_AA
            )
            display_id = (
                f"F{track_id - 999_999}" if track_id >= 1_000_000 else str(track_id)
            )
            label = (
                f"{state['class_name']} ID {display_id} "
                f"{state['confidence']:.0%}"
            )
            cv2.putText(
                frame, label, (x1, max(18, y1 - 5)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.48, (70, 170, 255),
                1, cv2.LINE_AA
            )
        for track_id in stale_ids:
            self.display_tracks.pop(track_id, None)
