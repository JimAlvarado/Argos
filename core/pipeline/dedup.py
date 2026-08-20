"""Evita guardar la misma evidencia una y otra vez.

Sin esto, una escena estatica genera una captura cada `event_interval` segundos
de forma indefinida: dos personas quietas producen 1,200 imagenes por hora, casi
identicas entre si, y terminan saturando el disco.

La regla es guardar por **cambio de escena**, no por reloj:

- Aparece un objeto nuevo (un identificador de seguimiento no visto) -> guarda.
- Cambia la composicion (otras clases o distinto conteo) -> guarda.
- Nada cambia -> no guarda, hasta que pase el periodo de refresco.

El refresco existe para que una vigilancia larga siga dejando constancia
periodica en lugar de quedarse en silencio durante horas.
"""
from __future__ import annotations


class SceneDeduplicator:
    """Decide si el cuadro actual merece una evidencia nueva."""

    def __init__(self, refresh_seconds: float = 300.0, enabled: bool = True):
        self.refresh_seconds = float(refresh_seconds)
        self.enabled = bool(enabled)
        self._last_signature = None
        self._last_saved_at = 0.0
        self.omitidas = 0  # cuantas se evitaron; util para diagnostico

    @staticmethod
    def signature(track_ids, counts) -> tuple:
        """Huella de la escena: que objetos hay y de que clase.

        Se usan los identificadores de seguimiento porque son lo que distingue
        "las mismas dos personas de siempre" de "entro alguien nuevo". Si el
        modelo no entrega identificadores, el conteo por clase sirve de respaldo.
        """
        objetos = tuple(sorted(str(t) for t in (track_ids or []) if t is not None))
        clases = tuple(sorted((str(k), int(v)) for k, v in (counts or {}).items()))
        return objetos, clases

    def should_record(self, track_ids, counts, now: float) -> bool:
        if not self.enabled:
            return True

        firma = self.signature(track_ids, counts)

        if firma != self._last_signature:
            self._last_signature = firma
            self._last_saved_at = now
            return True

        if self.refresh_seconds > 0 and now - self._last_saved_at >= self.refresh_seconds:
            self._last_saved_at = now
            return True

        self.omitidas += 1
        return False

    def reset(self) -> None:
        """Olvida la escena anterior. Se llama al reiniciar la deteccion."""
        self._last_signature = None
        self._last_saved_at = 0.0
        self.omitidas = 0
