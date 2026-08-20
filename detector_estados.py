"""Modulo de estados con duracion: mantenedor, tolva y horno.

UN solo script para las tres estaciones, lanzado como TRES procesos. La
estacion sale de `ARZYZ_MODULE_ID`, que el centro de control ya inyecta y con
el que cada modulo late. Asi hay una sola base de codigo y, a la vez,
aislamiento de fallos: si el RTSP de una camara muere, el supervisor reinicia
solo ese proceso y las otras siguen midiendo.

Se lanza desde el centro de control o por su cuenta:

    python detector_estados.py                     (mantenedor por defecto)
    set ARZYZ_MODULE_ID=mantenedor && python detector_estados.py

Estado de las estaciones (19-ago-2026): solo `mantenedor` tiene senal validada
contra video real. Tolva y horno estan definidas pero marcadas como no
calibradas: el modulo se niega a arrancar en una estacion sin calibrar antes de
inventar un umbral, porque en este proyecto los umbrales salen de mediciones.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox

import cv2
import customtkinter as ctk
import numpy as np
from PIL import Image

from core import failures, paths
from core.camera import abrir_fuente, ocultar_credenciales
from core.config import load_config, save_config
from core.evidence import EvidenceManager
from core.paths import APP_NAME
from core.pipeline.estados import Histeresis, MaquinaDeEstado
# Las senales y la calibracion viven en el pipeline, no aqui: las comparte
# `tools/calibrar_estado.py`, y una herramienta de consola no debe arrastrar
# Tkinter solo para leer un umbral. Se reexportan para no romper a quien ya
# importa `detector_estados.ESTACIONES`.
from core.pipeline.senales import (  # noqa: F401
    ESTACIONES,
    SENALES,
    recortar,
    senal_rosado,
)
from core.storage import EventStore
from core.utils import formato_duracion
from ui.source import PanelDeFuente
from ui.widgets import MetricCard, SeccionDesplegable

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


# Muestreo de la senal. 2 Hz basta de sobra para una puerta que se abre por
# minutos, y deja el 95% del CPU libre: no hay razon para mirar 25 veces por
# segundo algo que cambia cada varios minutos.
HZ_ANALISIS = 2.0

# Desplazamiento global entre muestras CONSECUTIVAS, en pixeles sobre un ancho
# de trabajo de 640, a partir del cual se considera que la CAMARA se movio.
#
# Consecutivas y NO contra una referencia fija del arranque. Con referencia fija
# esto no funciona, y se comprobo midiendo: al abrirse la puerta aparece una
# region magenta enorme, la correlacion de fase pierde su pico en cero y el
# desplazamiento salta a 68, 97 y hasta 464 px con la camara perfectamente
# quieta. Es decir, detectaba el CAMBIO DE ESCENA, que es justo lo que se quiere
# medir. Entre muestras consecutivas la escena apenas cambia y un giro de la PTZ
# si destaca.
#
# Medido en el video del 19-ago con la PTZ quieta, comparando consecutivas:
# maximo 0.07 px en 10 minutos, incluida la apertura completa de la puerta. Con
# 2.0 px queda 28 veces de margen sobre el ruido real.
DESPLAZAMIENTO_MAXIMO = 2.0
ANCHO_REGISTRO = 640

# Segundos que el cuadro debe quedarse quieto para volver a confiar en el
# recorte despues de un movimiento. Comparar contra una imagen de referencia
# para confirmar que la camara "volvio" no es viable: la misma medicion de
# arriba demuestra que esa comparacion se confunde con los cambios legitimos de
# la escena. Asi que no se afirma que la vista sea la correcta: se avisa y el
# intervalo queda marcado con hueco para que el dato no se lea como limpio.
SEGUNDOS_DE_ASENTAMIENTO = 3.0


class VigilanteDeEstado(threading.Thread):
    """Lee la fuente, mide la senal y publica los cambios de estado."""

    def __init__(self, estacion: str, fuente, nombre_fuente: str,
                 region: dict, salida: queue.Queue, store: EventStore,
                 solo_vista: bool = False, ritmo_real: bool = True):
        super().__init__(daemon=True, name=f"vigilante-{estacion}")
        self.estacion = estacion
        self.ajustes = ESTACIONES[estacion]
        self.fuente = fuente
        self.nombre_fuente = nombre_fuente
        self.salida = salida
        self.store = store
        self.solo_vista = solo_vista
        # Un archivo se reproduce al ritmo de la camara para que el operador
        # vea lo que pasa. En analisis y verificacion se desactiva: la duracion
        # medida no depende del ritmo porque el reloj del motor es el tiempo
        # del video, no el de pared. Ese es justamente el punto.
        self.ritmo_real = ritmo_real
        self.detener = threading.Event()
        self._region = dict(region)
        self._region_lock = threading.Lock()
        self.fuente_activa = None
        # Base de tiempo real. Para un archivo, los sellos de reloj son
        # "cuando se corrio la prueba mas el desplazamiento dentro del video":
        # las DURACIONES son exactas, las horas absolutas son del ensayo. No se
        # deduce la hora del nombre del archivo porque no coincide con el reloj
        # en pantalla del video (nombre 03h15, OSD 04:17).
        self._base_reloj = datetime.now()

    def actualizar_region(self, region: dict) -> None:
        with self._region_lock:
            self._region = dict(region)

    def run(self) -> None:
        try:
            self._ciclo()
        except Exception as error:
            failures.record(self.estacion, "el ciclo de vigilancia fallo",
                            exc=error)
            self.salida.put({"error": str(error)})

    def _recorte(self, cuadro: np.ndarray) -> tuple[np.ndarray, tuple]:
        """Recorta la region vigente. El recorte lo hace el pipeline.

        La region se copia bajo candado porque la interfaz puede moverla con la
        medicion corriendo.
        """
        with self._region_lock:
            region = dict(self._region)
        return recortar(cuadro, region)

    def _ciclo(self) -> None:
        captura, self.fuente_activa = abrir_fuente(self.fuente)
        self.salida.put({"aviso": "Fuente abierta"})

        fps = captura.get(cv2.CAP_PROP_FPS) or 25.0
        es_archivo = not (isinstance(self.fuente_activa, str)
                          and self.fuente_activa.startswith("rtsp://"))
        salto = max(1, int(round(fps / HZ_ANALISIS)))

        maquina = MaquinaDeEstado(
            Histeresis.desde_estados_medidos(
                self.ajustes["inactivo_medido"], self.ajustes["activo_medido"]),
            nombre_activo=self.ajustes["nombre_activo"],
            nombre_inactivo=self.ajustes["nombre_inactivo"],
        )
        medir = self.ajustes["senal"]
        previo_registro = None
        ventana_hann = None
        movida_desde: float | None = None
        quieta_desde: float | None = None
        numero = 0
        ultimo_envio = 0.0
        inicio_pared = time.monotonic()

        while not self.detener.is_set():
            ok = captura.grab()
            if not ok:
                break
            if numero % salto == 0:
                ok, cuadro = captura.retrieve()
                if not ok:
                    break

                # El reloj del motor es el TIEMPO DEL VIDEO, no el de pared.
                # Asi la duracion medida es la real aunque el archivo se
                # reproduzca mas rapido o mas lento que en vivo.
                if es_archivo:
                    momento = numero / fps
                else:
                    momento = time.monotonic() - inicio_pared

                recorte, caja = self._recorte(cuadro)
                valor = float(medir(recorte))

                # Vigilancia de camara movida (la del mantenedor es PTZ). Si el
                # cuadro completo se desplazo, el recorte ya no apunta al vano y
                # cualquier lectura seria inventada: se entrega None y el motor
                # marca hueco en vez de afirmar un estado.
                gris = cv2.cvtColor(
                    cv2.resize(cuadro, (ANCHO_REGISTRO,
                                        int(cuadro.shape[0] * ANCHO_REGISTRO
                                            / cuadro.shape[1]))),
                    cv2.COLOR_BGR2GRAY).astype(np.float32)
                if ventana_hann is None:
                    ventana_hann = cv2.createHanningWindow(
                        (gris.shape[1], gris.shape[0]), cv2.CV_32F)
                desplazamiento = 0.0
                if previo_registro is not None:
                    (dx, dy), _ = cv2.phaseCorrelate(
                        previo_registro, gris, ventana_hann)
                    desplazamiento = float(np.hypot(dx, dy))
                previo_registro = gris

                # Se enclava al detectar movimiento y se suelta solo cuando el
                # cuadro lleva unos segundos quieto. Sin el enclavamiento, un
                # giro de la PTZ marcaria hueco unicamente en las dos muestras
                # del giro y el resto del intervalo se leeria como bueno con el
                # recorte apuntando a otro lado.
                if desplazamiento > DESPLAZAMIENTO_MAXIMO:
                    if movida_desde is None:
                        movida_desde = momento
                        self.salida.put({
                            "aviso": "La cámara se movió: región por verificar"
                        })
                    quieta_desde = None
                elif movida_desde is not None:
                    if quieta_desde is None:
                        quieta_desde = momento
                    elif momento - quieta_desde >= SEGUNDOS_DE_ASENTAMIENTO:
                        movida_desde = None
                        quieta_desde = None
                camara_movida = movida_desde is not None

                intervalo = maquina.actualizar(
                    None if camara_movida else valor, momento)
                if intervalo is not None:
                    self._publicar(intervalo)

                if momento - ultimo_envio >= 0.25:
                    ultimo_envio = momento
                    self.salida.put({
                        "vista": self._vista(cuadro, caja, maquina, valor,
                                             camara_movida),
                        "estado": maquina.estado,
                        "activo": maquina.activo,
                        "duracion": maquina.duracion_actual(momento),
                        "valor": valor,
                        "desplazamiento": desplazamiento,
                        "camara_movida": camara_movida,
                        "momento": momento,
                    })

                if es_archivo and self.ritmo_real:
                    # Un archivo entrega cuadros tan rapido como se lean. Sin
                    # marcar el paso, 10 minutos de video se consumen en
                    # segundos y no se ve nada.
                    objetivo = inicio_pared + momento
                    espera = objetivo - time.monotonic()
                    if espera > 0:
                        self.detener.wait(espera)
            numero += 1

        # El intervalo en curso no se pierde al detener: sin esto, la ultima
        # apertura del turno nunca quedaria registrada.
        momento_final = (numero / fps) if es_archivo else (
            time.monotonic() - inicio_pared)
        pendiente = maquina.cerrar(momento_final)
        if pendiente is not None:
            self._publicar(pendiente)
        captura.release()
        self.salida.put({"fin": True})

    def _sello(self, momento: float) -> str:
        """Momento del video convertido a un sello de reloj guardable."""
        return (self._base_reloj + timedelta(seconds=momento)).strftime(
            "%Y-%m-%d %H:%M:%S")

    def _publicar(self, intervalo) -> None:
        registro = {
            "estacion": self.estacion,
            "estado": intervalo.estado,
            "inicio": self._sello(intervalo.inicio),
            "fin": self._sello(intervalo.fin),
            "duracion_s": intervalo.duracion,
            "source": self.nombre_fuente,
            "origen": self.ajustes["origen"],
            "parcial": intervalo.parcial,
            "con_hueco": intervalo.con_hueco,
            "valor_medio": intervalo.valor_medio,
            "evidence_path": "",
        }
        if not self.solo_vista:
            try:
                registro["id"] = self.store.insert_estado(registro)
            except Exception as error:
                failures.record(self.estacion,
                                "no se pudo registrar el estado", exc=error)
        self.salida.put({"intervalo": registro})

    def _vista(self, cuadro, caja, maquina, valor, camara_movida):
        """Cuadro COMPLETO con la region marcada.

        Completo y no recortado a proposito: para calibrar la region hay que ver
        donde cae dentro de la escena. Fue justo lo que obligo a usar el modulo
        de personas para revisar esta camara.
        """
        vista = cuadro.copy()
        x0, y0, x1, y1 = caja
        if camara_movida:
            color, texto = (0, 128, 255), "CAMARA MOVIDA"
        elif maquina.activo:
            color = (180, 0, 255)
            texto = f"{maquina.estado.upper()} · {valor:+.1f}"
        else:
            color = (140, 190, 140)
            texto = f"{maquina.estado.upper()} · {valor:+.1f}"
        cv2.rectangle(vista, (x0, y0), (x1, y1), color, 3)
        cv2.putText(vista, texto, (x0, max(28, y0 - 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2, cv2.LINE_AA)
        return vista


class AppEstados(ctk.CTk):
    """Ventana del modulo. Una estacion por proceso."""

    def __init__(self, estacion: str):
        super().__init__()
        self.estacion = estacion
        self.ajustes = ESTACIONES[estacion]
        self.title(f"{APP_NAME} · {self.ajustes['titulo']}")
        self.geometry("1360x860")
        self.minsize(1100, 720)
        self.configure(fg_color="#0d151c")

        self.config_data = load_config()
        self.evidencias = EvidenceManager(paths.EVIDENCE_DIR)
        self.store = EventStore(paths.DB_PATH, self.evidencias)
        self.cola: queue.Queue = queue.Queue()
        self.vigilante: VigilanteDeEstado | None = None
        self._imagen = None
        self._duracion_visible = 0.0

        self._construir()
        self._refrescar_resumen()
        self.after(60, self._sondear)
        self.protocol("WM_DELETE_WINDOW", self._cerrar)

    # ---- interfaz ---------------------------------------------------------
    def _construir(self) -> None:
        encabezado = ctk.CTkFrame(self, fg_color="#111c25", height=64)
        encabezado.pack(fill="x", padx=12, pady=(12, 0))
        ctk.CTkLabel(
            encabezado, text=f"{APP_NAME.upper()} · {self.ajustes['titulo'].upper()}",
            font=("Segoe UI", 17, "bold")).pack(side="left", padx=16, pady=14)
        self.reloj = ctk.CTkLabel(encabezado, text="",
                                  font=("Segoe UI", 13, "bold"))
        self.reloj.pack(side="right", padx=16)

        tarjetas = ctk.CTkFrame(self, fg_color="transparent")
        tarjetas.pack(fill="x", padx=12, pady=10)
        for i in range(4):
            tarjetas.grid_columnconfigure(i, weight=1)
        # MetricCard exige los 4 argumentos: master, titulo, valor, color.
        self.t_estado = MetricCard(tarjetas, "Estado", "—", "#8292a2")
        self.t_cronometro = MetricCard(tarjetas, "Tiempo en ese estado", "0s",
                                       "#43a9ff")
        self.t_aperturas = MetricCard(
            tarjetas, f"Veces {self.ajustes['nombre_activo']} (hoy)", "0",
            "#bd8cff")
        self.t_acumulado = MetricCard(tarjetas, "Tiempo acumulado (hoy)", "0s",
                                      "#f4b942")
        for i, tarjeta in enumerate((self.t_estado, self.t_cronometro,
                                     self.t_aperturas, self.t_acumulado)):
            tarjeta.grid(row=0, column=i, padx=(0 if i == 0 else 8, 0),
                         sticky="ew")

        cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        cuerpo.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        panel = ctk.CTkScrollableFrame(cuerpo, width=320,
                                       label_text="CONFIGURACIÓN")
        panel.pack(side="left", fill="y", padx=(0, 12))
        self._construir_panel(panel)

        derecha = ctk.CTkFrame(cuerpo, fg_color="transparent")
        derecha.pack(side="left", fill="both", expand=True)

        # Botones y registro ANTES del lienzo: en pack, el ultimo empaquetado
        # es el primero en perder espacio, y si el lienzo tuviera prioridad la
        # imagen empujaria los botones fuera de la ventana (bug de la 0.8.1).
        acciones = ctk.CTkFrame(derecha, fg_color="transparent")
        acciones.pack(side="bottom", fill="x", pady=(10, 0))
        self.b_iniciar = ctk.CTkButton(
            acciones, text="▶  INICIAR MEDICIÓN", height=42,
            fg_color="#1f9d55", hover_color="#26b463", command=self.iniciar)
        self.b_iniciar.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self.b_probar = ctk.CTkButton(
            acciones, text="◉  SOLO VISTA", height=42,
            fg_color="#2b3d4f", hover_color="#3a5164", command=self.probar)
        self.b_probar.pack(side="left", expand=True, fill="x", padx=6)
        self.b_detener = ctk.CTkButton(
            acciones, text="■  DETENER", height=42, state="disabled",
            fg_color="#b3403c", hover_color="#c94b46", command=self.detener)
        self.b_detener.pack(side="left", expand=True, fill="x", padx=(6, 0))

        registro = ctk.CTkFrame(derecha, fg_color="#111c25", height=190)
        registro.pack(side="bottom", fill="x", pady=(10, 0))
        ctk.CTkLabel(
            registro,
            text=f"REGISTRO DE {self.ajustes['nombre_activo'].upper()}S",
            font=("Segoe UI", 11, "bold"), text_color="#8292a2",
        ).pack(anchor="w", padx=12, pady=(8, 0))
        self.tabla = tk.Text(registro, height=8, bg="#0d151c", fg="#d7e2ea",
                             font=("Consolas", 10), relief="flat", padx=10,
                             pady=6, state="disabled")
        self.tabla.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        panel_video = ctk.CTkFrame(derecha, fg_color="#0a1219")
        panel_video.pack(side="top", fill="both", expand=True)
        self.lienzo = ctk.CTkLabel(
            panel_video, text="Pulsa INICIAR MEDICIÓN o SOLO VISTA",
            font=("Segoe UI", 13), text_color="#66788a")
        self.lienzo.pack(fill="both", expand=True)

        self.estado_texto = ctk.CTkLabel(derecha, text="LISTO",
                                         font=("Segoe UI", 12, "bold"),
                                         text_color="#43a9ff")
        self.estado_texto.pack(side="top", anchor="w", pady=(6, 0))

        self._tic_reloj()

    def _construir_panel(self, panel) -> None:
        if not self.ajustes["calibrada"]:
            ctk.CTkLabel(
                panel, text=f"La estación «{self.ajustes['titulo']}» aún no "
                "tiene señal calibrada contra video real. No se puede medir "
                "sin umbral medido.",
                font=("Segoe UI", 11), text_color="#e0a45e", wraplength=280,
                justify="left").pack(anchor="w", padx=16, pady=(8, 4))

        sec_fuente = SeccionDesplegable(panel, "FUENTE DE VIDEO", abierta=True)
        sec_fuente.pack(fill="x", pady=(6, 0))
        self.fuente = PanelDeFuente(sec_fuente.contenido, self.config_data)

        sec_region = SeccionDesplegable(panel, "REGIÓN MEDIDA")
        sec_region.pack(fill="x", pady=(6, 0))
        caja = sec_region.contenido
        ctk.CTkLabel(
            caja, text="Porcentaje del cuadro. Se ajusta con la medición "
            "activa; el recuadro se ve sobre la imagen.",
            font=("Segoe UI", 10), text_color="#8ba0ad", wraplength=260,
            justify="left").pack(anchor="w", pady=(2, 6))
        guardada = self.config_data.get(
            f"estados_{self.estacion}_region", self.ajustes["region"])
        self.sliders = {}
        self.valores = {}
        etiquetas = (("x", "Izquierda"), ("y", "Arriba"),
                     ("w", "Ancho"), ("h", "Alto"))
        for clave, texto in etiquetas:
            fila = ctk.CTkFrame(caja, fg_color="transparent")
            fila.pack(fill="x", pady=(4, 0))
            ctk.CTkLabel(fila, text=texto,
                         font=("Segoe UI", 11)).pack(side="left")
            valor_txt = ctk.CTkLabel(fila, text="",
                                     font=("Segoe UI", 11, "bold"))
            valor_txt.pack(side="right")
            minimo = 0.02 if clave in ("w", "h") else 0.0
            deslizador = ctk.CTkSlider(
                caja, from_=minimo, to=1.0, number_of_steps=196,
                command=lambda v, c=clave: self._region_cambio(c, v))
            deslizador.set(float(guardada.get(clave,
                                              self.ajustes["region"][clave])))
            deslizador.pack(fill="x")
            self.sliders[clave] = deslizador
            self.valores[clave] = valor_txt
            self._region_cambio(clave, deslizador.get())

        sec_umbral = SeccionDesplegable(panel, "UMBRALES")
        sec_umbral.pack(fill="x", pady=(6, 0))
        caja_u = sec_umbral.contenido
        if self.ajustes["calibrada"]:
            h = Histeresis.desde_estados_medidos(
                self.ajustes["inactivo_medido"], self.ajustes["activo_medido"])
            ctk.CTkLabel(
                caja_u,
                text=f"Medido en video real:\n"
                     f"  {self.ajustes['nombre_inactivo']}  "
                     f"{self.ajustes['inactivo_medido']:+.2f}\n"
                     f"  {self.ajustes['nombre_activo']}  "
                     f"{self.ajustes['activo_medido']:+.2f}\n\n"
                     f"Histéresis derivada:\n"
                     f"  entra > {h.entra:+.2f}\n"
                     f"  sale  < {h.sale:+.2f}",
                font=("Consolas", 10), text_color="#8ba0ad",
                justify="left").pack(anchor="w", pady=(4, 6))

        sec_alarma = SeccionDesplegable(panel, "ALARMA POR DURACIÓN")
        sec_alarma.pack(fill="x", pady=(6, 0))
        caja_a = sec_alarma.contenido
        ctk.CTkLabel(
            caja_a, text="Segundos a partir de los cuales avisar. En 0 no "
            "avisa. PENDIENTE de acordar con operación: no se inventa un "
            "umbral aquí.",
            font=("Segoe UI", 10), text_color="#e0a45e", wraplength=260,
            justify="left").pack(anchor="w", pady=(2, 6))
        self.alarma = ctk.CTkEntry(caja_a, placeholder_text="0")
        self.alarma.insert(0, str(self.config_data.get(
            f"estados_{self.estacion}_alarma_s", 0)))
        self.alarma.pack(fill="x")

    def _region_cambio(self, clave: str, valor) -> None:
        self.valores[clave].configure(text=f"{float(valor) * 100:.0f} %")
        if self.vigilante and self.vigilante.is_alive():
            self.vigilante.actualizar_region(self._region())

    def _region(self) -> dict:
        return {k: float(s.get()) for k, s in self.sliders.items()}

    def _tic_reloj(self) -> None:
        self.reloj.configure(text=datetime.now().strftime("%d/%m/%Y  %H:%M:%S"))
        self.after(1000, self._tic_reloj)

    # ---- ciclo ------------------------------------------------------------
    def probar(self) -> None:
        self._arrancar(solo_vista=True)

    def iniciar(self) -> None:
        self._arrancar(solo_vista=False)

    def _arrancar(self, solo_vista: bool) -> None:
        if not self.ajustes["calibrada"]:
            messagebox.showwarning(
                APP_NAME,
                f"La estación «{self.ajustes['titulo']}» no tiene señal "
                "calibrada contra video real. Medir con un umbral inventado "
                "daría datos que parecen buenos y no lo son.")
            return
        try:
            origen, nombre = self.fuente.construir()
        except ValueError as error:
            messagebox.showerror(APP_NAME, str(error))
            return

        self.fuente.guardar_en(self.config_data)
        self.config_data[f"estados_{self.estacion}_region"] = self._region()
        try:
            self.config_data[f"estados_{self.estacion}_alarma_s"] = max(
                0.0, float(self.alarma.get().strip() or 0))
        except ValueError:
            self.config_data[f"estados_{self.estacion}_alarma_s"] = 0.0
        save_config(self.config_data)

        self._escribir(
            f"── inicio de medición · {datetime.now():%d/%m/%Y %H:%M:%S} · "
            f"{ocultar_credenciales(nombre)}"
            + ("  (SOLO VISTA: no se guarda)" if solo_vista else ""))

        self.vigilante = VigilanteDeEstado(
            self.estacion, origen, nombre, self._region(), self.cola,
            self.store, solo_vista=solo_vista)
        self.vigilante.start()
        self.b_iniciar.configure(state="disabled")
        self.b_probar.configure(state="disabled")
        self.b_detener.configure(state="normal")
        self.estado_texto.configure(text="MIDIENDO", text_color="#1f9d55")

    def detener(self) -> None:
        if self.vigilante:
            self.vigilante.detener.set()
            # Esperar a que cierre la fuente: si la ventana se destruye antes,
            # el hilo sigue escribiendo en una cola que ya no existe.
            self.vigilante.join(timeout=4)
            self.vigilante = None
        self.b_iniciar.configure(state="normal")
        self.b_probar.configure(state="normal")
        self.b_detener.configure(state="disabled")
        self.estado_texto.configure(text="DETENIDO", text_color="#d95c5c")
        self._refrescar_resumen()

    def _sondear(self) -> None:
        try:
            while True:
                dato = self.cola.get_nowait()
                if dato.get("error"):
                    messagebox.showerror(APP_NAME, dato["error"])
                    self.detener()
                    continue
                if dato.get("fin"):
                    self._escribir("── fin de la fuente")
                    self.detener()
                    continue
                if dato.get("aviso"):
                    self.estado_texto.configure(text=dato["aviso"].upper(),
                                                text_color="#d9a441")
                    continue
                if dato.get("intervalo"):
                    self._anotar_intervalo(dato["intervalo"])
                    continue
                self._pintar(dato)
        except queue.Empty:
            pass
        self.after(60, self._sondear)

    def _anotar_intervalo(self, registro: dict) -> None:
        avisos = []
        if registro["parcial"]:
            avisos.append("parcial")
        if registro["con_hueco"]:
            avisos.append("con hueco")
        marca = f"  [{', '.join(avisos)}]" if avisos else ""
        self._escribir(
            f"{registro['estado'].upper():<9} "
            f"inicio {registro['inicio'][11:]}  "
            f"cierre {registro['fin'][11:]}  "
            f"duración {formato_duracion(registro['duracion_s']):>9}{marca}")
        self._refrescar_resumen()

        alarma = float(self.config_data.get(
            f"estados_{self.estacion}_alarma_s", 0) or 0)
        activo = registro["estado"] == self.ajustes["nombre_activo"]
        if activo and alarma > 0 and registro["duracion_s"] >= alarma:
            self._escribir(
                f"   ⚠ excedió el límite acordado de "
                f"{formato_duracion(alarma)}")

    def _refrescar_resumen(self) -> None:
        """Totales del dia leidos de la base, no acumulados en memoria.

        Si se contaran en memoria, reiniciar el modulo pondria el turno en cero
        y el dato del dia se perderia sin que nadie lo note.
        """
        desde = datetime.now().strftime("%Y-%m-%d 00:00:00")
        resumen = self.store.resumen_de_estados(
            self.estacion, self.ajustes["nombre_activo"], desde=desde)
        self.t_aperturas.set(str(resumen["veces"]), "#bd8cff")
        self.t_acumulado.set(
            formato_duracion(resumen["duracion_total"]), "#f4b942")

    def _escribir(self, linea: str) -> None:
        self.tabla.configure(state="normal")
        self.tabla.insert("end", linea + "\n")
        self.tabla.see("end")
        self.tabla.configure(state="disabled")

    def _pintar(self, dato: dict) -> None:
        activo = dato["activo"]
        if dato.get("camara_movida"):
            self.t_estado.set("CÁMARA MOVIDA", "#ff8c42")
        else:
            self.t_estado.set(dato["estado"].upper(),
                              "#bd8cff" if activo else "#8fbf8f")
        self.t_cronometro.set(formato_duracion(dato["duracion"]),
                              "#bd8cff" if activo else "#43a9ff")
        self.estado_texto.configure(
            text=f"señal {dato['valor']:+.1f}   ·   "
                 f"desplazamiento {dato['desplazamiento']:.2f} px   ·   "
                 f"video {formato_duracion(dato['momento'])}",
            text_color="#8ba0ad")

        vista = dato["vista"]
        ancho = max(self.lienzo.winfo_width(), 320)
        alto = max(self.lienzo.winfo_height(), 240)
        escala = min(alto / vista.shape[0], ancho / vista.shape[1])
        vista = cv2.resize(vista, (max(int(vista.shape[1] * escala), 1),
                                   max(int(vista.shape[0] * escala), 1)))
        imagen = Image.fromarray(cv2.cvtColor(vista, cv2.COLOR_BGR2RGB))
        # CTkImage y no ImageTk: en pantallas con escalado una PhotoImage se ve
        # borrosa porque no se reescala.
        self._imagen = ctk.CTkImage(light_image=imagen, dark_image=imagen,
                                    size=(imagen.width, imagen.height))
        self.lienzo.configure(image=self._imagen, text="")

    def _cerrar(self) -> None:
        self.detener()
        while True:
            try:
                self.cola.get_nowait()
            except queue.Empty:
                break
        self.destroy()


if __name__ == "__main__":
    MODULO = os.environ.get("ARZYZ_MODULE_ID", "mantenedor")
    if MODULO not in ESTACIONES:
        print(f"Estación desconocida: {MODULO}. "
              f"Opciones: {', '.join(ESTACIONES)}", file=sys.stderr)
        raise SystemExit(2)
    failures.configure(MODULO)
    from core.heartbeat import HeartbeatWriter

    # Late con el MISMO nombre con el que lo registra el centro de control; si
    # difieren, el supervisor no lo escucha nunca y lo reinicia en bucle.
    HeartbeatWriter(MODULO).start()
    AppEstados(MODULO).mainloop()
