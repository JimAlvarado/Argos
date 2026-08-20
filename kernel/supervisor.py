"""Supervisor del kernel: vigila los modulos y reinicia al que se cae.

Reglas de decision, en orden:

1. El proceso termino con codigo 0  -> cierre intencional del operador. No se
   reinicia; estado "detenido".
2. El proceso termino con otro codigo -> caida. Se reinicia con espera
   creciente (2, 4, 8 segundos) hasta `max_reintentos`; despues, "caido".
3. El proceso vive pero su latido tiene mas de `latido_maximo` segundos ->
   congelado. Se termina el proceso y cuenta como caida (regla 2).

Cada reinicio y cada modulo dado por caido quedan en el registro de fallas.
El resto del sistema no se entera: un modulo caido jamas tumba a los demas.
"""
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from core import failures, heartbeat


@dataclass
class Vigilado:
    proceso: subprocess.Popen
    relanzar: Callable[[], subprocess.Popen]
    estado: str = "activo"            # activo | reiniciando | detenido | caido
    reinicios: int = 0
    arranco_en: float = field(default_factory=time.monotonic)
    proximo_intento: float = 0.0
    # Un modulo solo puede considerarse congelado si alguna vez latio. Sin este
    # dato, "nunca reporto" se confunde con "dejo de reportar" y se termina un
    # proceso sano en bucle.
    latio_alguna_vez: bool = False
    aviso_sin_latido: bool = False


class Supervisor(threading.Thread):
    def __init__(
        self,
        latido_maximo: float = 10.0,
        gracia_arranque: float = 20.0,
        max_reintentos: int = 3,
        espera_base: float = 2.0,
        ciclo: float = 1.0,
    ):
        super().__init__(daemon=True, name="kernel-supervisor")
        self.latido_maximo = float(latido_maximo)
        self.gracia_arranque = float(gracia_arranque)
        self.max_reintentos = int(max_reintentos)
        self.espera_base = float(espera_base)
        self.ciclo = float(ciclo)
        self._vigilados: dict[str, Vigilado] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()

    # ---- registro -------------------------------------------------------------
    def register(
        self, modulo: str, proceso: subprocess.Popen,
        relanzar: Callable[[], subprocess.Popen],
    ) -> None:
        heartbeat.clear(modulo)
        with self._lock:
            previo = self._vigilados.get(modulo)
            reinicios = previo.reinicios if previo and previo.estado != "detenido" else 0
            self._vigilados[modulo] = Vigilado(
                proceso=proceso, relanzar=relanzar, reinicios=reinicios
            )

    def estado_de(self, modulo: str) -> dict | None:
        with self._lock:
            v = self._vigilados.get(modulo)
            if v is None:
                return None
            return {
                "estado": v.estado,
                "reinicios": v.reinicios,
                "latido_hace": heartbeat.age(modulo),
            }

    # ---- ciclo de vigilancia ----------------------------------------------------
    def run(self) -> None:
        while not self._stop.is_set():
            self._revisar()
            self._stop.wait(self.ciclo)

    def stop(self) -> None:
        self._stop.set()

    def _revisar(self) -> None:
        ahora = time.monotonic()
        with self._lock:
            elementos = list(self._vigilados.items())
        for modulo, v in elementos:
            try:
                self._revisar_uno(modulo, v, ahora)
            except Exception as exc:
                # El supervisor no puede morir por un modulo problematico.
                failures.record("supervisor", f"error vigilando {modulo}", exc=exc)

    def _revisar_uno(self, modulo: str, v: Vigilado, ahora: float) -> None:
        if v.estado in ("detenido", "caido"):
            return

        if v.estado == "reiniciando":
            if ahora >= v.proximo_intento:
                self._relanzar(modulo, v)
            return

        codigo = v.proceso.poll()
        if codigo is None:
            # Vivo. ¿Congelado? Solo despues de la gracia de arranque, porque
            # cargar torch puede tardar y aun no hay latidos.
            edad = heartbeat.age(modulo)
            if edad is not None:
                v.latio_alguna_vez = True
            paso_gracia = ahora - v.arranco_en > self.gracia_arranque

            if paso_gracia and not v.latio_alguna_vez:
                # El modulo no reporta: puede no implementar latidos o hacerlo
                # con otro nombre. Se vigila solo por proceso vivo y se avisa
                # una vez. Terminarlo seria destruir algo que funciona.
                if not v.aviso_sin_latido:
                    v.aviso_sin_latido = True
                    failures.record(
                        "supervisor",
                        f"{modulo} no reporta latido; se vigila solo por proceso "
                        "vivo. Revisa que el modulo lata con este mismo nombre.",
                        nivel="WARNING",
                    )
                return

            if paso_gracia and edad is not None and edad > self.latido_maximo:
                failures.record(
                    "supervisor",
                    f"{modulo} congelado (latido hace {edad:.0f}s); se termina",
                    nivel="WARNING",
                )
                try:
                    v.proceso.terminate()
                except OSError:
                    pass
                self._programar_reintento(modulo, v, ahora)
            return

        if codigo == 0:
            v.estado = "detenido"          # cierre intencional del operador
            return

        failures.record(
            "supervisor", f"{modulo} termino con codigo {codigo}", nivel="WARNING"
        )
        self._programar_reintento(modulo, v, ahora)

    def _programar_reintento(self, modulo: str, v: Vigilado, ahora: float) -> None:
        if v.reinicios >= self.max_reintentos:
            v.estado = "caido"
            failures.record(
                "supervisor",
                f"{modulo} caido tras {v.reinicios} reintentos; requiere revision",
            )
            return
        espera = self.espera_base * (2 ** v.reinicios)
        v.estado = "reiniciando"
        v.proximo_intento = ahora + espera

    def _relanzar(self, modulo: str, v: Vigilado) -> None:
        v.reinicios += 1
        heartbeat.clear(modulo)
        try:
            v.proceso = v.relanzar()
        except Exception as exc:
            failures.record("supervisor", f"no se pudo relanzar {modulo}", exc=exc)
            v.estado = "caido"
            return
        v.estado = "activo"
        v.arranco_en = time.monotonic()
        failures.record(
            "supervisor",
            f"{modulo} reiniciado automaticamente (intento {v.reinicios})",
            nivel="WARNING",
        )
