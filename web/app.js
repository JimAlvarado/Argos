const state = {
  modules: [],
  currentView: location.pathname === "/dashboard" ? "dashboard" : "modules",
  liveSource: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHtml = (value) => String(value).replace(
  /[&<>"']/g,
  (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[character],
);

function setView(view, updateHistory = true) {
  state.currentView = view;
  $$(".view").forEach((element) => element.classList.remove("active"));
  $$(".nav-item").forEach((element) => element.classList.remove("active"));
  $(`#${view}-view`).classList.add("active");
  $(`.nav-item[data-view="${view}"]`).classList.add("active");
  $("#page-title").textContent = view === "dashboard"
    ? "Dashboard operativo"
    : "Módulos de detección";
  if (updateHistory) {
    history.pushState({ view }, "", view === "dashboard" ? "/dashboard" : "/");
  }
  if (view === "dashboard") loadDashboard();
}

function toast(message, isError = false) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.toggle("error", isError);
  element.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("show"), 3600);
}

async function api(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(url, { cache: "no-store", ...options, headers });
  const contentType = response.headers.get("Content-Type") || "";
  const content = await response.text();
  if (!contentType.includes("application/json")) {
    throw new Error(
      "El servicio está desactualizado. Cierra la consola anterior y vuelve a ejecutar iniciar_centro_control.bat.",
    );
  }
  let data;
  try {
    data = JSON.parse(content);
  } catch {
    throw new Error("El servicio devolvió información incompleta. Reinicia el Centro de Control.");
  }
  if (!response.ok) {
    const error = new Error(data.message || "No fue posible completar la operación.");
    error.data = data;
    throw error;
  }
  return data;
}

function applyModuleStatus(modules) {
  state.modules = modules;
  const active = modules.filter((module) => module.running).length;
  $("#active-count").textContent = active;
  $("#dashboard-active").textContent = active;

  for (const module of modules) {
    const card = $(`.module-card[data-module="${module.id}"]`);
    if (!card) continue;
    card.classList.toggle("running", module.running);
    const badge = card.querySelector(".module-state");
    const button = card.querySelector(".module-action");
    if (module.running) {
      badge.textContent = "Activo";
      if (button) button.innerHTML = "Mostrar detector <span>→</span>";
    } else if (module.available) {
      badge.textContent = "Disponible";
      if (button) button.innerHTML = "Abrir detector <span>→</span>";
    }
    aplicarSupervision(card, module);
  }
}

// El estado que reporta el supervisor, no el supuesto: un modulo puede figurar
// como "activo" y estar congelado sin emitir latido.
function aplicarSupervision(card, module) {
  let marca = card.querySelector(".module-supervision");
  if (!marca) {
    marca = document.createElement("p");
    marca.className = "module-supervision";
    card.appendChild(marca);
  }
  const s = module.supervision;
  if (!s || !module.running) {
    marca.textContent = "";
    marca.className = "module-supervision";
    return;
  }
  const edad = s.latido_hace;
  const latido =
    edad == null ? "sin latido" : `latido hace ${edad.toFixed(0)} s`;
  const reinicios = s.reinicios
    ? ` · ${s.reinicios} reinicio${s.reinicios > 1 ? "s" : ""}`
    : "";
  // Debe cubrir los cuatro estados de kernel/supervisor.py.
  const etiquetas = {
    activo: "Supervisado",
    reiniciando: "Reiniciando…",
    caido: "Caído",
    detenido: "Detenido",
  };
  marca.textContent = `${etiquetas[s.estado] || s.estado} · ${latido}${reinicios}`;
  marca.className = `module-supervision estado-${s.estado}`;
}

async function loadStatus() {
  try {
    const data = await api("/api/status");
    applyModuleStatus(data.modules);
  } catch (error) {
    toast(error.message, true);
  }
}

async function startModule(moduleId, button) {
  button.disabled = true;
  const previous = button.innerHTML;
  button.textContent = "Abriendo ventana…";
  toast("Preparando el módulo. La ventana aparecerá en unos instantes.");
  try {
    const data = await api(`/api/modules/${moduleId}/start`, { method: "POST" });
    toast(data.message);
    applyModuleStatus(data.modules);
  } catch (error) {
    toast(error.message, true);
    button.innerHTML = previous;
  } finally {
    button.disabled = false;
  }
}

function renderChart(series) {
  const max = Math.max(...series.map((item) => item.value), 1);
  $("#bar-chart").innerHTML = series.map((item) => {
    const day = new Date(`${item.date}T12:00:00`);
    const label = day.toLocaleDateString("es-MX", { weekday: "short" });
    const height = Math.max((item.value / max) * 175, 4);
    return `
      <div class="bar-column">
        <span class="bar-value">${item.value}</span>
        <div class="bar" style="height:${height}px"></div>
        <span class="bar-label">${label}</span>
      </div>`;
  }).join("");
}

function renderClasses(classes) {
  if (!classes.length) {
    $("#class-list").innerHTML = '<div class="empty-state">Aún no hay detecciones para mostrar.</div>';
    return;
  }
  const max = Math.max(...classes.map((item) => item.value), 1);
  $("#class-list").innerHTML = classes.map((item) => `
    <div class="class-row">
      <span class="class-name">${escapeHtml(item.name)}</span>
      <div class="class-track"><div class="class-fill" style="width:${(item.value / max) * 100}%"></div></div>
      <span class="class-value">${item.value}</span>
    </div>`).join("");
}

function renderRecent(events) {
  if (!events.length) {
    $("#recent-events").innerHTML = '<tr><td colspan="4">No hay eventos registrados.</td></tr>';
    return;
  }
  $("#recent-events").innerHTML = events.map((event) => `
    <tr>
      <td>${escapeHtml(event.time)}</td>
      <td>${escapeHtml(event.source)}</td>
      <td>${escapeHtml(event.summary)}</td>
      <td class="confidence">${event.confidence}%</td>
    </tr>`).join("");
}

function applyDashboard(data) {
  $("#events-today").textContent = data.stats.events_today.toLocaleString("es-MX");
  $("#objects-today").textContent = data.stats.objects_today.toLocaleString("es-MX");
  $("#crossings-today").textContent = data.stats.crossings_today.toLocaleString("es-MX");
  $("#alerts-today").textContent = data.stats.alerts_today.toLocaleString("es-MX");
  $("#events-total").textContent = `${data.stats.events_total.toLocaleString("es-MX")} históricos`;
  renderChart(data.series);
  renderClasses(data.classes);
  renderRecent(data.recent);
}

async function loadDashboard() {
  try {
    applyDashboard(await api("/api/dashboard"));
    $("#live-updated").textContent = "ACTUALIZADO";
  } catch (error) {
    toast(error.message, true);
  }
}

function connectLive() {
  state.liveSource?.close();
  const source = new EventSource("/api/live");
  state.liveSource = source;
  source.addEventListener("update", (event) => {
    const data = JSON.parse(event.data);
    applyModuleStatus(data.modules);
    applyDashboard(data.dashboard);
    $("#live-updated").textContent = new Date().toLocaleTimeString("es-MX", {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  });
  source.onopen = () => {
    $("#live-updated").textContent = "CONECTADO";
  };
  source.onerror = () => {
    $("#live-updated").textContent = "RECONECTANDO";
  };
}

const evidenceTitles = {
  objects: ["Capturas de objetos", "Detecciones con evidencia visual guardada."],
  crossings: ["Capturas de cruces", "Objetos registrados al atravesar la línea."],
  alerts: ["Capturas de alertas", "Ingresos detectados dentro de zonas vigiladas."],
};

function renderEvidence(data, category) {
  const items = data.items || [];
  viewer.items = items;
  if (!items.length) {
    const legacyCrossings = category === "crossings" && data.total_events > 0;
    const detail = legacyCrossings
      ? `${data.total_events} cruces están registrados, pero se crearon antes de habilitar la captura automática. Las imágenes que nunca se guardaron no pueden recuperarse; el próximo cruce sí conservará su evidencia.`
      : "Las nuevas evidencias aparecerán aquí automáticamente.";
    $("#evidence-grid").innerHTML = `
      <div class="evidence-empty">
        No existen capturas en esta categoría.<br>
        ${detail}
      </div>`;
    return;
  }
  viewer.items = items;
  $("#evidence-grid").innerHTML = items.map((item, index) => `
    <article class="evidence-card">
      <a href="${item.image_url}" data-viewer-index="${index}" rel="noopener">
        <img src="${item.thumbnail_url || item.image_url}" alt="${escapeHtml(item.label)}" loading="lazy">
      </a>
      <div class="evidence-info">
        <strong>${escapeHtml(item.label)}</strong>
        <span>${escapeHtml(item.source)}</span>
        <small><time>${escapeHtml(item.time)}</time><b>${item.confidence}%</b></small>
      </div>
    </article>`).join("");
}

async function openEvidence(category) {
  const [title, description] = evidenceTitles[category];
  $("#evidence-title").textContent = title;
  $("#evidence-description").textContent = description;
  $("#evidence-grid").innerHTML = '<div class="evidence-empty">Cargando evidencias…</div>';
  $("#evidence-modal").classList.add("open");
  try {
    const data = await api(`/api/evidence?type=${encodeURIComponent(category)}`);
    renderEvidence(data, category);
  } catch (error) {
    $("#evidence-grid").innerHTML = `<div class="evidence-empty">${escapeHtml(error.message)}</div>`;
  }
}


const viewer = { items: [], index: 0 };

function showViewerAt(index) {
  const total = viewer.items.length;
  if (!total) return;
  viewer.index = Math.min(Math.max(index, 0), total - 1);
  const item = viewer.items[viewer.index];
  const image = $("#viewer-image");
  image.src = item.image_url;
  image.alt = item.label || "Captura";
  $("#viewer-label").textContent = item.label || "";
  $("#viewer-source").textContent = item.source || "";
  $("#viewer-time").textContent = item.time || "";
  $("#viewer-confidence").textContent = item.confidence != null ? `${item.confidence}%` : "";
  $("#viewer-position").textContent = `${viewer.index + 1} de ${total}`;
  $("#viewer-prev").disabled = viewer.index === 0;
  $("#viewer-next").disabled = viewer.index === total - 1;
}

function openViewer(index) {
  if (!viewer.items.length) return;
  showViewerAt(index);
  $("#viewer-modal").classList.add("open");
  $("#viewer-next").focus({ preventScroll: true });
}

function closeViewer() {
  $("#viewer-modal").classList.remove("open");
  $("#viewer-image").removeAttribute("src");
}

function moveViewer(paso) {
  showViewerAt(viewer.index + paso);
}

const viewerIsOpen = () => $("#viewer-modal").classList.contains("open");

// Delegacion: la galeria se redibuja seguido, asi el manejador sobrevive.
$("#evidence-grid").addEventListener("click", (event) => {
  const enlace = event.target.closest("[data-viewer-index]");
  if (!enlace) return;
  event.preventDefault();
  openViewer(Number(enlace.dataset.viewerIndex));
});

$("#viewer-prev").addEventListener("click", () => moveViewer(-1));
$("#viewer-next").addEventListener("click", () => moveViewer(1));

document.addEventListener("keydown", (event) => {
  if (!viewerIsOpen()) return;
  if (event.key === "ArrowLeft") { event.preventDefault(); moveViewer(-1); }
  else if (event.key === "ArrowRight") { event.preventDefault(); moveViewer(1); }
  else if (event.key === "Escape") { event.preventDefault(); closeViewer(); }
});

$$(".nav-item").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

$$("[data-start]").forEach((button) => {
  button.addEventListener("click", () => startModule(button.dataset.start, button));
});

$$("[data-evidence]").forEach((button) => {
  button.addEventListener("click", () => openEvidence(button.dataset.evidence));
});

$$("[data-close-modal]").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.closeModal === "viewer-modal") { closeViewer(); return; }
    $(`#${button.dataset.closeModal}`).classList.remove("open");
  });
});

document.addEventListener("click", (event) => {
  if (!event.target.classList.contains("modal-backdrop")) return;
  if (event.target.id === "viewer-modal") { closeViewer(); return; }
  event.target.classList.remove("open");
});

$("#generar-diagnostico").addEventListener("click", async (event) => {
  const boton = event.currentTarget;
  const original = boton.textContent;
  boton.disabled = true;
  boton.textContent = "Generando…";
  try {
    const data = await api("/api/diagnostico", { method: "POST" });
    // Se muestra aqui mismo: el archivo no sale de la carpeta del proyecto.
    const respuesta = await fetch(
      `/api/diagnostico/archivo?name=${encodeURIComponent(data.archivo)}`,
      { cache: "no-store" },
    );
    const texto = await respuesta.text();
    $("#reporte-texto").textContent = texto;
    $("#reporte-ruta").textContent = data.ruta;
    const hallazgos = data.banderas || [];
    $("#reporte-hallazgos").textContent = hallazgos.length
      ? `${hallazgos.length} hallazgo(s) detectado(s)`
      : "Sin hallazgos automáticos";
    $("#reporte-modal").classList.add("open");
  } catch (error) {
    toast(error.message, true);
  } finally {
    boton.disabled = false;
    boton.textContent = original;
  }
});

$("#reporte-copiar").addEventListener("click", async (event) => {
  const boton = event.currentTarget;
  try {
    await navigator.clipboard.writeText($("#reporte-texto").textContent);
    boton.textContent = "Copiado";
  } catch (error) {
    // Sin permiso de portapapeles: se selecciona para copiar a mano.
    const rango = document.createRange();
    rango.selectNodeContents($("#reporte-texto"));
    const seleccion = window.getSelection();
    seleccion.removeAllRanges();
    seleccion.addRange(rango);
    boton.textContent = "Selecciona y Ctrl+C";
  }
  setTimeout(() => { boton.textContent = "Copiar todo"; }, 2500);
});

$("#refresh-dashboard").addEventListener("click", async () => {
  await Promise.all([loadStatus(), loadDashboard()]);
  toast("Dashboard actualizado.");
});

window.addEventListener("popstate", (event) => {
  setView(
    event.state?.view || (location.pathname === "/dashboard" ? "dashboard" : "modules"),
    false,
  );
});

setInterval(() => {
  $("#clock").textContent = new Date().toLocaleTimeString("es-MX", {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}, 1000);

setView(state.currentView, false);
loadStatus();
connectLive();
