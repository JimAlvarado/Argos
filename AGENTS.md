# Arzyz Vision — contexto para Codex

Sistema de visión por computadora **operando en planta** (ARZYZ, aluminio).
Cuenta lingotes en banda y detecta personas. Windows 11, Python 3.14,
cámaras 4K por RTSP. Todo en español: código, comentarios, mensajes y
documentación.

Dos equipos, mismo código: la PC de planta (20 núcleos) y la laptop G15 de
desarrollo (**RTX 3050 Laptop, 4 GB**). **Desde el 20-ago-2026 los dos usan GPU
NVIDIA**: planta ya no es «sin GPU», así que el build de PyTorch es CUDA en
ambos y no `+cpu` en planta. El equipo se elige solo en tiempo de ejecución; no
hay ramas ni banderas por máquina.

## Regla número uno

**Esto está en producción.** Prioriza no romper sobre mejorar. Cambios
quirúrgicos e incrementales; nunca reestructuras masivas. Si una mejora toca
más de un módulo, propón el plan primero y espera aprobación.

## Verificación obligatoria

- Antes de dar por terminado CUALQUIER cambio: `python -m unittest discover -s tests -v`
  → deben ser **272 pruebas en verde** (o más si agregaste; nunca menos).
- Cada corrección de bug lleva su prueba de regresión que falle sin el arreglo.
  Verifica que la prueba detecta el bug reintroduciéndolo antes de cerrar.
- No pruebes solo la lógica: los fallos históricos vivieron en las costuras
  (interfaz, HTML del dashboard, contratos entre archivos).

## Contratos que NO se rompen (cada uno causó un bug real)

- **Latidos**: todo módulo late con el nombre que recibe en `ARZYZ_MODULE_ID`.
  Si el nombre del lanzador y el del módulo difieren, el supervisor lo mata en bucle.
- **`MetricCard`** exige 4 argumentos (master, título, valor, color). Usa
  `MetricCard.set()`, nunca toques `value_label` por dentro.
- **Tarjetas del dashboard**: toda tarjeta disponible necesita `data-start="id"`,
  clase `available` y color de acento en CSS. Sin `data-start`, el botón no hace nada.
- **Base de datos**: todo acceso pasa por `core/storage.py` (`EventStore`),
  único escritor. Nunca abras SQLite directo desde un módulo.
- **Detección clásica**: vive SOLO en `core/pipeline/classic.py`. La comparten
  el módulo de objetos y el capturador; no la dupliques.
- **Estados con duración**: viven SOLO en `core/pipeline/estados.py`. Las
  comparten tolva, horno y mantenedor. El motor recibe una señal escalar y el
  tiempo desde fuera; nunca consulta el reloj (así las pruebas son
  deterministas y sirve igual a 2 Hz que a 25 Hz).
- **El intervalo empieza en el CRUCE, no en la confirmación.** Anotar la hora de
  confirmación resta la permanencia a TODA duración: error sistemático.
- **Histéresis con dos umbrales** (`entra > sale`, se valida en el
  constructor). Con un solo umbral una señal apoyada ahí transiciona en cada
  muestra. Los umbrales se derivan del punto medio entre los DOS estados
  medidos, nunca de percentiles del rango observado: eso depende de cuánto
  duró cada estado y sesga el umbral.
- **`CSV_FIELDS` de `EvidenceManager`**: columnas nuevas SIEMPRE al final
  (operación tiene hojas de Excel apoyadas en el orden). `append_csv` migra
  solo un archivo con encabezado viejo; sin eso, una fila nueva sobre un CSV
  del día anterior queda corrida y el error no salta.
- **Panel de fuente**: `ui/source.py` es compartido entre detectores. La
  contraseña NUNCA se guarda en disco.
- **RTSP**: la fuente puede ser una LISTA de rutas candidatas (Provision usa
  /profile1-3); se prueban una por una con timeout, jamás se pasa la lista a OpenCV.
- Al subir versión de frontend, incrementa `?v=` en app.js y styles.css.

## Reglas del negocio (acordadas con operación)

- **Solo cuenta la pieza completa**: un lingote cortado por el borde NO cuenta.
- Línea de conteo calibrada en **0.53** (barrida contra conteo manual: 0.35→8,
  0.53→15 exacto, 0.75→13). Si cambia la cámara, recalibrar contra conteo manual.
- Región de interés lingotera: `1600,1140,560,1020` (día). La cámara puede
  reposicionarse desde configuración: verificar encuadre, no asumir.
- Antiparpadeo obligatorio: sin él, el conteo se infla 193% (medido).
- Evidencias a media escala: a tamaño completo son 611 MB/hora y saturan el disco.

## Entorno

- El proyecto vive en `C:\Proyectos` — NUNCA en OneDrive/Dropbox (bloquean el
  latido y congelan módulos; lección aprendida).
- Nada se escribe fuera de la carpeta del proyecto. Los datos van a `data\`.
- `data\`, `__pycache__`, `tmp*`, `node_modules` y `modelos\*.pt` jamás se
  incluyen en entregas ni commits.
- ffmpeg disponible en PATH.
- **Equipo de inferencia**: la decisión GPU/CPU vive SOLO en `core/runtime.py`
  (`hay_gpu`, `dispositivo_inferencia`, `cuantizacion`, `nombre_equipo`,
  `preparar_equipo`). Ningún módulo vuelve a consultar `torch.cuda` por su
  cuenta. `device` y `quantize` se pasan explícitos en cada inferencia: sin
  ellos Ultralytics autodetecta y el equipo usado queda invisible.
- La precisión se pide con `quantize` (16 o None), **no** con `half`:
  Ultralytics 8.4 dejó `half` deprecado.
- El build de PyTorch decide el equipo, no el código: `+cu130` en **los dos**
  equipos desde el 20-ago-2026. Comandos exactos en `INSTALAR_WINDOWS_11.md`.
  El build `+cpu` queda solo como respaldo si una máquina se queda sin GPU.
- `last_device` en la configuración recuerda el equipo entre arranques para
  aplicar el perfil sin pagar la importación de torch. Lo escriben los dos
  detectores; si solo lo escribiera uno, abrir el otro arrancaría con el
  perfil equivocado.

## Estilo

- Español en todo. Nombres descriptivos, sin abreviaturas crípticas.
- Comentarios explican el PORQUÉ (decisiones, mediciones), no el qué.
- Umbrales y constantes salen de mediciones reales documentadas, no de
  valores inventados; anota la medición junto a la constante.

## Estado actual y pendientes

- Módulo de objetos verificado 15/15 (100%) contra conteo manual.
- **Fase 2 en curso** — cuatro cámaras del proceso: tolva → horno rotatorio →
  mantenedor → lingotera (ésta ya en producción). Un solo tablero web; un
  proceso por cámara para no perder el aislamiento de fallos.
  - Hecho: motor `core/pipeline/estados.py`, tabla `estados` en EventStore y
    `detector_estados.py` — UN script para las tres estaciones, parametrizado
    por `ARZYZ_MODULE_ID` y lanzado como TRES procesos (registro `MODULES` con
    `mantenedor` y `tolva` disponibles; solo `horno` en `available: False`).
    Documentado en `MANTENEDOR.md`.
  - El registro `MODULES` y las tarjetas del tablero van en el ORDEN DEL
    PROCESO y agrupados: tolva → horno → mantenedor → lingotera en «Proceso de
    fundición», y personas/placas/vehículos en «Seguridad y vigilancia». La
    lista se declara una sola vez en `MODULOS_FUNDICION`. `status()` recorre
    `MODULES` en orden y alimenta `/api/status`: si el registro y el HTML
    discrepan, el operador ve una cosa y la API entrega otra.
  - **Cámara PTZ**: el desplazamiento global se compara entre muestras
    CONSECUTIVAS, nunca contra una referencia fija del arranque. Con referencia
    fija la aparición del resplandor hace saltar el desplazamiento a cientos de
    píxeles con la cámara inmóvil, y el módulo deja de detectar aperturas. Está
    cubierto con prueba de regresión sobre video sintético con textura.
  - Una estación sin `calibrada: True` NO arranca: sin los dos estados medidos
    no hay umbral posible y medir con uno inventado da datos que parecen buenos.
  - **Mantenedor (cámara 3)**: señal validada 4/4 contra imagen. El
    discriminador es `R − G` en el vano, NO el brillo: el resplandor del metal
    es lo único magenta en una nave gris, así que es robusto a la iluminación.
    Medido 19-ago-2026: cerrada −3.77, abierta +20.74. Cámara PTZ — no se movió
    en 10 min (desplazamiento máx. 0.07 px), pero el módulo DEBE vigilar el
    desplazamiento global y pausar avisando si se reposiciona.
    Operación: 8 de cada 10 min abierta NO es normal; hay que temporizar,
    guardar y alarmar por duración.
  - **Horno (cámara 2)**: «encendido/apagado» **NO es medible con esta cámara**
    — medido el 20-ago-2026 sobre 96 min de video, no es opinión. La señal
    óptica mide si **se ve** el interior incandescente, y eso lo decide la
    **posición del tambor**: la señal cae de +11.65 a +0.39 en 40 s porque el
    tambor gira y tapa la boca, con el horno caliente. Simulado con el motor
    real, el video del 20-ago reporta 19m 29s de «apagado» en 20 min sobre un
    horno encendido todo el rato (97% mal asignado) y **sin parpadeo**: el
    registro se ve impecable y está mal. «Encendido» tiene que venir del PLC.
    El `brillo` está **descartado con medición**: el mínimo del encendido
    (+90.98) iguala al del apagado (+90.25) y el máximo del apagado de DÍA
    (+135.63) supera la mediana del encendido de NOCHE (+112.85) porque la
    región incluye las ventanas; un umbral de brillo mide la hora del día.
    Lo que sí tiene firma óptica robusta es la **colada**: brillo +120 contra
    ~95 de fondo, R−G +28.8 y 25% de área, ~2 min sostenidos.
  - **Tolva (cámara 1)**: MIDIENDO por **detección de movimiento**, no por
    nivel de llenado. `MovimientoEnRegion` devuelve el % de píxeles cuyo gris
    cambió más de 25 niveles respecto de la muestra anterior. Medido el
    20-ago-2026 y verificado 4/4 contra imagen: quieta **0.11** (sd 0.09),
    cargando **9.41** (sd 6.67) — 83× de separación, y el 100% de las muestras
    quietas cae bajo el umbral de salida. **Contar píxeles, no promediar la
    diferencia**: la diferencia media nunca baja de 1.1 con la escena quieta
    porque el ruido del sensor mueve un poco todos los píxeles, y solo separa
    3-7×. El umbral de 25 también es lo que hace que los **faros** del cargador
    (que suben el brillo ~10 niveles) no cuenten como movimiento.
    Pendiente de operación: **cuánto cuenta como «una carga»** (el cargador hace
    pausas de 5-10 s dentro de un ciclo, así que el conteo depende de la
    permanencia). Y el carro NO se mueve en el video del 20-jul: para cronometrar
    el «envío al horno» hace falta video con el carro desplazándose.
    El archivo dice «Tolva norte» y el OSD «Tolva Sur»: resolver antes de
    nombrar la fuente.
  - **Señales con memoria**: la de movimiento necesita el cuadro anterior, y las
    otras son funciones puras. Se declara como CLASE en `ESTACIONES` y
    `construir_senal()` la instancia una vez por medición, dentro del hilo. Si
    se guardara instanciada, dos procesos compartirían el cuadro previo.
  - Los videos traen reloj en pantalla (OSD): es la base de la correlación
    entre cámaras sin sincronizar procesos.
  - PLC: existe y gobierna horno y mantenedor. Esta etapa es solo cámaras; la
    costura está puesta (`origen` en la tabla `estados`) para crecer a PLC sin
    migrar nada.
- Pendiente: entrenar YOLOv8 de lingotes (dataset en data\dataset\). El módulo
  de objetos ya tiene el selector de modelo listo. **Regla de operación
  (14-ago-2026): una sola fuente cuenta a la vez** — con modelo elegido cuenta
  el modelo, sin modelo la visión clásica; nunca las dos juntas. Si el modelo
  no carga, la clásica retoma el conteo. Cada conteo se audita en `model_name`.
- Migración a Postgres: solo se reimplementa EventStore; los módulos no se tocan.
- Detector de personas: validar rotación de identidad (<2x) con diagnóstico real.
