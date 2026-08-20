"""Latido de vida de cada modulo.

Cada proceso escribe su latido en un archivo propio dentro de
`data/heartbeats/`; el kernel lo lee para distinguir un modulo trabajando de
uno congelado. Archivo por modulo (no SQLite) por dos razones: evita que dos
procesos compitan por la base, y sobrevive aunque la base este bloqueada, que
es justo uno de los escenarios de falla que el kernel debe detectar.
"""
from __future__ import annotations

import json
import os
import threading
import time

from core import paths

HEARTBEAT_DIR = paths.DATA_DIR / "heartbeats"


def _archivo(modulo: str):
    return HEARTBEAT_DIR / f"{modulo}.json"


class HeartbeatWriter(threading.Thread):
    """Hilo demonio que late cada `interval` segundos.

    Corre en un hilo separado de la interfaz a proposito: si el proceso entero
    muere o queda congelado a nivel de sistema, el latido se detiene y el
    kernel lo nota. Nunca lanza excepciones hacia afuera.
    """

    def __init__(self, modulo: str, interval: float = 1.0):
        super().__init__(daemon=True, name=f"heartbeat-{modulo}")
        self.modulo = modulo
        self.interval = float(interval)
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            beat(self.modulo)
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()


def beat(modulo: str) -> None:
    """Escribe un latido. Nunca lanza: un fallo aqui no debe tumbar al modulo."""
    try:
        HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
        destino = _archivo(modulo)
        temporal = destino.with_suffix(".tmp")
        temporal.write_text(
            json.dumps({"module": modulo, "pid": os.getpid(), "at": time.time()}),
            encoding="utf-8",
        )
        # Reemplazo atomico: el lector nunca ve un archivo a medio escribir.
        os.replace(temporal, destino)
    except Exception:
        pass


def age(modulo: str) -> float | None:
    """Segundos desde el ultimo latido, o None si nunca ha latido."""
    try:
        datos = json.loads(_archivo(modulo).read_text(encoding="utf-8"))
        return max(0.0, time.time() - float(datos["at"]))
    except Exception:
        return None


def clear(modulo: str) -> None:
    """Borra el latido (al arrancar un modulo, para no leer latidos viejos)."""
    try:
        _archivo(modulo).unlink(missing_ok=True)
    except OSError:
        pass
