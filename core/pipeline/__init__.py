"""Piezas del ciclo de inferencia, una por responsabilidad."""
from core.pipeline.crossing import CrossingMixin
from core.pipeline.dedup import SceneDeduplicator
from core.pipeline.overlay import OverlayMixin
from core.pipeline.tracking import TrackingMixin
from core.pipeline.zones import ZoneMixin

__all__ = ["CrossingMixin", "SceneDeduplicator", "OverlayMixin", "TrackingMixin", "ZoneMixin"]
