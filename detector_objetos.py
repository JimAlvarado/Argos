"""Modulo de deteccion y conteo de objetos en banda transportadora.

Cuenta piezas que cruzan una linea. Dos fuentes posibles, UNA a la vez (regla
de operacion, 14-ago-2026): la vision clasica de `core.pipeline.classic` (la
misma que alimenta al capturador de dataset) o un modelo entrenado elegido en
configuracion. Si hay modelo, cuenta el modelo; si no, la clasica. Si el
modelo no puede cargar, la clasica retoma el conteo: la banda nunca se queda
sin contar.

Se lanza desde el centro de control o por su cuenta:

    python detector_objetos.py
"""
from __future__ import annotations

import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import cv2
import customtkinter as ctk
import numpy as np
from PIL import Image

from core import failures, paths
from core.config import load_config, save_config
from core.evidence import EvidenceManager
from core.paths import APP_NAME, MODEL_DIR
from core.pipeline.classic import (
    Ajustes,
    ContadorDeObjetos,
    ESCALA_PROCESO,
    ModeloDeFondo,
    detectar,
)
from core.storage import EventStore
from core.utils import format_timestamp_12h
# Se reutiliza la validacion de archivos .pt del detector de personas: si cada
# modulo validara por su cuenta, aceptarian archivos distintos.
from ui.models import ModelMixin
from ui.source import PanelDeFuente
from ui.widgets import MetricCard, SeccionDesplegable

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# Region de interes por defecto: la banda de la lingotera sobre el cuadro 4K.
# La camara puede reposicionarse desde su configuracion; por eso es editable.
REGION_POR_DEFECTO = {"x": 1600, "y": 1140, "w": 560, "h": 1020}

# Marcador del selector cuando la carpeta modelos\ no tiene ningun .pt.
SIN_MODELOS = "(no hay modelos .pt)"

# Umbral por defecto, ajustable con el deslizador "Confianza" del submenu del
# modelo. 30% es el punto util medido en CPU: 35 de 53 muestras al 30% contra
# 23 al 40% (mediciones en docs/historico/ANALISIS_VELOCIDAD.md).
CONFIANZA_MODELO = 0.30

# Azul de los marcadores de deteccion (BGR), acordado con operacion sobre la
# captura de referencia del detector de personas.
AZUL_DETECCION = (255, 96, 32)


def detecciones_de_resultado(resultados) -> list[tuple[tuple, float | None]]:
    """Convierte la salida de Ultralytics en pares (caja, confianza).

    La caja es (x, y, w, h) entera; la confianza puede faltar (None) si el
    resultado no la trae. No requiere torch: solo itera lo que recibe.
    """
    detecciones = []
    for resultado in resultados or []:
        boxes = getattr(resultado, "boxes", None)
        if boxes is None:
            continue
        confianzas = getattr(boxes, "conf", None)
        for indice, fila in enumerate(getattr(boxes, "xyxy", [])):
            x1, y1, x2, y2 = (float(v) for v in fila)
            confianza = None
            if confianzas is not None:
                try:
                    confianza = float(confianzas[indice])
                except (IndexError, TypeError, ValueError):
                    confianza = None
            detecciones.append(
                ((int(x1), int(y1), int(x2 - x1), int(y2 - y1)), confianza))
    return detecciones


def cajas_de_resultado(resultados) -> list[tuple[int, int, int, int]]:
    """Solo las cajas (x, y, w, h) de la salida de Ultralytics."""
    return [caja for caja, _ in detecciones_de_resultado(resultados)]


def etiqueta_de_caja(confianza: float | None = None) -> str:
    """Texto de la caja: "lingote" y la confianza cuando el modelo la da."""
    if confianza is None:
        return "lingote"
    return f"lingote {confianza:.2f}"


def fusionar_cajas_solapadas(detecciones: list) -> list:
    """Une cajas del modelo que se solapan: son la MISMA pieza.

    Medido con lingotes_v2 (15-ago): el modelo pone a veces dos cajas sobre
    una pieza; sin fusion, la caja gemela crea una pista paralela que cruza
    la linea unos cuadros despues y duplica el conteo. Dos piezas reales
    nunca se solapan: van separadas mas de una pieza completa sobre la cadena.
    """
    resultado = []
    for caja, conf in detecciones:
        x1, y1, w, h = caja
        x2, y2 = x1 + w, y1 + h
        fusionada = False
        for i, ((rx1, ry1, rw, rh), rconf) in enumerate(resultado):
            rx2, ry2 = rx1 + rw, ry1 + rh
            ix = max(0, min(x2, rx2) - max(x1, rx1))
            iy = max(0, min(y2, ry2) - max(y1, ry1))
            inter = ix * iy
            menor = min(w * h, rw * rh)
            if menor > 0 and inter / menor > 0.4:
                nx1, ny1 = min(x1, rx1), min(y1, ry1)
                nx2, ny2 = max(x2, rx2), max(y2, ry2)
                resultado[i] = ((nx1, ny1, nx2 - nx1, ny2 - ny1),
                                max(conf or 0, rconf or 0))
                fusionada = True
                break
        if not fusionada:
            resultado.append((caja, conf))
    return resultado


class ContadorWorker(threading.Thread):
    """Lee la fuente, detecta, cuenta y publica cuadros para la interfaz."""

    def __init__(self, fuente, region, linea_relativa, guardar_evidencia,
                 salida: queue.Queue, store: EventStore, evidencias: EvidenceManager,
                 nombre_fuente: str, solo_vista: bool = False,
                 modelo_ruta: str | None = None,
                 confianza: float = CONFIANZA_MODELO):
        super().__init__(daemon=True, name="contador-objetos")
        self.fuente = fuente
        self.region = region
        self.linea_relativa = linea_relativa
        self.guardar_evidencia = guardar_evidencia
        self.salida = salida
        self.store = store
        self.evidencias = evidencias
        self.nombre_fuente = nombre_fuente
        # En modo vista solo se muestra la camara: no cuenta ni guarda nada.
        self.solo_vista = solo_vista
        # Modelo entrenado opcional. Regla de operacion (14-ago-2026): una
        # sola fuente de conteo a la vez; con modelo cuenta el modelo, sin
        # modelo cuenta la vision clasica. Nunca las dos juntas.
        self.modelo_ruta = modelo_ruta
        self.modelo_activo = ""
        # Umbral del modelo; la interfaz puede ajustarlo en vivo.
        self.confianza = float(confianza)
        # Equipo de inferencia. Arranca en CPU y se resuelve al cargar el
        # modelo, que es cuando torch ya esta importado. Se guarda para
        # pasarlo explicito en cada inferencia y poder reportarlo.
        self.dispositivo = "cpu"
        self.cuantizacion = None
        self.equipo = "CPU"
        self._region_nueva: dict | None = None
        self._region_lock = threading.Lock()
        self._confianzas: list = []
        # Ids contados pendientes de mostrarse en ULTIMOS CONTEOS.
        self._ids_contados: list = []
        self.detener = threading.Event()
        self.total = 0
        self.iniciado_en = time.time()

    def actualizar_region(self, region: dict) -> None:
        """Recibe una region nueva desde la interfaz, con el ciclo corriendo."""
        with self._region_lock:
            self._region_nueva = dict(region)

    def run(self) -> None:
        try:
            self._ciclo()
        except Exception as error:            # nunca debe morir en silencio
            failures.record("contador", "el ciclo de conteo fallo", exc=error)
            self.salida.put({"error": str(error)})

    def _abrir(self):
        """Abre la fuente probando cada ruta candidata.

        Algunas marcas publican el flujo en perfiles distintos (Provision usa
        profile1, profile2 o profile3), por eso la fuente puede ser una lista.
        Pasarla tal cual a OpenCV falla: hay que probarlas una por una.
        """
        candidatas = self.fuente if isinstance(self.fuente, list) else [self.fuente]
        for numero, candidata in enumerate(candidatas, start=1):
            if len(candidatas) > 1:
                self.salida.put({"aviso": f"Buscando flujo {numero}/{len(candidatas)}…"})
            if isinstance(candidata, str) and candidata.startswith("rtsp://"):
                captura = cv2.VideoCapture(
                    candidata, cv2.CAP_FFMPEG,
                    [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 4000,
                     cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000],
                )
            else:
                captura = cv2.VideoCapture(candidata)
            if captura.isOpened():
                self.fuente_activa = candidata
                return captura
            captura.release()
        visible = candidatas[0]
        if isinstance(visible, str) and "@" in visible:
            visible = "rtsp://***@" + visible.split("@", 1)[1]
        raise RuntimeError(
            f"No se pudo abrir la fuente. Revisa IP, usuario y contraseña.\n{visible}")

    def _ciclo(self) -> None:
        captura = self._abrir()

        # Un archivo entrega cuadros tan rapido como se lean, sin el ritmo que
        # impone una camara: sin marcar el paso, un video de 30 s se consumia
        # en menos de 2 y no daba tiempo ni de mirar la vista ni de detenerla.
        es_archivo = (isinstance(self.fuente_activa, str)
                      and not self.fuente_activa.startswith("rtsp://")
                      and Path(self.fuente_activa).is_file())
        paso_cuadro = 0.0
        if es_archivo:
            fps = captura.get(cv2.CAP_PROP_FPS)
            paso_cuadro = 1.0 / fps if fps and fps > 0 else 1.0 / 30.0

        r = dict(self.region)
        rec: dict | None = None       # region efectiva, recortada al cuadro
        ancho_p = alto_p = linea_y = 0
        fondo_modelo = None
        contador = None
        ajustes = Ajustes()
        ultimo_envio = 0.0

        modelo = self._cargar_modelo() if self.modelo_ruta else None
        # Si el modelo no cargo, la clasica toma el conteo: la banda nunca se
        # queda sin contar por un archivo danado.
        usa_modelo = modelo is not None
        self.modelo_activo = Path(self.modelo_ruta).name if usa_modelo else ""
        salto_modelo = 1
        numero_cuadro = 0
        ultimas_cajas: list = []

        while not self.detener.is_set():
            inicio_cuadro = time.monotonic()
            with self._region_lock:
                region_nueva, self._region_nueva = self._region_nueva, None
            if region_nueva and region_nueva != r:
                r = dict(region_nueva)
                rec = None            # se recalcula con el siguiente cuadro

            ok, cuadro = captura.read()
            if not ok:
                # Un archivo de video termina; una camara puede parpadear.
                if es_archivo:
                    break
                time.sleep(0.2)
                continue
            numero_cuadro += 1

            alto, ancho = cuadro.shape[:2]
            if rec is None:
                # La region puede ajustarse con el conteo activo: se conserva
                # el total, se reaprende el fondo (~1 s; las piezas en
                # transito durante el ajuste pueden no contarse) y se recorta
                # al cuadro para que un valor fuera de rango no tumbe el ciclo.
                rec = self._region_efectiva(r, ancho, alto)
                ancho_p = max(int(rec["w"] * ESCALA_PROCESO), 16)
                alto_p = max(int(rec["h"] * ESCALA_PROCESO), 16)
                linea_y = int(alto_p * self.linea_relativa)
                fondo_modelo = ModeloDeFondo(memoria=40, cada=5)
                total_previo = contador.total if contador else 0
                contador = ContadorDeObjetos(linea_y, alto_p, ancho_p)
                contador.total = total_previo
                ultimas_cajas = []

            recorte = cuadro[rec["y"]:rec["y"] + rec["h"],
                             rec["x"]:rec["x"] + rec["w"]]
            chico = cv2.resize(recorte, (ancho_p, alto_p))

            cajas: list = []
            nuevas = 0
            if usa_modelo:
                # Fuente unica: el modelo. El fondo clasico no participa.
                if numero_cuadro % salto_modelo == 0:
                    t_inferencia = time.monotonic()
                    ultimas_cajas = self._detectar_con_modelo(modelo, chico)
                    t_inferencia = time.monotonic() - t_inferencia
                    # En CPU la inferencia no debe frenar el ciclo: si tarda
                    # mas de 80 ms se espacia (hasta 1 de cada 5 cuadros). El
                    # antiparpadeo del contador tolera esos huecos. Con GPU la
                    # inferencia baja muy por debajo de ese umbral y el salto
                    # se queda solo en 1: se infiere cada cuadro sin tocar nada.
                    salto_modelo = max(1, min(5, int(t_inferencia / 0.08) + 1))
                    if not self.solo_vista:
                        nuevas = contador.actualizar(ultimas_cajas)
                cajas = ultimas_cajas
                listo = True
            else:
                # Fuente unica: la vision clasica.
                gris = cv2.cvtColor(chico, cv2.COLOR_BGR2GRAY)
                fondo = fondo_modelo.actualizar(gris)
                if fondo_modelo.listo:
                    cajas = detectar(gris, fondo, ajustes)
                    if not self.solo_vista:
                        nuevas = contador.actualizar(cajas)
                listo = fondo_modelo.listo

            if nuevas:
                self.total = contador.total
                self._ids_contados.extend(contador.contadas_recientes)
                for _ in range(nuevas):
                    self._registrar(recorte)

            ahora = time.time()
            if ahora - ultimo_envio > 0.06:      # ~15 cuadros por segundo
                ultimo_envio = ahora
                self.salida.put({
                    "vista": self._dibujar(chico, cajas, linea_y, contador,
                                           usa_modelo),
                    "total": contador.total,
                    "metodo": "modelo" if usa_modelo else "clasica",
                    "en_banda": len(cajas),
                    "listo": listo,
                    "ids_contados": self._ids_contados,
                    "transcurrido": ahora - self.iniciado_en,
                })
                self._ids_contados = []

            if paso_cuadro:
                restante = paso_cuadro - (time.monotonic() - inicio_cuadro)
                if restante > 0:
                    # wait() y no sleep(): despierta al instante si piden parar.
                    self.detener.wait(restante)
        captura.release()
        self.salida.put({"fin": True})

    def _cargar_modelo(self):
        """Carga el modelo en este mismo hilo; si falla, se sigue sin el.

        Un modelo danado o ausente jamas debe impedir el conteo: la vision
        clasica es la fuente oficial y el modelo solo la verifica.
        """
        try:
            self.salida.put({"aviso": "Cargando modelo…"})
            from core import runtime

            runtime.load_inference_runtime()
            modelo = runtime.YOLO(self.modelo_ruta)
            # El equipo se resuelve una sola vez, con torch ya cargado, y no
            # en cada cuadro: consultarlo por inferencia solo agrega trabajo.
            runtime.preparar_equipo()
            self.cuantizacion = runtime.cuantizacion()
            self.dispositivo = runtime.dispositivo_inferencia()
            self.equipo = runtime.nombre_equipo()
            self.salida.put({"equipo": self.equipo})
            return modelo
        except Exception as error:
            failures.record(
                "modelo-objetos",
                "no se pudo cargar el modelo; la visión clásica retoma "
                "el conteo", exc=error)
            self.salida.put({"aviso": "Modelo no disponible: visión clásica"})
            return None

    def _detectar_con_modelo(self, modelo, chico) -> list:
        """Cajas (x, y, w, h) del modelo sobre el recorte a escala de proceso.

        Las confianzas quedan en `self._confianzas`, alineadas con las cajas,
        solo para rotular la vista; el conteo usa unicamente las cajas.
        """
        try:
            # `device` y `quantize` van explicitos: Ultralytics tambien los
            # autodetecta, pero entonces el equipo usado queda invisible y no
            # hay forma de comprobar que la GPU esta contando de verdad.
            resultados = modelo.predict(
                chico, conf=self.confianza, verbose=False,
                device=self.dispositivo, quantize=self.cuantizacion)
        except Exception as error:
            failures.record("modelo-objetos", "falló la inferencia del modelo",
                            exc=error)
            self._confianzas = []
            return []
        detecciones = fusionar_cajas_solapadas(
            detecciones_de_resultado(resultados))
        self._confianzas = [confianza for _, confianza in detecciones]
        return [caja for caja, _ in detecciones]

    @staticmethod
    def _region_efectiva(r: dict, ancho: int, alto: int) -> dict:
        """Recorta la region al cuadro real: nunca debe tumbar el ciclo."""
        x = max(0, min(int(r["x"]), ancho - 32))
        y = max(0, min(int(r["y"]), alto - 32))
        w = max(32, min(int(r["w"]), ancho - x))
        h = max(32, min(int(r["h"]), alto - y))
        return {"x": x, "y": y, "w": w, "h": h}

    @staticmethod
    def _rotulo(vista, texto, x, y, color_fondo, color_texto):
        """Texto legible sobre una banda de color, al estilo de los overlays
        del detector de personas (la referencia acordada con operacion)."""
        (ancho_t, alto_t), _ = cv2.getTextSize(
            texto, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
        y = max(y, alto_t + 4)
        cv2.rectangle(vista, (x, y - alto_t - 4), (x + ancho_t + 8, y + 3),
                      color_fondo, -1)
        cv2.putText(vista, texto, (x + 4, y - 1), cv2.FONT_HERSHEY_SIMPLEX,
                    0.42, color_texto, 1, cv2.LINE_AA)

    @staticmethod
    def _linea_de_conteo(vista, linea_y):
        """Linea fina con remates, sin texto: el total vive en las tarjetas."""
        ancho = vista.shape[1]
        amarillo = (0, 215, 255)
        cv2.line(vista, (0, linea_y), (ancho, linea_y), amarillo, 1,
                 cv2.LINE_AA)
        # Remates: punto lleno a la izquierda, aro a la derecha.
        cv2.circle(vista, (5, linea_y), 4, amarillo, -1, cv2.LINE_AA)
        cv2.circle(vista, (ancho - 6, linea_y), 4, amarillo, 1, cv2.LINE_AA)

    @staticmethod
    def _esquinas(vista, x, y, w, h, color):
        """Marca solo las cuatro esquinas de la caja (pedido de operacion)."""
        lado = max(6, min(w, h) // 4)
        for px, py, dx, dy in ((x, y, 1, 1), (x + w, y, -1, 1),
                               (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
            cv2.line(vista, (px, py), (px + dx * lado, py), color, 2,
                     cv2.LINE_AA)
            cv2.line(vista, (px, py), (px, py + dy * lado), color, 2,
                     cv2.LINE_AA)

    def _dibujar(self, chico, cajas, linea_y, contador, con_modelo=False):
        vista = chico.copy()
        for indice, (x, y, w, h) in enumerate(cajas):
            self._esquinas(vista, x, y, w, h, AZUL_DETECCION)
            confianza = None
            if con_modelo and indice < len(self._confianzas):
                confianza = self._confianzas[indice]
            self._rotulo(vista, etiqueta_de_caja(confianza), x, y - 4,
                         AZUL_DETECCION, (245, 245, 245))
        self._linea_de_conteo(vista, linea_y)
        # El identificador es interno: en pantalla solo el punto de rastreo
        # (ambar = contada, gris = en transito); el id aparece en el registro
        # de ultimos conteos.
        for pista in contador.pistas:
            tono = (255, 190, 60) if pista.contado else (200, 200, 200)
            cv2.circle(vista, (int(pista.x), int(pista.y)), 3, tono, -1,
                       cv2.LINE_AA)
        return vista

    def _registrar(self, recorte) -> None:
        momento = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ruta = ""
        if self.guardar_evidencia:
            try:
                # Se guarda a media escala: a 950 piezas/hora, una imagen de
                # 658 KB llena 4.8 GB por turno y satura el disco. A la mitad
                # sigue siendo legible para verificar y pesa cuatro veces menos.
                alto, ancho = recorte.shape[:2]
                reducido = cv2.resize(recorte, (ancho // 2, alto // 2))
                ruta = self.evidencias.save_image(
                    reducido, momento, self.nombre_fuente, "objetos",
                    {"lingote": 1}, 1.0)
            except Exception as error:
                # Perder una imagen no debe detener el conteo, pero si quedar
                # registrado para poder revisarlo.
                failures.record("evidencia", "no se pudo guardar la captura",
                                exc=error)
        try:
            self.store.insert({
                "detected_at": momento,
                "source": self.nombre_fuente,
                "total": 1,
                "classes": {"lingote": 1},
                "max_confidence": 1.0,
                "evidence_path": ruta,
                # Queda registrado QUE fuente conto: permite auditar cada
                # conteo cuando se alterne entre clasica y modelo.
                "model_name": self.modelo_activo or "vision-clasica",
                "track_ids": [],
            })
        except Exception as error:
            failures.record("registro", "no se pudo guardar el conteo", exc=error)


class AppObjetos(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} — Detección y conteo de objetos")
        self.geometry("1240x820")
        self.minsize(1080, 700)

        self.config_data = load_config()
        self.evidencias = EvidenceManager(paths.EVIDENCE_DIR)
        self.store = EventStore(paths.DB_PATH, self.evidencias)
        self.cola: queue.Queue = queue.Queue(maxsize=4)
        self.worker: ContadorWorker | None = None
        self._imagen = None

        self._construir()
        self.after(60, self._sondear)
        self.protocol("WM_DELETE_WINDOW", self._cerrar)

    # ---- interfaz ---------------------------------------------------------
    def _construir(self) -> None:
        cabecera = ctk.CTkFrame(self, corner_radius=0, height=64)
        cabecera.pack(fill="x")
        ctk.CTkLabel(cabecera, text="DETECCIÓN Y CONTEO DE OBJETOS",
                     font=ctk.CTkFont(size=17, weight="bold")).pack(side="left", padx=18, pady=14)
        self.estado = ctk.CTkLabel(cabecera, text="DETENIDO",
                                   font=ctk.CTkFont(size=13, weight="bold"),
                                   text_color="#d95c5c")
        self.estado.pack(side="right", padx=18)

        metricas = ctk.CTkFrame(self, fg_color="transparent")
        metricas.pack(fill="x", padx=16, pady=(12, 6))
        self.m_total = MetricCard(metricas, "PIEZAS CONTADAS", "0", "#4aa96c")
        self.m_ritmo = MetricCard(metricas, "RITMO POR HORA", "—", "#4ea1d3")
        self.m_banda = MetricCard(metricas, "EN LA BANDA", "0", "#d9a441")
        # Dice cual fuente esta contando: regla de operacion, una sola a la
        # vez (el modelo elegido O la vision clasica, nunca ambas).
        self.m_fuente = MetricCard(metricas, "FUENTE DE CONTEO", "CLÁSICA",
                                   "#bd8cff")
        for m in (self.m_total, self.m_ritmo, self.m_banda, self.m_fuente):
            m.pack(side="left", expand=True, fill="x", padx=6)

        cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        cuerpo.pack(fill="both", expand=True, padx=16, pady=8)

        panel = ctk.CTkScrollableFrame(cuerpo, width=290, label_text="CONFIGURACIÓN")
        panel.pack(side="left", fill="y", padx=(0, 12))

        # La decision mas importante del modulo va arriba, bajo el titulo del
        # panel: con que fuente se cuenta. Regla de operacion (14-ago-2026):
        # una sola a la vez, el modelo elegido O la vision clasica.
        modo_guardado = self.config_data.get("objetos_fuente") or (
            "modelo" if self.config_data.get("objetos_modelo") else "clasica")
        self.fuente_conteo = ctk.CTkSegmentedButton(
            panel, values=["CLÁSICA", "MODELO"],
            command=self._fuente_conteo_cambio)
        self.fuente_conteo.set(
            "MODELO" if modo_guardado == "modelo" else "CLÁSICA")
        self.fuente_conteo.pack(fill="x", pady=(4, 2))
        ctk.CTkLabel(
            panel, text="Una sola fuente cuenta a la vez. Aplica al iniciar.",
            font=ctk.CTkFont(size=10), text_color="#8ba0ad").pack(
            anchor="w", pady=(0, 4))

        # Mismos campos que el detector de personas: el componente es
        # compartido, asi una camara configurada en un modulo funciona en el otro.
        sec_video = SeccionDesplegable(panel, "FUENTE DE VIDEO", abierta=True)
        sec_video.pack(fill="x", pady=(6, 0))
        self.fuente = PanelDeFuente(sec_video.contenido, self.config_data)

        self.sec_region = SeccionDesplegable(panel, "REGIÓN DE INTERÉS")
        self.sec_region.pack(fill="x", pady=(6, 0))
        caja_region = self.sec_region.contenido
        # Deslizadores en vez de campos numericos (pedido de operacion): no
        # hay valores invalidos posibles y el ajuste se ve mientras se mueve.
        # El paso es de 4 px: la correccion real medida fue de 56 px, asi que
        # 4 px sobran para recuadrar la banda.
        self.region_sliders = {}
        self.region_valores = {}
        guardada = self.config_data.get("objetos_region", REGION_POR_DEFECTO)
        for clave, texto, tope in (("x", "Izquierda (x)", 3840),
                                   ("y", "Arriba (y)", 2160),
                                   ("w", "Ancho", 3840),
                                   ("h", "Alto", 2160)):
            minimo = 0 if clave in ("x", "y") else 32
            fila = ctk.CTkFrame(caja_region, fg_color="transparent")
            fila.pack(fill="x", pady=(4, 0))
            ctk.CTkLabel(fila, text=texto,
                         font=ctk.CTkFont(size=11)).pack(side="left")
            valor_txt = ctk.CTkLabel(fila, text="",
                                     font=ctk.CTkFont(size=11, weight="bold"))
            valor_txt.pack(side="right")
            deslizador = ctk.CTkSlider(
                caja_region, from_=minimo, to=tope,
                number_of_steps=(tope - minimo) // 4,
                command=lambda v, c=clave: self._region_cambio(c, v))
            valor = int(guardada.get(clave, REGION_POR_DEFECTO[clave]))
            deslizador.set(min(max(valor, minimo), tope))
            deslizador.pack(fill="x")
            self.region_sliders[clave] = deslizador
            self.region_valores[clave] = valor_txt
            self._region_cambio(clave, deslizador.get())
        ctk.CTkLabel(
            caja_region, text="Se puede ajustar con el conteo activo.",
            font=ctk.CTkFont(size=10), text_color="#8ba0ad", wraplength=250,
            justify="left").pack(anchor="w", pady=(4, 2))

        sec_linea = SeccionDesplegable(panel, "LÍNEA DE CONTEO")
        sec_linea.pack(fill="x", pady=(6, 0))
        caja_linea = sec_linea.contenido
        self.linea = ctk.CTkSlider(caja_linea, from_=0.20, to=0.85,
                                   command=self._linea_cambio)
        self.linea.set(float(self.config_data.get("objetos_linea", 0.53)))
        self.linea.pack(fill="x", pady=(4, 0))
        self.linea_txt = ctk.CTkLabel(caja_linea, text="")
        self.linea_txt.pack(anchor="w")
        self._linea_cambio(self.linea.get())
        ctk.CTkLabel(
            caja_linea, text="Calibrado: 0.53 conto 15 de 15 piezas reales.",
            font=ctk.CTkFont(size=10), text_color="#8ba0ad", wraplength=250,
            justify="left").pack(anchor="w", pady=(2, 2))

        self.sec_modelo = SeccionDesplegable(panel, "MODELO DE DETECCIÓN")
        self.sec_modelo.pack(fill="x", pady=(6, 0))
        caja_modelo = self.sec_modelo.contenido
        self.modelo_var = ctk.StringVar(value=SIN_MODELOS)
        self.modelo_combo = ctk.CTkComboBox(
            caja_modelo, values=[SIN_MODELOS], variable=self.modelo_var,
            command=self._modelo_elegido)
        self.modelo_combo.pack(fill="x", pady=(4, 0))
        self.b_agregar_modelo = ctk.CTkButton(
            caja_modelo, text="Agregar modelo .pt…", height=30,
            fg_color="#283746", hover_color="#354a5e",
            command=self._agregar_modelo)
        self.b_agregar_modelo.pack(fill="x", pady=(6, 2))
        # Umbral de confianza del modelo, como en el detector de personas.
        self.confianza_txt = ctk.CTkLabel(caja_modelo, text="")
        self.confianza_txt.pack(anchor="w", pady=(6, 0))
        self.confianza = ctk.CTkSlider(
            caja_modelo, from_=0.05, to=0.95, number_of_steps=18,
            command=self._confianza_cambio)
        self.confianza.set(float(self.config_data.get(
            "objetos_confianza", CONFIANZA_MODELO)))
        self.confianza.pack(fill="x")
        self._confianza_cambio(self.confianza.get())
        ctk.CTkLabel(
            caja_modelo, text="El modelo cuenta solo si arriba eliges MODELO.",
            font=ctk.CTkFont(size=10), text_color="#8ba0ad", wraplength=250,
            justify="left").pack(anchor="w", pady=(4, 2))
        self._refrescar_modelos(self.config_data.get("objetos_modelo", ""))

        # La casilla de evidencias va SIEMPRE hasta abajo del panel
        # (pedido de operacion).
        self.evidencia = ctk.CTkCheckBox(panel, text="Guardar evidencias")
        if self.config_data.get("objetos_evidencia", True):
            self.evidencia.select()
        self.evidencia.pack(anchor="w", pady=(14, 10))

        derecha = ctk.CTkFrame(cuerpo, fg_color="transparent")
        derecha.pack(side="left", fill="both", expand=True)

        # Los botones y el registro se empaquetan PRIMERO y anclados abajo:
        # en pack, el ultimo en empaquetarse es el primero en quedarse sin
        # espacio. Con el lienzo al final, la imagen recibe solo el sobrante
        # y ya no puede empujar los botones fuera de la ventana (el bug de
        # "desaparecen los botones al abrir la imagen").
        acciones = ctk.CTkFrame(derecha, fg_color="transparent")
        acciones.pack(side="bottom", fill="x", pady=(10, 0))
        acciones.grid_columnconfigure((0, 1, 2), weight=1)

        registro = ctk.CTkFrame(derecha, height=140)
        registro.pack(side="bottom", fill="x", pady=(10, 0))
        ctk.CTkLabel(registro, text="ÚLTIMOS CONTEOS",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", padx=10, pady=(8, 2))
        self.registro = tk.Listbox(registro, height=6, bg="#0b1218", fg="#c3d2dc",
                                   borderwidth=0, highlightthickness=0)
        self.registro.pack(fill="x", padx=10, pady=(0, 10))

        self.lienzo = ctk.CTkLabel(derecha, text="Sin señal", fg_color="#0b1218",
                                   corner_radius=10)
        self.lienzo.pack(fill="both", expand=True)

        self.b_iniciar = ctk.CTkButton(
            acciones, text="▶  INICIAR CONTEO", height=38,
            fg_color="#178c56", hover_color="#126f45", command=self.iniciar)
        self.b_iniciar.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        self.b_detener = ctk.CTkButton(
            acciones, text="■  DETENER", height=38,
            fg_color="#a6333b", hover_color="#81272e",
            command=self.detener, state="disabled")
        self.b_detener.grid(row=0, column=1, padx=4, sticky="ew")
        # Permite verificar encuadre y region sin ensuciar los conteos.
        self.b_probar = ctk.CTkButton(
            acciones, text="◉  PROBAR CÁMARA", height=38,
            fg_color="#283746", hover_color="#354a5e", command=self.probar)
        self.b_probar.grid(row=0, column=2, padx=(4, 0), sticky="ew")

    def _linea_cambio(self, valor) -> None:
        self.linea_txt.configure(text=f"{float(valor):.2f} de la altura")

    def _fuente_conteo_cambio(self, valor: str) -> None:
        modo = "modelo" if valor == "MODELO" else "clasica"
        self.config_data["objetos_fuente"] = modo
        save_config(self.config_data)
        if modo == "modelo" and not self.sec_modelo.abierta:
            # Lo siguiente que hay que elegir es el modelo: se abre solo.
            self.sec_modelo.alternar()

    def _confianza_cambio(self, valor) -> None:
        """Muestra el umbral y lo aplica en vivo si el modelo esta corriendo."""
        self.confianza_txt.configure(text=f"Confianza: {float(valor):.0%}")
        self.config_data["objetos_confianza"] = round(float(valor), 2)
        save_config(self.config_data)
        worker = getattr(self, "worker", None)
        if worker and worker.is_alive():
            worker.confianza = float(valor)

    # ---- modelo de deteccion ----------------------------------------------
    def _modelos_disponibles(self) -> list[str]:
        return sorted(ruta.name for ruta in MODEL_DIR.glob("*.pt"))

    def _refrescar_modelos(self, seleccion: str = "") -> None:
        nombres = self._modelos_disponibles()
        self.modelo_combo.configure(values=nombres or [SIN_MODELOS])
        # Si el modelo guardado ya no esta en modelos\, se cae al primero
        # disponible: la ausencia no debe impedir abrir el modulo.
        if seleccion in nombres:
            self.modelo_var.set(seleccion)
        else:
            self.modelo_var.set(nombres[0] if nombres else SIN_MODELOS)

    def _modelo_elegido(self, valor: str) -> None:
        if valor == SIN_MODELOS:
            return
        self.config_data["objetos_modelo"] = valor
        save_config(self.config_data)

    def _agregar_modelo(self) -> None:
        """Copia un .pt a modelos\\ y lo deja seleccionado y guardado."""
        ruta = filedialog.askopenfilename(
            title="Elegir modelo YOLO",
            filetypes=[("Modelo PyTorch", "*.pt")])
        if not ruta:
            return
        origen = Path(ruta)
        try:
            ModelMixin._validate_model_file(origen)
        except (OSError, ValueError) as error:
            messagebox.showerror(APP_NAME, str(error))
            return
        destino = MODEL_DIR / origen.name
        if destino.resolve() != origen.resolve():
            if destino.exists() and not messagebox.askyesno(
                    APP_NAME, f"Ya existe {origen.name} en modelos\\. "
                    "¿Reemplazarlo con el archivo elegido?"):
                return
            try:
                shutil.copy2(origen, destino)
            except OSError as error:
                messagebox.showerror(
                    APP_NAME, f"No se pudo copiar el modelo:\n{error}")
                return
        self._refrescar_modelos(destino.name)
        self._modelo_elegido(destino.name)

    def _region_cambio(self, clave: str, valor) -> None:
        self.region_valores[clave].configure(text=f"{int(valor)} px")
        # La region se aplica en vivo: recuadrar sin detener el conteo. El
        # worker conserva el total y solo reaprende el fondo (~1 s).
        worker = getattr(self, "worker", None)
        if worker and worker.is_alive():
            worker.actualizar_region(self._region())

    # ---- ciclo ------------------------------------------------------------
    def _region(self) -> dict:
        return {k: int(s.get()) for k, s in self.region_sliders.items()}

    def probar(self) -> None:
        """Muestra la camara en vivo sin contar ni guardar nada."""
        self._arrancar(solo_vista=True)

    def iniciar(self) -> None:
        self._arrancar(solo_vista=False)

    def _arrancar(self, solo_vista: bool) -> None:
        try:
            origen, nombre = self.fuente.construir()
            region = self._region()
        except ValueError as error:
            messagebox.showerror(APP_NAME, str(error))
            return

        self.fuente.guardar_en(self.config_data)
        self.config_data["objetos_region"] = region
        self.config_data["objetos_linea"] = round(float(self.linea.get()), 3)
        self.config_data["objetos_evidencia"] = bool(self.evidencia.get())
        modo = "modelo" if self.fuente_conteo.get() == "MODELO" else "clasica"
        modelo_nombre = self.modelo_var.get()
        if modelo_nombre == SIN_MODELOS:
            modelo_nombre = ""
        self.config_data["objetos_fuente"] = modo
        self.config_data["objetos_modelo"] = modelo_nombre
        save_config(self.config_data)

        modelo_ruta = None
        if modo == "modelo":
            ruta_modelo = MODEL_DIR / modelo_nombre if modelo_nombre else None
            if ruta_modelo and ruta_modelo.is_file():
                modelo_ruta = str(ruta_modelo)
            else:
                # Sin archivo de modelo se avisa y se arranca igual con la
                # clasica: la banda nunca se queda sin contar.
                messagebox.showwarning(
                    APP_NAME, "Elegiste contar con MODELO pero no hay un "
                    "modelo .pt disponible. Se continúa con visión clásica; "
                    "agrega uno en «Modelo de detección».")

        self.registro.delete(0, "end")
        self.solo_vista = solo_vista
        self.worker = ContadorWorker(
            origen, region, float(self.linea.get()),
            bool(self.evidencia.get()) and not solo_vista, self.cola,
            self.store, self.evidencias, nombre, solo_vista=solo_vista,
            modelo_ruta=modelo_ruta,
            confianza=float(self.confianza.get()))
        self.worker.start()
        if solo_vista:
            self.estado.configure(text="VISTA DE CÁMARA", text_color="#4ea1d3")
        else:
            self.estado.configure(text="CONTANDO", text_color="#4aa96c")
        self.b_iniciar.configure(state="disabled")
        self.b_probar.configure(state="disabled")
        self.b_detener.configure(state="normal")

    def detener(self) -> None:
        if self.worker:
            self.worker.detener.set()
            # Esperar a que el hilo cierre la camara: si la ventana se destruye
            # antes, el hilo sigue escribiendo en una cola que ya no existe.
            self.worker.join(timeout=3)
            self.worker = None
        self.estado.configure(text="DETENIDO", text_color="#d95c5c")
        self.b_iniciar.configure(state="normal")
        self.b_probar.configure(state="normal")
        self.b_detener.configure(state="disabled")

    def _sondear(self) -> None:
        try:
            while True:
                dato = self.cola.get_nowait()
                if dato.get("error"):
                    messagebox.showerror(APP_NAME, dato["error"])
                    self.detener()
                    continue
                if dato.get("fin"):
                    self.detener()
                    continue
                if dato.get("equipo"):
                    self._recordar_equipo(dato["equipo"])
                    continue
                if dato.get("aviso"):
                    self.estado.configure(text=dato["aviso"].upper(),
                                          text_color="#d9a441")
                    continue
                self._pintar(dato)
        except queue.Empty:
            pass
        self.after(60, self._sondear)

    def _recordar_equipo(self, equipo: str) -> None:
        """Muestra el equipo que infiere y lo deja anotado en la configuracion.

        `last_device` es la memoria del equipo entre arranques: `load_config()`
        la lee para aplicar el perfil correcto antes de construir la ventana,
        sin pagar la importacion de torch. Hasta ahora solo la escribia el
        detector de personas, asi que abrir unicamente este modulo en un equipo
        con GPU seguia arrancando con el perfil de CPU.
        """
        self.estado.configure(text=f"MODELO EN {equipo.upper()}",
                              text_color="#d9a441")
        anotado = "gpu" if equipo.upper() != "CPU" else "cpu"
        if self.config_data.get("last_device") != anotado:
            self.config_data["last_device"] = anotado
            save_config(self.config_data)

    def _pintar(self, dato: dict) -> None:
        total = dato["total"]
        self.m_total.set(str(total))
        self.m_banda.set(str(dato["en_banda"]))
        self.m_fuente.set(
            "MODELO" if dato.get("metodo") == "modelo" else "CLÁSICA")
        transcurrido = max(dato["transcurrido"], 1e-6)
        self.m_ritmo.set(f"{total * 3600 / transcurrido:,.0f}" if total else "—")
        # Cada renglon lleva el id interno de la pieza: permite auditar un
        # conteo puntual sin ensuciar el video con numeros.
        momento = format_timestamp_12h(
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        for identificador in dato.get("ids_contados", ()):
            self.registro.insert(
                0, f"  pieza #{identificador}   {momento}")
        if not dato["listo"]:
            self.estado.configure(text="APRENDIENDO EL FONDO", text_color="#d9a441")
        elif self.worker and getattr(self, "solo_vista", False):
            self.estado.configure(text="VISTA DE CÁMARA", text_color="#4ea1d3")
        elif self.worker:
            self.estado.configure(text="CONTANDO", text_color="#4aa96c")

        vista = dato["vista"]
        # La imagen se ajusta al espacio del lienzo en AMBOS ejes: si solo se
        # respeta el alto, un video ancho desborda la ventana hacia los lados.
        alto_dest = max(self.lienzo.winfo_height() - 8, 200)
        ancho_dest = max(self.lienzo.winfo_width() - 8, 200)
        escala = min(alto_dest / vista.shape[0], ancho_dest / vista.shape[1])
        vista = cv2.resize(vista, (max(int(vista.shape[1] * escala), 1),
                                   max(int(vista.shape[0] * escala), 1)))
        imagen = Image.fromarray(cv2.cvtColor(vista, cv2.COLOR_BGR2RGB))
        # CTkImage y no ImageTk: en pantallas con escalado (4K de planta) una
        # PhotoImage se ve borrosa porque no se reescala.
        self._imagen = ctk.CTkImage(light_image=imagen, dark_image=imagen,
                                    size=(imagen.width, imagen.height))
        self.lienzo.configure(image=self._imagen, text="")

    def _cerrar(self) -> None:
        self.detener()
        # Vaciar la cola evita que queden cuadros pendientes al destruir.
        while True:
            try:
                self.cola.get_nowait()
            except queue.Empty:
                break
        self.destroy()


if __name__ == "__main__":
    MODULO = os.environ.get("ARZYZ_MODULE_ID", "objetos")
    failures.configure(MODULO)
    from core.heartbeat import HeartbeatWriter

    HeartbeatWriter(MODULO).start()
    AppObjetos().mainloop()
