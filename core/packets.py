"""Estructuras que viajan entre el worker y la interfaz.

Extraido de detector_empresarial.py sin modificar la logica.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FramePacket:
    frame: object
    crop: object | None
    counts: dict[str, int]
    total: int
    fps: float
    latency_ms: float
    timestamp: str
    crossing_total: int
    crossing_ab: int
    crossing_ba: int
    crossing_by_class: dict[str, int]
    last_crossing: str


@dataclass
class PreviewPacket:
    frame: object
    timestamp: str
