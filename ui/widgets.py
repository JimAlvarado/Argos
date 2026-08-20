"""Widgets propios reutilizables de la interfaz."""
from __future__ import annotations

import customtkinter as ctk


class MetricCard(ctk.CTkFrame):
    def __init__(self, master, title: str, value: str, color: str):
        super().__init__(master, corner_radius=8, fg_color="#16202a")
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self, text=title.upper(), font=("Segoe UI", 10, "bold"),
            text_color="#8292a2"
        ).grid(row=0, column=0, padx=12, pady=(7, 0), sticky="w")
        self.value_label = ctk.CTkLabel(
            self, text=value, font=("Segoe UI", 20, "bold"), text_color=color
        )
        self.value_label.grid(row=1, column=0, padx=12, pady=(0, 7), sticky="w")

    def set(self, value: str, color: str | None = None):
        kwargs = {"text": value}
        if color:
            kwargs["text_color"] = color
        self.value_label.configure(**kwargs)


class SeccionDesplegable(ctk.CTkFrame):
    """Submenu plegable: un encabezado que muestra u oculta su contenido.

    Los paneles de configuracion crecen con cada opcion nueva; agrupadas en
    secciones plegables, el operador ve solo lo que esta ajustando. El caller
    empaqueta la seccion y coloca sus widgets dentro de `contenido`.
    """

    def __init__(self, master, titulo: str, abierta: bool = False):
        super().__init__(master, fg_color="transparent")
        self.titulo = titulo
        self.abierta = False
        self.encabezado = ctk.CTkButton(
            self, text=f"▸  {titulo}", anchor="w", height=30,
            fg_color="#1d2935", hover_color="#28394a",
            command=self.alternar)
        self.encabezado.pack(fill="x")
        self.contenido = ctk.CTkFrame(self, fg_color="transparent")
        if abierta:
            self.alternar()

    def alternar(self) -> None:
        self.abierta = not self.abierta
        flecha = "▾" if self.abierta else "▸"
        self.encabezado.configure(text=f"{flecha}  {self.titulo}")
        if self.abierta:
            self.contenido.pack(fill="x", padx=2, pady=(4, 6))
        else:
            self.contenido.pack_forget()
