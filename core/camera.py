"""Apertura de fuentes y lector que conserva solo el cuadro mas reciente.

`LatestFrameReader` viene de detector_empresarial.py sin modificar la logica.
`abrir_fuente` es la forma oficial de abrir una fuente de video.

Nota de deuda consciente: `detector_objetos.ContadorWorker._abrir` hace lo mismo
y todavia no se migro aqui. Se dejo tal cual a proposito porque ese modulo esta
contando lingotes en produccion, verificado 15/15, y cambiarle la apertura de
camara en el mismo paso que se agrega un modulo nuevo mezcla dos riesgos.
Migrarlo es un cambio aparte, aislado y con su propia verificacion.
"""
from __future__ import annotations

import threading
import time

import cv2


def abrir_fuente(fuente, timeout_ms: int = 4000):
    """Abre la fuente probando cada ruta candidata. Devuelve (captura, usada).

    La fuente puede ser una LISTA de rutas: algunas marcas publican el flujo en
    perfiles distintos (Provision usa profile1, profile2 o profile3) y no se
    sabe cual responde hasta intentarlo. Pasar la lista a OpenCV falla siempre;
    hay que probarlas de una en una. Es un contrato del proyecto, no un detalle.

    El timeout es indispensable: sin el, una IP que no responde deja el modulo
    colgado en `VideoCapture` sin latir, y el supervisor lo reinicia en bucle.
    """
    candidatas = fuente if isinstance(fuente, list) else [fuente]
    for candidata in candidatas:
        if isinstance(candidata, str) and candidata.startswith("rtsp://"):
            captura = cv2.VideoCapture(
                candidata, cv2.CAP_FFMPEG,
                [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms,
                 cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000],
            )
        else:
            captura = cv2.VideoCapture(candidata)
        if captura.isOpened():
            return captura, candidata
        captura.release()
    raise RuntimeError(
        "No se pudo abrir la fuente. Revisa IP, usuario y contraseña.\n"
        f"{ocultar_credenciales(candidatas[0])}"
    )


def ocultar_credenciales(origen) -> str:
    """Version mostrable de una URL: nunca se ensena la contrasena."""
    texto = str(origen)
    if texto.startswith("rtsp://") and "@" in texto:
        return "rtsp://***@" + texto.split("@", 1)[1]
    return texto


class LatestFrameReader(threading.Thread):
    """Lee la cámara continuamente y conserva sólo el cuadro más reciente."""

    def __init__(
        self, capture, parent_stop_event: threading.Event,
        preview_callback=None, preview_fps: float = 20.0
    ):
        super().__init__(daemon=True, name="latest-frame-reader")
        self.capture = capture
        self.parent_stop_event = parent_stop_event
        self.local_stop_event = threading.Event()
        self.lock = threading.Lock()
        self.frame = None
        self.sequence = 0
        self.captured_at = 0.0
        self.disconnected = False
        self.preview_callback = preview_callback
        self.preview_period = 1.0 / max(preview_fps, 1.0)
        self.next_preview_at = 0.0

    def stop(self):
        self.local_stop_event.set()

    def latest_after(self, sequence: int):
        with self.lock:
            if self.frame is None or self.sequence == sequence:
                return None
            return self.sequence, self.frame, self.captured_at

    def run(self):
        failures = 0
        try:
            while (
                not self.parent_stop_event.is_set()
                and not self.local_stop_event.is_set()
            ):
                ok, frame = self.capture.read()
                if not ok:
                    failures += 1
                    if failures >= 20:
                        self.disconnected = True
                        break
                    self.local_stop_event.wait(0.02)
                    continue
                failures = 0
                with self.lock:
                    self.frame = frame
                    self.sequence += 1
                    self.captured_at = time.perf_counter()
                    captured_at = self.captured_at
                if (
                    self.preview_callback
                    and captured_at >= self.next_preview_at
                ):
                    self.next_preview_at = captured_at + self.preview_period
                    self.preview_callback(frame, captured_at)
        finally:
            # Este hilo es el único propietario de la captura en vivo. Liberarla
            # desde el worker mientras FFmpeg ejecuta read() causa APPCRASH.
            self.capture.release()
