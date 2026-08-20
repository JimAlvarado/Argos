"""Validacion geometrica de la linea de conteo y de la zona.

Funciones puras: reciben puntos normalizados y devuelven puntos validos.
Las usan el worker y la interfaz, por eso viven aqui y no dentro de
DetectionWorker (evita una dependencia circular).
"""
from __future__ import annotations

import cv2
import numpy as np


def validated_line_points(points):
    try:
        if len(points) != 2 or any(len(point) != 2 for point in points):
            raise ValueError
        normalized = [
            [min(max(float(point[0]), 0.0), 1.0),
             min(max(float(point[1]), 0.0), 1.0)]
            for point in points
        ]
        delta_x = abs(normalized[1][0] - normalized[0][0])
        delta_y = abs(normalized[1][1] - normalized[0][1])
        if (
            delta_y >= delta_x and normalized[0][1] > normalized[1][1]
        ) or (
            delta_x > delta_y and normalized[0][0] > normalized[1][0]
        ):
            normalized.reverse()
        return normalized
    except (TypeError, ValueError):
        return [[0.10, 0.50], [0.90, 0.50]]

@staticmethod


def validated_zone_points(points):
    try:
        if len(points) < 3 or any(len(point) != 2 for point in points):
            raise ValueError
        normalized = [
            [
                min(max(float(point[0]), 0.0), 1.0),
                min(max(float(point[1]), 0.0), 1.0),
            ]
            for point in points
        ]
        contour = np.asarray(normalized, dtype=np.float32)
        if abs(cv2.contourArea(contour)) < 0.0025:
            raise ValueError
        return normalized
    except (TypeError, ValueError):
        return [
            [0.20, 0.20], [0.80, 0.20],
            [0.80, 0.80], [0.20, 0.80],
        ]
