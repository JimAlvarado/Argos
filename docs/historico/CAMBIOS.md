# Arzyz Vision 0.8.5 — que trae esta entrega

> **Empieza por `RETOMAR_MANANA.md`** (en la raiz del proyecto; este documento
> vive archivado en `docs/historico/`).

Paquete completo **sin los modelos `.pt`**. Copialos antes de arrancar:

```bat
copy "..\0.4.2\Nueva estructuracion\modelos\*.pt" "modelos\"
```

## Correccion: el modulo de objetos no abria

`MetricCard` exige cuatro argumentos y el modulo pasaba tres: la ventana moria
al construirse. Corregido y verificado ejecutando la interfaz de verdad.
Detalle en `MODULO_OBJETOS.md`.

## Modulo de Deteccion de Objetos

En el dashboard, **Deteccion Facial** se sustituyo por **Deteccion de Objetos**,
y ya esta disponible: al pulsarlo abre un detector que **cuenta piezas en la
banda sin modelo entrenado**.

Verificado sobre video real: **15 conteos contra 15 del operador, 100%**.
Detalle en `MODULO_OBJETOS.md`.

## Capturador de dataset (fase 3)

`tools\capturador.py` convierte un video de la camara en imagenes etiquetadas.
Sobre 60 s reales produjo 199 imagenes de dia y 249 de noche, con las cajas ya
propuestas. Detalle en `CAPTURADOR.md`.

## Correccion critica en esta version

**El modulo se abria y se cerraba solo al iniciarlo desde el dashboard.**

Dos defectos encadenados:

1. El centro de control registraba el modulo como `personas`, pero el detector
   latia como `detector`. El supervisor no lo escuchaba nunca.
2. El supervisor confundia "no recibo latido" con "esta congelado", asi que
   terminaba un proceso sano y lo relanzaba en bucle.

Corregido: el nombre del modulo nace en un solo lugar y viaja al proceso por
`ARZYZ_MODULE_ID`; y un modulo solo se da por congelado si **alguna vez latio**
y despues enmudecio. Si nunca reporta, se vigila por proceso vivo y se anota un
aviso en `data\logs\`.

## Kernel de supervision (paso 4)

El centro de control ya no solo lanza modulos: los **vigila y los reinicia**.
Cada modulo late una vez por segundo; el supervisor distingue un cierre
intencional de una caida y de un congelamiento, y el dashboard muestra el estado
real de cada tarjeta.

Estado de cada modulo visible en las tarjetas del dashboard.

## Instalacion

```bat
cd /d "<esta carpeta>"
py -3.14 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install torch==2.13.0 torchvision==0.28.0 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt
```

Si ya tienes un `.venv` de otra version, copialo aqui y te ahorras la descarga.

## Arranque

| Archivo | Que hace |
|---|---|
| `iniciar_centro_control.bat` | Abre el dashboard en `127.0.0.1:8765` |
| `iniciar_detector.bat` | Abre el detector directamente |
| `generar_diagnostico.bat` | Genera el reporte en `data\diagnostico\` |

Al abrir el dashboard por primera vez, recarga con **Ctrl+F5**.

## Verificacion

```bat
python -m unittest discover -s tests -v
```

Deben ser **143 pruebas en verde**.

## Estructura

```
detector_empresarial.py     arranque, ciclo del worker, coordinacion
centro_control.py           dashboard y lanzador de modulos
core/                       nucleo sin interfaz grafica
  paths, config, profiles, runtime, storage, evidence,
  camera, packets, utils, failures
  pipeline/                 tracking, crossing, zones, overlay,
                            validation, dedup
ui/                         interfaz dividida por responsabilidad
  layout, geometry, models, alarms, widgets
tools/diagnostico.py        reporte de diagnostico
tests/                      65 pruebas
web/                        dashboard (html, css, js)
config/                     bytetrack_arzyz.yaml
modelos/                    VACIO: copia aqui tus archivos .pt
```

## Historial de esta version

| Etapa | Resultado |
|---|---|
| Paso 1 | `core/` extraido del monolito |
| Paso 2 | `core/pipeline/` — DetectionWorker dividido |
| Paso 3 | `ui/` — DetectorApp dividido |
| Paso 4 | `kernel/` — supervision, latidos y estado real |
| Registro de fallas | `data\logs\`, captura caidas de hilos |
| Visor del dashboard | Flechas izquierda y derecha entre capturas |
| Deduplicacion | 99% menos evidencias repetidas |
| Conteo de objetos | Cuenta identidades distintas, no detecciones |
| Perfil automatico | Mejor resolucion, confianza, NMS y FPS segun equipo |
| Rastreador | Umbrales coherentes con la confianza operativa |
| Diagnostico | `generar_diagnostico.bat` |

`detector_empresarial.py` paso de 4,624 a 1,486 lineas (68% menos).

## Documentos incluidos

| Archivo | Contenido |
|---|---|
| `RETOMAR_MANANA.md` | **Estado actual y siguiente paso** |
| `MODULO_OBJETOS.md` | Conteo de piezas en banda |
| `CAPTURADOR.md` | Generar dataset desde video |
| `DIAGNOSTICO.md` | Como generar y leer el reporte |
| `PASO_1_CORE.md`, `PASO_2_PIPELINE.md`, `PASO_3_UI.md` | Cada refactor |
| `CORRECCIONES_VISOR_Y_EVIDENCIAS.md` | Visor y duplicados |
| `CORRECCIONES_OBJETOS_Y_PERFIL.md` | Conteo, perfil y NMS |
| `ANALISIS_VELOCIDAD.md` | Mediciones originales del equipo |
| `INSTALAR_WINDOWS_11.md` | Instalacion detallada |

## Pendiente

Generar el diagnostico con la camara real y compartir el `.txt`. Con la rotacion
de identidad medida se ajusta `new_track_thresh` con datos, no por suposicion.

Despues viene el paso 4: `kernel/` con supervisor de procesos.
