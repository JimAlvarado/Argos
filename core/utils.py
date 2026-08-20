"""Utilidades sin dependencias de interfaz.

Extraido de detector_empresarial.py sin modificar la logica.
"""
from __future__ import annotations

from datetime import datetime


def format_timestamp_12h(timestamp: str, include_date: bool = True) -> str:
    try:
        moment = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return str(timestamp)
    pattern = "%Y-%m-%d  %I:%M:%S %p" if include_date else "%I:%M:%S %p"
    return moment.strftime(pattern)


def formato_duracion(segundos: float) -> str:
    """Duracion legible para el operador: "8m 26s", "1h 04m", "45s".

    Vive aqui y no en `core/pipeline/estados.py` porque tambien la necesita
    `core/storage.py` para rotular los eventos, y storage esta por debajo del
    pipeline: al reves la dependencia quedaria invertida.
    """
    total = int(round(max(0.0, segundos)))
    horas, resto = divmod(total, 3600)
    minutos, segs = divmod(resto, 60)
    if horas:
        return f"{horas}h {minutos:02d}m"
    if minutos:
        return f"{minutos}m {segs:02d}s"
    return f"{segs}s"

