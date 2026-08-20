"""Construccion de la ventana: encabezado, metricas, controles y paneles.

Extraido de DetectorApp sin modificar la logica. Es un mixin: conserva el acceso
a los widgets y al estado de la ventana. DetectorApp lo compone.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

from pathlib import Path
from tkinter import messagebox

from core.config import (
    DANGER_SOUND_OPTIONS,
    RTSP_TEMPLATES,
    SUPPORTED_CAMERA_BRANDS,
    save_config,
)
from core.paths import APP_NAME
from ui.widgets import MetricCard


class LayoutMixin:
    """Construccion de la ventana: encabezado, metricas, controles y paneles."""

    def _build_ui(self):
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_metrics()
        self._build_workspace()

    def _build_header(self):
        header = ctk.CTkFrame(self, height=62, corner_radius=0, fg_color="#111923")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header, text="◉", font=("Segoe UI", 30, "bold"), text_color="#28d17c"
        ).grid(row=0, column=0, rowspan=2, padx=(20, 10), pady=8)
        ctk.CTkLabel(
            header, text=APP_NAME.upper(), font=("Segoe UI", 18, "bold"),
            text_color="#f3f6f8"
        ).grid(row=0, column=1, sticky="sw")
        ctk.CTkLabel(
            header, text="CENTRO DE MONITOREO INTELIGENTE",
            font=("Segoe UI", 9, "bold"), text_color="#6f8193"
        ).grid(row=1, column=1, sticky="nw")

        self.clock_label = ctk.CTkLabel(
            header, text="", font=("Consolas", 15, "bold"), text_color="#c6d0da"
        )
        self.clock_label.grid(row=0, column=2, rowspan=2, padx=20)
        self._update_clock()

    def _build_metrics(self):
        metrics = ctk.CTkFrame(self, fg_color="transparent")
        metrics.grid(row=1, column=0, padx=12, pady=10, sticky="ew")
        for column in range(4):
            metrics.grid_columnconfigure(
                column, weight=1, uniform="metric_cards"
            )
        self.status_card = MetricCard(metrics, "Estado", "INICIANDO", "#f4b942")
        self.status_card.grid(row=0, column=0, padx=(0, 6), sticky="ew")
        self.fps_card = MetricCard(metrics, "Inferencia", "0.0 FPS", "#43a9ff")
        self.fps_card.grid(row=0, column=1, padx=6, sticky="ew")
        self.objects_card = MetricCard(metrics, "Objetos en escena", "0", "#28d17c")
        self.objects_card.grid(row=0, column=2, padx=6, sticky="ew")
        self.model_card = MetricCard(metrics, "Modelo activo", "CARGANDO…", "#bd8cff")
        self.model_card.grid(row=0, column=3, padx=(6, 0), sticky="ew")

    def _build_workspace(self):
        workspace = ctk.CTkFrame(self, fg_color="transparent")
        workspace.grid(row=2, column=0, padx=12, pady=(0, 12), sticky="nsew")
        workspace.grid_rowconfigure(0, weight=1)
        workspace.grid_columnconfigure(0, weight=0, minsize=258)
        workspace.grid_columnconfigure(
            1, weight=3, uniform="workspace_content"
        )
        workspace.grid_columnconfigure(
            2, weight=2, uniform="workspace_content"
        )

        self._build_controls(workspace)
        self._build_video_panel(workspace)
        self._build_activity_panel(workspace)

    def _build_controls(self, parent):
        panel = ctk.CTkScrollableFrame(
            parent, width=250, corner_radius=10, fg_color="#111923",
            scrollbar_button_color="#283746",
            scrollbar_button_hover_color="#354a5e",
        )
        panel.grid(row=0, column=0, padx=(0, 8), sticky="nsew")

        ctk.CTkLabel(
            panel, text="CONFIGURACIÓN", font=("Segoe UI", 13, "bold"),
            text_color="#dce3e9"
        ).pack(anchor="w", padx=16, pady=(16, 8))

        self.source_var = ctk.StringVar(value=self.config_data["source_type"])
        self.source_combo = ctk.CTkComboBox(
            panel,
            values=["Cámara local", "Cámara IP / RTSP", "Archivo de video"],
            variable=self.source_var,
            command=lambda _: self._show_source_fields(),
        )
        self.source_combo.pack(fill="x", padx=16, pady=5)

        self.source_fields = ctk.CTkFrame(panel, fg_color="transparent")
        self.source_fields.pack(fill="x", padx=16)

        self.camera_index_entry = self._entry(
            self.source_fields, "Índice de cámara", self.config_data["camera_index"]
        )
        configured_brand = self.config_data["brand"]
        if configured_brand not in SUPPORTED_CAMERA_BRANDS:
            configured_brand = "Axis"
        self.brand_var = ctk.StringVar(value=configured_brand)
        self.brand_combo = ctk.CTkComboBox(
            self.source_fields, values=SUPPORTED_CAMERA_BRANDS,
            variable=self.brand_var, command=self._apply_brand
        )
        self.ip_entry = self._entry(
            self.source_fields, "Dirección IP", self.config_data["ip"]
        )
        self.port_entry = self._entry(
            self.source_fields, "Puerto RTSP", self.config_data["port"]
        )
        self.user_entry = self._entry(
            self.source_fields, "Usuario", self.config_data["username"]
        )
        self.password_entry = self._entry(
            self.source_fields, "Contraseña (no se guarda)", "", show="•"
        )
        self.route_entry = self._entry(
            self.source_fields, "Ruta RTSP", self.config_data["route"]
        )
        self.file_entry = self._entry(
            self.source_fields, "Archivo de video", self.config_data["video_file"]
        )
        self.browse_video_button = ctk.CTkButton(
            self.source_fields, text="Examinar video", height=30,
            command=self._browse_video
        )

        ctk.CTkLabel(
            panel, text="MODELO E INFERENCIA", font=("Segoe UI", 11, "bold"),
            text_color="#8292a2"
        ).pack(anchor="w", padx=16, pady=(18, 5))
        self.model_label = ctk.CTkLabel(
            panel, text=self.model_path.name, anchor="w", wraplength=230,
            text_color="#dce3e9"
        )
        self.model_label.pack(fill="x", padx=16)
        self.load_model_button = ctk.CTkButton(
            panel, text="Cargar modelo .pt", height=32, command=self._browse_model
        )
        self.load_model_button.pack(fill="x", padx=16, pady=(5, 6))

        self.class_filter_label = ctk.CTkLabel(
            panel, text="Clases: esperando modelo", anchor="w",
            wraplength=230, text_color="#aeb9c4", font=("Segoe UI", 10)
        )
        self.class_filter_label.pack(fill="x", padx=16, pady=(2, 5))
        class_buttons = ctk.CTkFrame(panel, fg_color="transparent")
        class_buttons.pack(fill="x", padx=16, pady=(0, 10))
        class_buttons.grid_columnconfigure((0, 1), weight=1)
        self.select_classes_button = ctk.CTkButton(
            class_buttons, text="Elegir clases", height=30,
            fg_color="#283746", hover_color="#354a5e",
            command=self.open_class_selector, state="disabled"
        )
        self.select_classes_button.grid(row=0, column=0, padx=(0, 3), sticky="ew")
        self.people_only_button = ctk.CTkButton(
            class_buttons, text="Solo personas", height=30,
            fg_color="#205a48", hover_color="#28715a",
            command=self.select_people_only, state="disabled"
        )
        self.people_only_button.grid(row=0, column=1, padx=(3, 0), sticky="ew")

        self.confidence_label = ctk.CTkLabel(
            panel, text=f"Confianza: {self.config_data['confidence']:.0%}",
            anchor="w", text_color="#aeb9c4"
        )
        self.confidence_label.pack(fill="x", padx=16)
        self.confidence_slider = ctk.CTkSlider(
            panel, from_=0.10, to=0.95, number_of_steps=85,
            command=self._confidence_changed
        )
        self.confidence_slider.set(self.config_data["confidence"])
        self.confidence_slider.pack(fill="x", padx=16, pady=(3, 10))

        ctk.CTkLabel(
            panel, text="Resolución de inferencia", anchor="w",
            text_color="#aeb9c4"
        ).pack(fill="x", padx=16)
        self.image_size_var = ctk.StringVar(
            value=str(self.config_data["image_size"])
        )
        self.image_size_combo = ctk.CTkComboBox(
            panel, values=["640", "960", "1280"],
            variable=self.image_size_var
        )
        self.image_size_combo.pack(fill="x", padx=16, pady=(3, 10))

        ctk.CTkLabel(
            panel, text="FPS objetivo de análisis", anchor="w",
            text_color="#aeb9c4"
        ).pack(fill="x", padx=16)
        self.target_fps_var = ctk.StringVar(
            value=str(int(self.config_data.get("target_fps", 30)))
        )
        self.target_fps_combo = ctk.CTkComboBox(
            panel, values=["10", "15", "20", "30", "60"],
            variable=self.target_fps_var
        )
        self.target_fps_combo.pack(fill="x", padx=16, pady=(3, 10))

        ctk.CTkLabel(
            panel, text="REGLAS Y ALERTAS", font=("Segoe UI", 11, "bold"),
            text_color="#8292a2"
        ).pack(anchor="w", padx=16, pady=(8, 5))
        self.rule_tool_var = ctk.StringVar(value="Cruce de línea")
        self.rule_tool_menu = ctk.CTkOptionMenu(
            panel,
            values=["Cruce de línea", "Zona de alerta"],
            variable=self.rule_tool_var,
            command=self._rule_tool_changed,
            fg_color="#213244",
            button_color="#2b4358",
            button_hover_color="#36546e",
        )
        self.rule_tool_menu.pack(fill="x", padx=16, pady=(0, 6))

        self.rules_container = ctk.CTkFrame(
            panel, fg_color="transparent"
        )
        self.rules_container.pack(fill="x", padx=16, pady=(0, 8))
        self.line_tool_frame = ctk.CTkFrame(
            self.rules_container, fg_color="#121c26", corner_radius=8
        )
        self.line_tool_frame.pack(fill="x")
        self.line_enabled_var = ctk.BooleanVar(
            value=bool(self.config_data.get("line_enabled", True))
        )
        self.line_enabled_check = ctk.CTkCheckBox(
            self.line_tool_frame, text="Activar conteo direccional",
            variable=self.line_enabled_var, command=self._line_enabled_changed
        )
        self.line_enabled_check.pack(anchor="w", padx=10, pady=(9, 5))
        line_buttons = ctk.CTkFrame(
            self.line_tool_frame, fg_color="transparent"
        )
        line_buttons.pack(fill="x", padx=10, pady=(2, 9))
        line_buttons.grid_columnconfigure((0, 1), weight=1)
        self.draw_line_button = ctk.CTkButton(
            line_buttons, text="Trazar", height=30,
            fg_color="#205a48", hover_color="#28715a",
            command=self.begin_line_drawing
        )
        self.draw_line_button.grid(row=0, column=0, padx=(0, 3), sticky="ew")
        self.reset_count_button = ctk.CTkButton(
            line_buttons, text="Borrar", height=30,
            fg_color="#283746", hover_color="#354a5e",
            command=self.clear_line
        )
        self.reset_count_button.grid(row=0, column=1, padx=(3, 0), sticky="ew")

        self.zone_tool_frame = ctk.CTkFrame(
            self.rules_container, fg_color="#121c26", corner_radius=8
        )
        self.zone_enabled_var = ctk.BooleanVar(
            value=bool(self.config_data.get("zone_enabled", False))
        )
        self.zone_enabled_check = ctk.CTkCheckBox(
            self.zone_tool_frame, text="Activar vigilancia de zona",
            variable=self.zone_enabled_var, command=self._zone_enabled_changed
        )
        self.zone_enabled_check.pack(anchor="w", padx=10, pady=(9, 5))
        self.high_danger_var = ctk.BooleanVar(
            value=bool(self.config_data.get("high_danger_zone", False))
        )
        self.high_danger_check = ctk.CTkCheckBox(
            self.zone_tool_frame, text="Zona de alto peligro",
            variable=self.high_danger_var,
            command=self._high_danger_changed,
            fg_color="#c72f3b", hover_color="#e04652",
        )
        self.high_danger_check.pack(anchor="w", padx=10, pady=(1, 6))
        self.danger_sound_var = ctk.StringVar(
            value=self.config_data.get(
                "danger_sound_mode", "Doble pitido"
            )
        )
        self.danger_sound_menu = ctk.CTkOptionMenu(
            self.zone_tool_frame,
            values=DANGER_SOUND_OPTIONS,
            variable=self.danger_sound_var,
            command=self._danger_sound_changed,
            height=28,
            fg_color="#5d2630",
            button_color="#8f2935",
            button_hover_color="#aa3340",
        )
        self.danger_sound_menu.pack(fill="x", padx=10, pady=(0, 5))
        self.load_danger_mp3_button = ctk.CTkButton(
            self.zone_tool_frame,
            text="Cargar MP3 de alarma",
            height=28,
            fg_color="#283746",
            hover_color="#354a5e",
            command=self._browse_danger_mp3,
        )
        self.load_danger_mp3_button.pack(fill="x", padx=10, pady=(0, 3))
        danger_mp3_path = str(
            self.config_data.get("danger_mp3_path", "")
        )
        self.danger_mp3_label = ctk.CTkLabel(
            self.zone_tool_frame,
            text=(
                Path(danger_mp3_path).name
                if danger_mp3_path
                else "Sin MP3 personalizado"
            ),
            anchor="w",
            wraplength=205,
            font=("Segoe UI", 9),
            text_color="#9eabb7",
        )
        self.danger_mp3_label.pack(fill="x", padx=10, pady=(0, 6))
        self.draw_zone_button = ctk.CTkButton(
            self.zone_tool_frame, text="Dibujar polígono", height=30,
            fg_color="#8a5a16", hover_color="#a66c19",
            command=self.begin_zone_drawing
        )
        self.draw_zone_button.pack(fill="x", padx=10, pady=(2, 5))
        zone_buttons = ctk.CTkFrame(
            self.zone_tool_frame, fg_color="transparent"
        )
        zone_buttons.pack(fill="x", padx=10, pady=(0, 9))
        zone_buttons.grid_columnconfigure((0, 1), weight=1)
        self.finish_zone_button = ctk.CTkButton(
            zone_buttons, text="Finalizar", height=28,
            state="disabled", command=self.finish_zone_drawing,
            fg_color="#205a48", hover_color="#28715a"
        )
        self.finish_zone_button.grid(
            row=0, column=0, padx=(0, 3), sticky="ew"
        )
        self.clear_zone_button = ctk.CTkButton(
            zone_buttons, text="Borrar", height=28,
            command=self.clear_zone,
            fg_color="#283746", hover_color="#354a5e"
        )
        self.clear_zone_button.grid(
            row=0, column=1, padx=(3, 0), sticky="ew"
        )

        self.evidence_var = ctk.BooleanVar(value=self.config_data["save_evidence"])
        self.evidence_check = ctk.CTkCheckBox(
            panel, text="Guardar evidencias", variable=self.evidence_var,
            command=self._evidence_changed
        )
        self.evidence_check.pack(anchor="w", padx=16, pady=5)

        self.message_label = ctk.CTkLabel(
            panel, text="Preparando el modelo…", wraplength=235, justify="left",
            text_color="#f4b942", font=("Segoe UI", 10)
        )
        self.message_label.pack(fill="x", padx=16, pady=(12, 10))
        self._show_source_fields()

    def _entry(self, parent, placeholder, value, **kwargs):
        entry = ctk.CTkEntry(parent, placeholder_text=placeholder, **kwargs)
        if value:
            entry.insert(0, str(value))
        return entry

    def _build_video_panel(self, parent):
        panel = ctk.CTkFrame(parent, corner_radius=10, fg_color="#111923")
        panel.grid(row=0, column=1, padx=4, sticky="nsew")
        panel.grid_rowconfigure(1, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(panel, height=40, fg_color="#16202a", corner_radius=8)
        top.grid(row=0, column=0, padx=8, pady=8, sticky="ew")
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            top, text="●  CÁMARA EN VIVO", font=("Segoe UI", 11, "bold"),
            text_color="#28d17c"
        ).grid(row=0, column=0, padx=12, pady=8)
        self.frame_time_label = ctk.CTkLabel(
            top, text="—", font=("Consolas", 10), text_color="#8292a2"
        )
        self.frame_time_label.grid(row=0, column=1, padx=12, sticky="e")

        # Canvas estable: el elemento de imagen se crea una sola vez y después
        # se actualizan sus píxeles. Esto evita el parpadeo causado por recrear
        # CTkLabel/CTkImage y recalcular la geometría en cada cuadro.
        self.video_canvas = tk.Canvas(
            panel, bg="#070b10", highlightthickness=0, bd=0,
            cursor="arrow"
        )
        self.video_canvas.grid(
            row=1, column=0, padx=8, pady=(0, 8), sticky="nsew"
        )
        self.video_image_item = self.video_canvas.create_image(
            0, 0, anchor="nw"
        )
        self.video_placeholder_item = self.video_canvas.create_text(
            0, 0,
            text="SIN SEÑAL\n\nConfigure una fuente e inicie la detección",
            fill="#536273", font=("Segoe UI", 16, "bold"), justify="center"
        )
        self.zone_overlay_item = self.video_canvas.create_polygon(
            0, 0, 0, 0, 0, 0,
            fill="#d27b22", stipple="gray25",
            outline="#ffb45b", width=3, state="hidden"
        )
        self.zone_vertex_items = [
            self.video_canvas.create_oval(
                0, 0, 0, 0, fill="#ffffff", outline="#ffb45b",
                width=2, state="hidden"
            )
            for _ in range(12)
        ]
        self.line_shadow_item = self.video_canvas.create_line(
            0, 0, 0, 0, fill="#071018", width=5, state="hidden"
        )
        self.line_overlay_item = self.video_canvas.create_line(
            0, 0, 0, 0, fill="#30de97", width=2, state="hidden"
        )
        self.line_endpoint_items = [
            self.video_canvas.create_oval(
                0, 0, 0, 0, fill=color, outline="#ffffff", width=2,
                state="hidden"
            )
            for color in ("#30de97", "#30de97")
        ]
        self.line_badge_items = [
            (
                self.video_canvas.create_oval(
                    0, 0, 0, 0, fill="#0e1924", outline="#30de97",
                    width=1, state="hidden"
                ),
                self.video_canvas.create_text(
                    0, 0, text=label, fill="#30de97",
                    font=("Segoe UI", 11, "bold"), state="hidden"
                ),
            )
            for label in ("A", "B")
        ]
        self.line_first_point_item = self.video_canvas.create_oval(
            0, 0, 0, 0, fill="#ffffff", outline="#30de97",
            width=2, state="hidden"
        )
        self.video_canvas.bind("<ButtonPress-1>", self._on_video_click)
        self.video_canvas.bind("<B1-Motion>", self._on_video_drag)
        self.video_canvas.bind("<ButtonRelease-1>", self._on_video_release)
        self.video_canvas.bind("<Motion>", self._on_video_motion)
        self.video_canvas.bind("<Configure>", self._on_video_canvas_configure)

        bottom = ctk.CTkFrame(panel, fg_color="#16202a", corner_radius=8)
        bottom.grid(row=2, column=0, padx=8, pady=(0, 8), sticky="ew")
        bottom.grid_columnconfigure(0, weight=1)
        self.class_summary_label = ctk.CTkLabel(
            bottom, text="No hay objetos detectados", anchor="w",
            font=("Segoe UI", 11), text_color="#aeb9c4"
        )
        self.class_summary_label.grid(row=0, column=0, padx=12, pady=8, sticky="ew")
        self.latency_label = ctk.CTkLabel(
            bottom, text="0 ms", font=("Consolas", 10), text_color="#8292a2"
        )
        self.latency_label.grid(row=0, column=1, padx=12)

        actions = ctk.CTkFrame(panel, fg_color="transparent")
        actions.grid(row=3, column=0, padx=8, pady=(0, 8), sticky="ew")
        actions.grid_columnconfigure((0, 1, 2), weight=1)
        self.start_button = ctk.CTkButton(
            actions, text="▶  INICIAR DETECCIÓN", height=38,
            fg_color="#178c56", hover_color="#126f45",
            command=self.start_detection, state="disabled"
        )
        self.start_button.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        self.stop_button = ctk.CTkButton(
            actions, text="■  DETENER", height=38,
            fg_color="#a6333b", hover_color="#81272e",
            command=self.stop_detection, state="disabled"
        )
        self.stop_button.grid(row=0, column=1, padx=4, sticky="ew")
        self.snapshot_button = ctk.CTkButton(
            actions, text="▣  CAPTURA MANUAL", height=38,
            fg_color="#283746", hover_color="#354a5e",
            command=self.save_manual_snapshot, state="disabled"
        )
        self.snapshot_button.grid(row=0, column=2, padx=(4, 0), sticky="ew")

    def _build_activity_panel(self, parent):
        panel = ctk.CTkFrame(parent, corner_radius=10, fg_color="#111923")
        panel.grid(row=0, column=2, padx=(8, 0), sticky="nsew")
        panel.grid_rowconfigure(5, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            panel, text="ÚLTIMAS DETECCIONES", font=("Segoe UI", 11, "bold"),
            text_color="#dce3e9"
        ).grid(row=0, column=0, padx=12, pady=(12, 6), sticky="w")
        self.crop_canvas = tk.Canvas(
            panel, height=165, bg="#070b10", highlightthickness=0, bd=0
        )
        self.crop_canvas.grid(row=1, column=0, padx=12, sticky="ew")
        self.crop_image_item = self.crop_canvas.create_image(0, 0, anchor="nw")
        self.crop_placeholder_item = self.crop_canvas.create_text(
            0, 0, text="Sin evidencia", fill="#536273",
            font=("Segoe UI", 11)
        )

        count_panel = ctk.CTkFrame(
            panel, fg_color="#16202a", corner_radius=8
        )
        count_panel.grid(row=2, column=0, padx=12, pady=(10, 2), sticky="ew")
        count_panel.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkLabel(
            count_panel, text="CONTEO POR CRUCE DE LÍNEA · SESIÓN",
            font=("Segoe UI", 10, "bold"), text_color="#dce3e9"
        ).grid(row=0, column=0, columnspan=3, padx=10, pady=(7, 2), sticky="w")
        self.cross_total_label = self._count_value(
            count_panel, 0, "TOTAL", "#28d17c"
        )
        self.cross_ab_label = self._count_value(
            count_panel, 1, "A → B", "#43a9ff"
        )
        self.cross_ba_label = self._count_value(
            count_panel, 2, "B → A", "#f4b942"
        )
        self.cross_classes_label = ctk.CTkLabel(
            count_panel, text="Por clase: —", anchor="w", wraplength=330,
            font=("Segoe UI", 9), text_color="#aeb9c4"
        )
        self.cross_classes_label.grid(
            row=3, column=0, columnspan=3, padx=10, pady=(2, 0), sticky="ew"
        )
        self.last_crossing_label = ctk.CTkLabel(
            count_panel, text="Último: sin cruces", anchor="w", wraplength=330,
            font=("Consolas", 9), text_color="#8292a2"
        )
        self.last_crossing_label.grid(
            row=4, column=0, columnspan=3, padx=10, pady=(0, 7), sticky="ew"
        )

        title_row = ctk.CTkFrame(panel, fg_color="transparent")
        title_row.grid(row=4, column=0, padx=12, pady=(10, 5), sticky="ew")
        title_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            title_row, text="REGISTRO DE EVENTOS", font=("Segoe UI", 11, "bold"),
            text_color="#dce3e9"
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            title_row, text="CSV", width=50, height=26,
            fg_color="#205a48", command=self.open_event_csv
        ).grid(row=0, column=1, padx=(0, 5))
        ctk.CTkButton(
            title_row, text="Evidencias", width=88, height=26,
            fg_color="#283746", command=self.open_evidence_folder
        ).grid(row=0, column=2)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Det.Treeview", background="#0d151e", foreground="#dce3e9",
            fieldbackground="#0d151e", borderwidth=0, rowheight=23,
            font=("Segoe UI", 9)
        )
        style.configure(
            "Det.Treeview.Heading", background="#1b2936", foreground="#9fabb7",
            borderwidth=0, font=("Segoe UI", 9, "bold")
        )
        style.map("Det.Treeview", background=[("selected", "#205a48")])
        columns = ("time", "objects", "confidence")
        self.event_tree = ttk.Treeview(
            panel, columns=columns, show="headings", style="Det.Treeview"
        )
        self.event_tree.heading("time", text="FECHA / HORA")
        self.event_tree.heading("objects", text="OBJETOS")
        self.event_tree.heading("confidence", text="CONF.")
        self.event_tree.column("time", width=130, anchor="w")
        self.event_tree.column("objects", width=150, anchor="w")
        self.event_tree.column("confidence", width=55, anchor="center")
        self.event_tree.grid(row=5, column=0, padx=12, pady=(0, 12), sticky="nsew")
        self.event_tree.bind("<Double-1>", self._open_selected_evidence)

    def _count_value(self, parent, column: int, title: str, color: str):
        ctk.CTkLabel(
            parent, text=title, font=("Segoe UI", 8, "bold"),
            text_color="#8292a2"
        ).grid(row=1, column=column, padx=6, pady=(2, 0))
        label = ctk.CTkLabel(
            parent, text="0", font=("Segoe UI", 23, "bold"), text_color=color
        )
        label.grid(row=2, column=column, padx=6, pady=(0, 1))
        return label

    def _show_source_fields(self):
        for child in self.source_fields.winfo_children():
            child.pack_forget()
        source_type = self.source_var.get()
        if source_type == "Cámara local":
            self.camera_index_entry.pack(fill="x", pady=5)
        elif source_type == "Cámara IP / RTSP":
            for widget in (
                self.brand_combo, self.ip_entry, self.user_entry,
                self.password_entry
            ):
                widget.pack(fill="x", pady=4)
        else:
            self.file_entry.pack(fill="x", pady=5)
            self.browse_video_button.pack(fill="x", pady=4)

    def _apply_brand(self, brand):
        self.route_entry.delete(0, "end")
        self.route_entry.insert(0, RTSP_TEMPLATES.get(brand, "/profile1"))

    def _update_class_filter_label(self):
        if not self.available_classes:
            text = "Clases: esperando modelo"
        elif self.enabled_class_names is None:
            text = f"Clases: todas ({len(self.available_classes)})"
        elif len(self.enabled_class_names) <= 3:
            text = "Clases: " + ", ".join(self.enabled_class_names)
        else:
            text = f"Clases activas: {len(self.enabled_class_names)}"
        self.class_filter_label.configure(text=text)

    def select_people_only(self):
        people_aliases = {"person", "persona", "personas", "people"}
        matches = [
            name
            for name in self.available_classes.values()
            if name.strip().casefold() in people_aliases
        ]
        if not matches:
            messagebox.showwarning(
                "Clase no encontrada",
                "El modelo activo no contiene una clase llamada person o persona.",
            )
            return
        self.enabled_class_names = matches
        self.config_data["enabled_class_names"] = matches
        save_config(self.config_data)
        self._update_class_filter_label()
        self._set_message(f"Filtro aplicado · sólo {', '.join(matches)}")

    def open_class_selector(self):
        if not self.available_classes:
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("Clases a detectar")
        dialog.geometry("620x650")
        dialog.minsize(520, 480)
        dialog.transient(self)
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            dialog, text="SELECCIONAR CLASES",
            font=("Segoe UI", 17, "bold"), text_color="#f3f6f8"
        ).grid(row=0, column=0, padx=18, pady=(18, 2), sticky="w")
        ctk.CTkLabel(
            dialog,
            text="Sólo las clases marcadas generarán cuadros, conteos y eventos.",
            font=("Segoe UI", 10), text_color="#8292a2"
        ).grid(row=1, column=0, padx=18, pady=(0, 10), sticky="w")

        scroll = ctk.CTkScrollableFrame(dialog, fg_color="#111923")
        scroll.grid(row=2, column=0, padx=18, pady=6, sticky="nsew")
        scroll.grid_columnconfigure((0, 1), weight=1)

        all_enabled = self.enabled_class_names is None
        enabled_set = set(self.enabled_class_names or ())
        variables = {}
        for position, (class_id, name) in enumerate(self.available_classes.items()):
            variable = ctk.BooleanVar(value=all_enabled or name in enabled_set)
            variables[class_id] = variable
            checkbox = ctk.CTkCheckBox(
                scroll, text=f"{class_id:>2}  {name}", variable=variable,
                font=("Segoe UI", 11)
            )
            checkbox.grid(
                row=position // 2, column=position % 2,
                padx=12, pady=6, sticky="w"
            )

        actions = ctk.CTkFrame(dialog, fg_color="transparent")
        actions.grid(row=3, column=0, padx=18, pady=(8, 18), sticky="ew")
        actions.grid_columnconfigure((0, 1, 2, 3), weight=1)

        def set_all(value: bool):
            for variable in variables.values():
                variable.set(value)

        def select_people():
            set_all(False)
            aliases = {"person", "persona", "personas", "people"}
            for class_id, name in self.available_classes.items():
                if name.strip().casefold() in aliases:
                    variables[class_id].set(True)

        def apply_selection():
            selected = [
                self.available_classes[class_id]
                for class_id, variable in variables.items()
                if variable.get()
            ]
            if not selected:
                messagebox.showwarning(
                    "Sin clases",
                    "Selecciona al menos una clase para continuar.",
                    parent=dialog,
                )
                return
            self.enabled_class_names = (
                None if len(selected) == len(self.available_classes) else selected
            )
            self.config_data["enabled_class_names"] = self.enabled_class_names
            save_config(self.config_data)
            self._update_class_filter_label()
            self._set_message(
                "Filtro aplicado · "
                + (
                    "todas las clases"
                    if self.enabled_class_names is None
                    else f"{len(selected)} clase(s)"
                )
            )
            dialog.destroy()

        ctk.CTkButton(
            actions, text="Todas", height=32, fg_color="#283746",
            command=lambda: set_all(True)
        ).grid(row=0, column=0, padx=(0, 3), sticky="ew")
        ctk.CTkButton(
            actions, text="Ninguna", height=32, fg_color="#283746",
            command=lambda: set_all(False)
        ).grid(row=0, column=1, padx=3, sticky="ew")
        ctk.CTkButton(
            actions, text="Personas", height=32, fg_color="#205a48",
            command=select_people
        ).grid(row=0, column=2, padx=3, sticky="ew")
        ctk.CTkButton(
            actions, text="Aplicar", height=32, fg_color="#178c56",
            command=apply_selection
        ).grid(row=0, column=3, padx=(3, 0), sticky="ew")

    def _confidence_changed(self, value):
        self.confidence_label.configure(text=f"Confianza: {float(value):.0%}")
