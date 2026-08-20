"""Panel de seleccion de fuente de video, compartido entre detectores.

Los mismos campos, los mismos tamanos y la misma construccion de URL para todos
los modulos. Vive en un solo archivo a proposito: si cada detector armara sus
campos por su cuenta, con el tiempo dejarian de comportarse igual y una camara
configurada en uno no funcionaria en el otro.

Uso:

    self.fuente = PanelDeFuente(panel, self.config_data)
    ...
    origen, nombre = self.fuente.construir()   # lanza ValueError si falta algo
    self.fuente.guardar_en(self.config_data)
"""
from __future__ import annotations

from pathlib import Path
from tkinter import filedialog
from urllib.parse import quote

import customtkinter as ctk

from core.config import (
    RTSP_ROUTE_CANDIDATES,
    RTSP_TEMPLATES,
    SUPPORTED_CAMERA_BRANDS,
)

TIPOS_DE_FUENTE = ["Cámara local", "Cámara IP / RTSP", "Archivo de video"]


class PanelDeFuente:
    """Construye y gobierna los campos de fuente dentro de un panel."""

    def __init__(self, panel, config_data: dict, al_cambiar=None):
        self.config_data = config_data
        self._al_cambiar = al_cambiar

        self.source_var = ctk.StringVar(
            value=config_data.get("source_type", TIPOS_DE_FUENTE[0]))
        self.source_combo = ctk.CTkComboBox(
            panel, values=TIPOS_DE_FUENTE, variable=self.source_var,
            command=lambda _: self.mostrar_campos(),
        )
        self.source_combo.pack(fill="x", padx=16, pady=5)

        self.campos = ctk.CTkFrame(panel, fg_color="transparent")
        self.campos.pack(fill="x", padx=16)

        self.camera_index_entry = self._entrada(
            "Índice de cámara", config_data.get("camera_index", "0"))

        marca = config_data.get("brand", "Axis")
        if marca not in SUPPORTED_CAMERA_BRANDS:
            marca = "Axis"
        self.brand_var = ctk.StringVar(value=marca)
        self.brand_combo = ctk.CTkComboBox(
            self.campos, values=SUPPORTED_CAMERA_BRANDS,
            variable=self.brand_var, command=self._aplicar_marca,
        )
        self.ip_entry = self._entrada("Dirección IP", config_data.get("ip", ""))
        self.port_entry = self._entrada("Puerto RTSP", config_data.get("port", "554"))
        self.user_entry = self._entrada("Usuario", config_data.get("username", ""))
        # La contrasena no se guarda en disco: se escribe en cada arranque.
        self.password_entry = self._entrada("Contraseña (no se guarda)", "", show="•")
        self.route_entry = self._entrada("Ruta RTSP", config_data.get("route", ""))
        self.file_entry = self._entrada(
            "Archivo de video", config_data.get("video_file", ""))
        self.browse_video_button = ctk.CTkButton(
            self.campos, text="Examinar video", height=30,
            command=self._elegir_video,
        )
        self.mostrar_campos()

    # ---- construccion -----------------------------------------------------
    def _entrada(self, marcador: str, valor, **extra) -> ctk.CTkEntry:
        entrada = ctk.CTkEntry(self.campos, placeholder_text=marcador, **extra)
        if valor:
            entrada.insert(0, str(valor))
        return entrada

    def mostrar_campos(self) -> None:
        """Muestra solo los campos del tipo de fuente elegido."""
        for hijo in self.campos.winfo_children():
            hijo.pack_forget()
        tipo = self.source_var.get()
        if tipo == "Cámara local":
            self.camera_index_entry.pack(fill="x", pady=5)
        elif tipo == "Cámara IP / RTSP":
            for widget in (self.brand_combo, self.ip_entry,
                           self.user_entry, self.password_entry):
                widget.pack(fill="x", pady=4)
        else:
            self.file_entry.pack(fill="x", pady=5)
            self.browse_video_button.pack(fill="x", pady=4)
        if self._al_cambiar:
            self._al_cambiar(tipo)

    def _aplicar_marca(self, marca) -> None:
        self.route_entry.delete(0, "end")
        self.route_entry.insert(0, RTSP_TEMPLATES.get(marca, "/profile1"))

    def _elegir_video(self) -> None:
        ruta = filedialog.askopenfilename(
            title="Elegir video",
            filetypes=[("Video", "*.mp4 *.avi *.mkv *.mov"), ("Todos", "*.*")],
        )
        if ruta:
            self.file_entry.delete(0, "end")
            self.file_entry.insert(0, ruta)

    # ---- uso --------------------------------------------------------------
    def construir(self):
        """Devuelve (origen, nombre_legible). Lanza ValueError si falta algo."""
        tipo = self.source_var.get()
        if tipo == "Cámara local":
            try:
                indice = int(self.camera_index_entry.get().strip() or "0")
            except ValueError as error:
                raise ValueError("El índice de cámara debe ser un número.") from error
            return indice, f"Cámara local {indice}"

        if tipo == "Archivo de video":
            ruta = Path(self.file_entry.get().strip())
            if not ruta.is_file():
                raise ValueError("Selecciona un archivo de video válido.")
            return str(ruta), ruta.name

        ip = self.ip_entry.get().strip()
        if not ip:
            raise ValueError("Ingresa la dirección IP de la cámara.")
        puerto = "554"
        marca = self.brand_var.get()
        rutas = RTSP_ROUTE_CANDIDATES.get(
            marca, [RTSP_TEMPLATES.get(marca, "/profile1")])
        usuario = quote(self.user_entry.get().strip(), safe="")
        clave = quote(self.password_entry.get(), safe="")
        credenciales = f"{usuario}:{clave}@" if usuario or clave else ""
        origenes = []
        for ruta in rutas:
            if not ruta.startswith("/"):
                ruta = "/" + ruta
            origenes.append(f"rtsp://{credenciales}{ip}:{puerto}{ruta}")
        return (origenes if len(origenes) > 1 else origenes[0]), f"RTSP {ip}"

    def guardar_en(self, config: dict) -> dict:
        """Vuelca los campos a la configuracion. Nunca guarda la contrasena."""
        config.update({
            "source_type": self.source_var.get(),
            "camera_index": self.camera_index_entry.get().strip() or "0",
            "brand": self.brand_var.get(),
            "ip": self.ip_entry.get().strip(),
            "port": "554",
            "username": self.user_entry.get().strip(),
            "route": RTSP_TEMPLATES.get(self.brand_var.get(), "/profile1"),
            "video_file": self.file_entry.get().strip(),
        })
        return config
