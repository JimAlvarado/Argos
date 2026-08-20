"""Interfaz del detector, dividida por responsabilidad."""
from ui.alarms import AlarmsMixin
from ui.geometry import GeometryMixin
from ui.layout import LayoutMixin
from ui.models import ModelMixin

__all__ = ["AlarmsMixin", "GeometryMixin", "LayoutMixin", "ModelMixin"]
