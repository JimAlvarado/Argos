"""Dibujo sobre el cuadro: zona, linea de conteo y mosaico de detecciones.

Extraido de DetectionWorker sin modificar la logica. Es un mixin: conserva el
acceso a los atributos del worker (self.config, self.store, contadores...).
DetectionWorker lo compone; no se instancia por separado.
"""
from __future__ import annotations

import cv2
import numpy as np


class OverlayMixin:
    """Dibujo sobre el cuadro: zona, linea de conteo y mosaico de detecciones."""

    def _detection_mosaic(self, frame, result):
        detections = self._result_detections(result)
        if detections is None or len(detections) == 0:
            return None
        confidences = detections.conf.detach().cpu().numpy()
        indices = confidences.argsort()[::-1][:4]
        height, width = frame.shape[:2]
        tile_width, tile_height = 320, 170
        mosaic = frame[0:1, 0:1].copy()
        mosaic = cv2.resize(mosaic, (tile_width * 2, tile_height * 2))
        mosaic[:] = (10, 14, 18)
        for tile_index, box_index in enumerate(indices):
            box = detections[int(box_index)]
            x1, y1, x2, y2 = box.xyxy[0].detach().cpu().numpy().astype(int)
            pad_x = max((x2 - x1) // 10, 8)
            pad_y = max((y2 - y1) // 10, 8)
            x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
            x2, y2 = min(width, x2 + pad_x), min(height, y2 + pad_y)
            crop = frame[y1:y2, x1:x2]
            if not crop.size:
                continue
            ratio = min(tile_width / crop.shape[1], (tile_height - 24) / crop.shape[0])
            resized = cv2.resize(
                crop,
                (
                    max(1, int(crop.shape[1] * ratio)),
                    max(1, int(crop.shape[0] * ratio)),
                ),
                interpolation=cv2.INTER_AREA,
            )
            row, column = divmod(tile_index, 2)
            tile_x, tile_y = column * tile_width, row * tile_height
            offset_x = tile_x + (tile_width - resized.shape[1]) // 2
            offset_y = tile_y + 24 + (tile_height - 24 - resized.shape[0]) // 2
            mosaic[
                offset_y:offset_y + resized.shape[0],
                offset_x:offset_x + resized.shape[1],
            ] = resized
            class_id = int(box.cls[0].item())
            class_name = str(result.names.get(class_id, class_id))
            confidence = float(box.conf[0].item())
            track_id = (
                int(box.id[0].item())
                if getattr(box, "id", None) is not None
                else None
            )
            label = f"{class_name} {confidence:.0%}"
            if track_id is not None:
                label += f"  ID {track_id}"
            cv2.putText(
                mosaic, label, (tile_x + 7, tile_y + 17),
                cv2.FONT_HERSHEY_SIMPLEX, 0.48, (102, 230, 170), 1, cv2.LINE_AA
            )
        return mosaic

    def _draw_zone_overlay(self, frame):
        with self._state_lock:
            enabled = self.zone_enabled
            high_danger = self.high_danger_zone
            points = [point[:] for point in self.zone_points]
        if not enabled or len(points) < 3:
            return
        height, width = frame.shape[:2]
        polygon = np.asarray(
            [
                [int(point[0] * width), int(point[1] * height)]
                for point in points
            ],
            dtype=np.int32,
        )
        overlay = frame.copy()
        fill_color = (36, 36, 200) if high_danger else (24, 92, 168)
        line_color = (70, 70, 255) if high_danger else (54, 174, 255)
        text_color = (90, 90, 255) if high_danger else (84, 195, 255)
        cv2.fillPoly(overlay, [polygon], fill_color, cv2.LINE_AA)
        cv2.addWeighted(overlay, 0.22, frame, 0.78, 0, frame)
        cv2.polylines(
            frame, [polygon], True, (7, 12, 18), 7, cv2.LINE_AA
        )
        cv2.polylines(
            frame, [polygon], True, line_color, 3, cv2.LINE_AA
        )
        for point in polygon:
            cv2.circle(
                frame, tuple(point), 5, (255, 255, 255), -1, cv2.LINE_AA
            )
            cv2.circle(
                frame, tuple(point), 8, line_color, 2, cv2.LINE_AA
            )
        label_x = int(polygon[:, 0].min())
        label_y = max(28, int(polygon[:, 1].min()) - 10)
        label = "ZONA DE ALTO PELIGRO" if high_danger else "ZONA DE ALERTA"
        (text_width, text_height), _ = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1
        )
        cv2.rectangle(
            frame,
            (label_x, label_y - text_height - 9),
            (label_x + text_width + 16, label_y + 5),
            (14, 25, 36),
            -1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame, label, (label_x + 8, label_y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, text_color,
            1, cv2.LINE_AA,
        )

    def _draw_counting_overlay(self, frame):
        with self._state_lock:
            enabled = self.line_enabled
            points = [point[:] for point in self.line_points]
            total = self.crossing_total
            ab = self.crossing_ab
            ba = self.crossing_ba
        if not enabled:
            return
        height, width = frame.shape[:2]
        point_a = (int(points[0][0] * width), int(points[0][1] * height))
        point_b = (int(points[1][0] * width), int(points[1][1] * height))
        cv2.arrowedLine(
            frame, point_a, point_b, (48, 222, 151),
            2, cv2.LINE_AA, tipLength=0.025
        )
        for point, color in (
            (point_a, (67, 169, 255)),
            (point_b, (48, 222, 151)),
        ):
            cv2.circle(frame, point, 11, (7, 12, 18), -1, cv2.LINE_AA)
            cv2.circle(frame, point, 8, color, -1, cv2.LINE_AA)
            cv2.circle(frame, point, 3, (255, 255, 255), -1, cv2.LINE_AA)
        delta_x, delta_y = point_b[0] - point_a[0], point_b[1] - point_a[1]
        pixel_length = max((delta_x ** 2 + delta_y ** 2) ** 0.5, 1.0)
        normal_x, normal_y = -delta_y / pixel_length, delta_x / pixel_length
        middle_x = (point_a[0] + point_b[0]) // 2
        middle_y = (point_a[1] + point_b[1]) // 2
        is_vertical = abs(delta_y) >= abs(delta_x)
        label_positions = (
            (("A", 1), ("B", -1))
            if is_vertical
            else (("A", -1), ("B", 1))
        )
        for side_label, multiplier in label_positions:
            label_x = int(middle_x + normal_x * 28 * multiplier)
            label_y = int(middle_y + normal_y * 28 * multiplier)
            cv2.circle(
                frame, (label_x, label_y - 5), 14,
                (14, 25, 36), -1, cv2.LINE_AA
            )
            cv2.circle(
                frame, (label_x, label_y - 5), 14,
                (48, 222, 151), 2, cv2.LINE_AA
            )
            cv2.putText(
                frame, side_label, (label_x - 5, label_y + 1),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255),
                2, cv2.LINE_AA
            )
        label = f"CRUCES {total}   A>B {ab}   B>A {ba}"
        text_y = max(28, min(point_a[1], point_b[1]) - 10)
        text_x = max(8, min(point_a[0], point_b[0]))
        (text_width, text_height), _ = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, 0.60, 2
        )
        cv2.rectangle(
            frame,
            (text_x, text_y - text_height - 10),
            (text_x + text_width + 18, text_y + 6),
            (14, 25, 36),
            -1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame, label, (text_x + 9, text_y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.60, (48, 222, 151), 2, cv2.LINE_AA
        )
