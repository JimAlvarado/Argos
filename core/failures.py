"""Registro exclusivo de fallas.

Guarda unicamente errores en data/logs/. No registra actividad normal: si un
archivo de este directorio crece, algo esta fallando.

Uso basico:

    from core import failures

    failures.configure("detector")          # una vez, al arrancar el proceso
    failures.record("camara", "RTSP caido", exc=error, url=direccion)

    with failures.capture("inferencia"):    # registra y relanza
        modelo.predict(cuadro)

Revisar despues:

    python -m core.failures                 # ultimas 20 fallas
    python -m core.failures --ultimas 100
    python -m core.failures --modulo detector
"""
from __future__ import annotations

import json
import sys
import threading
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from core import paths

# Carpeta dedicada. Se crea sola la primera vez que se registra algo.
LOG_DIR = paths.DATA_DIR / "logs"
DIAS_DE_RETENCION = 30
SEPARADOR = "-" * 78

_lock = threading.Lock()
_modulo_actual = "desconocido"


def _archivo_del_dia(modulo: str) -> Path:
    """Un archivo por modulo y por dia.

    Separar por modulo evita que dos procesos escriban el mismo archivo, que en
    Windows provoca bloqueos. Calcular la fecha en cada escritura permite que un
    proceso encendido varios dias siga escribiendo en el archivo correcto.
    """
    fecha = datetime.now().strftime("%Y-%m-%d")
    return LOG_DIR / f"fallas_{modulo}_{fecha}.log"


def configure(modulo: str, retencion_dias: int = DIAS_DE_RETENCION) -> Path:
    """Define el nombre del proceso y captura cualquier error no atrapado.

    Devuelve la carpeta de registros. Llamar una sola vez al arrancar.
    """
    global _modulo_actual
    _modulo_actual = modulo
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _limpiar_antiguos(retencion_dias)
    _instalar_capturas_globales()
    return LOG_DIR


def record(
    componente: str,
    mensaje: str,
    exc: BaseException | None = None,
    nivel: str = "ERROR",
    **contexto,
) -> None:
    """Registra una falla. Nunca lanza excepciones: no puede tumbar al que llama."""
    try:
        momento = datetime.now()
        lineas = [
            f"{momento:%Y-%m-%d %H:%M:%S} | {nivel} | {_modulo_actual} | "
            f"{componente} | {mensaje}"
        ]
        if contexto:
            lineas.append(f"  contexto: {json.dumps(contexto, ensure_ascii=False, default=str)}")
        if exc is not None:
            lineas.append(f"  excepcion: {type(exc).__name__}: {exc}")
            detalle = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ).rstrip()
            if detalle:
                lineas.extend(f"  {linea}" for linea in detalle.splitlines())
        lineas.append(SEPARADOR)

        destino = _archivo_del_dia(_modulo_actual)
        with _lock:
            destino.parent.mkdir(parents=True, exist_ok=True)
            with destino.open("a", encoding="utf-8") as archivo:
                archivo.write("\n".join(lineas) + "\n")
    except Exception:
        # Si el registro falla, se ignora: nunca debe romper la aplicacion.
        pass


@contextmanager
def capture(componente: str, mensaje: str = "", relanzar: bool = True):
    """Registra la falla que ocurra dentro del bloque.

    Con relanzar=False el error se traga y el programa continua. Usar solo donde
    seguir sin ese resultado sea aceptable (por ejemplo, guardar una evidencia).
    """
    try:
        yield
    except Exception as error:
        record(componente, mensaje or "fallo no controlado", exc=error)
        if relanzar:
            raise


def _instalar_capturas_globales() -> None:
    """Atrapa errores que hoy se pierden: hilos e interfaz incluidos."""
    anterior_sys = sys.excepthook

    def _hook_principal(tipo, valor, rastro):
        record("proceso", "error no atrapado en el hilo principal", exc=valor)
        anterior_sys(tipo, valor, rastro)

    sys.excepthook = _hook_principal

    anterior_hilos = threading.excepthook

    def _hook_hilos(args):
        record(
            "hilo",
            f"error no atrapado en el hilo {getattr(args.thread, 'name', '?')}",
            exc=args.exc_value,
        )
        anterior_hilos(args)

    threading.excepthook = _hook_hilos


def _limpiar_antiguos(dias: int) -> int:
    """Borra registros mas viejos que el limite. Devuelve cuantos elimino."""
    if dias <= 0 or not LOG_DIR.exists():
        return 0
    limite = datetime.now() - timedelta(days=dias)
    borrados = 0
    for archivo in LOG_DIR.glob("fallas_*.log"):
        try:
            if datetime.fromtimestamp(archivo.stat().st_mtime) < limite:
                archivo.unlink()
                borrados += 1
        except OSError:
            continue
    return borrados


def leer_recientes(cantidad: int = 20, modulo: str | None = None) -> list[str]:
    """Devuelve las ultimas fallas registradas, de la mas reciente hacia atras."""
    if not LOG_DIR.exists():
        return []
    patron = f"fallas_{modulo}_*.log" if modulo else "fallas_*.log"
    archivos = sorted(LOG_DIR.glob(patron), key=lambda p: p.stat().st_mtime, reverse=True)
    bloques: list[str] = []
    for archivo in archivos:
        try:
            texto = archivo.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for bloque in reversed(texto.split(SEPARADOR)):
            bloque = bloque.strip()
            if bloque:
                bloques.append(bloque)
            if len(bloques) >= cantidad:
                return bloques
    return bloques


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Revisa el registro de fallas.")
    parser.add_argument("--ultimas", type=int, default=20, help="cuantas fallas mostrar")
    parser.add_argument("--modulo", default=None, help="filtra por modulo")
    argumentos = parser.parse_args()

    bloques = leer_recientes(argumentos.ultimas, argumentos.modulo)
    if not bloques:
        print(f"Sin fallas registradas en {LOG_DIR}")
        return 0
    print(f"Ultimas {len(bloques)} fallas en {LOG_DIR}\n")
    try:
        for bloque in bloques:
            print(bloque)
            print(SEPARADOR)
    except BrokenPipeError:
        # Ocurre al canalizar la salida (por ejemplo con more o findstr).
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
