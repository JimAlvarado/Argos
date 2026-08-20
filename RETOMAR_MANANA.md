# Retomar aqui — 20 de agosto de 2026

Version **0.8.5**. **272 pruebas en verde**
(`python -m unittest discover -s tests -v`).

## AHORA SE USA GPU NVIDIA EN LOS DOS EQUIPOS

Confirmado por Jim el 20-ago. Planta ya **no** es «sin GPU»: el build de PyTorch
es CUDA en las dos maquinas y `+cpu` queda solo como respaldo. `CLAUDE.md`
actualizado. Ojo con la conclusion facil: el cuello de las camaras de estados
**no** es la inferencia sino **decodificar el flujo 4K** (40.2 % de un nucleo por
camara contra 1.5 % del analisis), y eso la GPU no lo cambia salvo que se
decodifique en hardware.

## Tolva: MIDIENDO por deteccion de movimiento

La tolva pasa a `available: True`. No mide nivel de llenado sino **movimiento**:
lo que interesa es cuando se esta cargando y cuanto dura.

**La senal**: `MovimientoEnRegion` en `core/pipeline/senales.py`. Devuelve el %
de pixeles cuyo gris cambio mas de **25 niveles** respecto de la muestra
anterior.

Por que contar pixeles y no promediar la diferencia (medido sobre los 10 min del
video del 20-jul, region de la artesa, 2 Hz, tramos verificados en imagen):

| senal | quieta | cargando | separacion |
|---|---|---|---|
| diferencia media | 1.1 a 1.8 | 3.4 a 12.6 | 3-7x |
| **% pixeles > 25** | **0.05 a 0.30** | **2.3 a 14.7** | **83x** |

La diferencia media **nunca baja de 1.1** con la escena quieta: ese piso es el
RUIDO del sensor, que mueve un poco todos los pixeles y se come la senal. El
umbral por pixel lo descarta.

**Dos estados medidos**: quieta **0.11** (370 muestras, sd 0.09, max 0.61),
cargando **9.41** (480 muestras, sd 6.67). El **100 %** de las muestras quietas
cae por debajo del umbral de salida: cero falsos positivos.

Verificado **4/4** contra imagen: t=105 y 340 sin cargador (0.14 y 0.09);
t=420 cargador descargando con los faros reflejando (21.2); t=545 chatarra
cayendo en el aire (12.5).

**Validado de punta a punta** con `tools.calibrar_estado --estacion tolva`:
9 ciclos de carga en los tramos correctos (35-89, 202-214, 269-295, 397-453,
515-570 s).

### Dos cosas que el video ya dejo claras

- **El carro NO se mueve** en estos 10 min: sigue en la misma posicion. Lo que se
  mueve es el cargador frontal y la chatarra. Si operacion quiere detectar el
  **envio de carga al horno** (el carro desplazandose por los rieles), hace falta
  video con ese movimiento: esta senal no lo distingue del acto de cargar.
- **Cuanto cuenta como «una carga» es decision de operacion.** El cargador hace
  pausas de 5-10 s dentro de un mismo ciclo, asi que el CONTEO depende de la
  permanencia (10 cargas con 3 s, 6 con 5 s) y el tiempo total del umbral
  (37 % con los dos estados medidos, 26 % con los que deduce el k-medias).
  Los 4 ciclos macro del video se ven claros; el criterio para agruparlos no lo
  pone el codigo.

### Decision de diseno: senales con memoria

La de movimiento necesita el cuadro ANTERIOR, y las otras tres son funciones
puras. En vez de duplicar el motor, se declara como **CLASE** en `ESTACIONES` y
`construir_senal()` la instancia una vez por medicion, dentro del hilo. Las
funciones pasan intactas, asi que **el mantenedor se comporta igual** (cubierto
con prueba). Si se guardara ya instanciada, dos procesos compartirian el cuadro
previo y la primera muestra tras un reinicio se compararia contra la ultima de la
sesion anterior.

### Bugs prevenidos, cada uno con prueba que falla sin el arreglo

1. **Los faros del cargador**: el brillo de la region sube ~10 niveles al entrar
   la maquina. Con el umbral en 25 no cuenta como movimiento. Bajarlo a 5 hace
   fallar la prueba. Era el falso positivo mas probable en una nave de noche.
2. **Mover la region con la medicion corriendo**: la interfaz lo permite, y
   `absdiff` con dos formas distintas **lanza excepcion y tumba el ciclo**.
   Quitar la guarda produce ERROR en la prueba.

### Al probar en vivo

`region` esta calibrada para el encuadre del video del 20-jul. Antes de INICIAR
MEDICION, darle **SOLO VISTA** y confirmar que el recuadro cae sobre el interior
de la artesa: es la misma leccion del mantenedor. La camara no se movio en el
video (max 0.83 px), pero el margen contra el umbral de 2.0 px es de 2.4x, mucho
menos holgado que el del mantenedor (0.07 px): si el paso del cargador la hace
vibrar, puede marcar hueco.

    set ARZYZ_MODULE_ID=tolva && python detector_estados.py

## Tablero: la linea de fundicion quedo unificada

Las cuatro camaras de la linea estaban **dispersas** en el tablero (01 objetos,
05 mantenedor, 06 tolva, 07 horno) con placas, vehiculos y personas
intercalados, y ni en el orden del proceso. Ahora hay dos secciones:

- **Proceso de fundicion**: tolva (01) -> horno (02) -> mantenedor (03) ->
  lingotera/objetos (04), en el orden real del flujo.
- **Seguridad y vigilancia**: personas (05), placas (06), vehiculos (07).

`MODULES` en `centro_control.py` quedo en el MISMO orden, porque `status()` lo
recorre en orden y alimenta `/api/status`: si el registro y el HTML discrepan, el
operador ve una cosa y la API entrega otra. La lista de la linea se declara una
sola vez, en `MODULOS_FUNDICION`.

`?v=29` en app.js y styles.css. **No se toco la logica de ningun detector**, ni
los identificadores (`data-module`, `data-start`, `ARZYZ_MODULE_ID`), asi que los
latidos y el supervisor siguen igual.

Cubierto por `AgrupacionDeLaLineaTest` (5 pruebas nuevas), y **se verifico que
falla al reintroducir el bug**: al intercalar "personas" en la linea, 3 de las 5
fallan senalando el orden roto.

### Un proceso por camara: validado con medicion

Medido sobre un cuadro 4K real en la laptop G15:

| Parte | Costo | % de un nucleo |
|---|---|---|
| recortar + senal R-G | 2.41 ms/muestra | 0.48 % |
| vigilancia de camara movida | 4.92 ms/muestra | 0.98 % |
| **analisis total** (2 Hz) | **7.33 ms** | **1.47 %** |
| **decodificar el flujo 4K en vivo** | — | **40.2 %** |

El dato que decide: **la decodificacion cuesta 27 veces mas que el analisis**.
Cada camara tiene que decodificar su propio flujo pase lo que pase, asi que
unificar los cuatro modulos en un proceso **no ahorraria nada medible** (~1.5 %
de nucleo por estacion) y costaria el aislamiento de fallos, que es justo lo que
hace que la caida de un RTSP no se lleve las otras tres camaras.

Cuatro procesos suman ~1.7 nucleos de decodificacion mas la inferencia de la
lingotera, sobre los **20 nucleos** de la PC de planta. Un proceso por camara se
confirma como la decision correcta. Medido en la G15: conviene repetirlo en
planta, donde hay mas nucleos pero puede que mas lentos por nucleo.

## Estado en una linea

Llegaron los tres videos del horno y **el resultado es negativo, con numeros**:
"encendido/apagado" **NO es medible** con la camara 2. No se activo nada, no se
toco codigo de comportamiento y la deuda quedo documentada donde se iba a
tropezar con ella. El mantenedor y el conteo siguen intactos.

## Lo que se midio (96 min de video, 19 y 20-ago)

| Video | OSD | Que hay |
|---|---|---|
| `HornoR 1-2026-08-19_02h00...` | 02:00-02:20 | fundiendo, boca a la vista |
| `HornoR 1-2026-08-19_02h26...` | 02:26-03:22 | ciclo completo: funde, **cuela 03:12**, tambor gira 03:21 |
| `HornoR 1-2026-08-20_07h50...` | 07:50-08:10 | horno caliente, boca entrando y saliendo de vista |

Region medida: la de `ESTACIONES["horno"]` (verificada en imagen ANTES de medir,
cae sobre la boca del tambor en sus dos posiciones). Camara **no** es PTZ en la
practica: desplazamiento maximo 0.22 px en 20 min.

| Estado | R-G | area | brillo |
|---|---|---|---|
| apagado / boca oculta | -2.41 a +0.39 | 0.2 % | +91 a +96 |
| quemador en vacio | +0.91 (sd 0.61) | 2.3 % | +98 |
| fundiendo, boca a la vista | +15.11 a +23.72 | 8-27 % | +94 a +115 |
| **colada (vaciado)** | **+28.82** | **25.4 %** | **+120** |

## Por que NO se activo el horno

La senal mide si **SE VE** el interior incandescente, y eso lo decide la
**posicion del tambor**, no si el horno esta encendido.

- Verificado en imagen: en el video del 19-ago la senal cae de **+11.65 a +0.39
  entre 03:20:59 y 03:21:39** porque el tambor gira y tapa la boca. El horno
  sigue caliente.
- Simulado con `MaquinaDeEstado` (apagado -0.5, encendido +19, permanencia 3 s),
  el video del 20-ago reporta **19m 29s de "apagado" en 20 min de video** sobre
  un horno que la imagen muestra encendido de principio a fin: **97 % del tiempo
  mal asignado**. Y **sin parpadeo**: intervalos limpios y duraciones
  plausibles. El registro se habria visto impecable y habria estado mal.

**`brillo` quedo descartado con medicion**: el minimo del encendido (+90.98) es
igual al del apagado (+90.25) y el maximo del apagado **de dia** (+135.63)
supera la mediana del encendido **de noche** (+112.85). Los rangos se solapan
porque la region incluye las ventanas del fondo. Un umbral de brillo mide la
hora del dia. Era la candidata anotada en el codigo.

`R-G` sirve y es robusto a la luz (el apagado da ~0 igual de dia que de noche,
porque en gris R-G vale 0 por definicion), pero no puede separar "quemador
ardiendo en vacio" (+0.91) de "apagado" (-2.41 a +0.39): la sd del ruido es
0.3-0.6 y los rangos se tocan. Con esa banda la histeresis vibraria en cada
muestra.

## Lo primero al retomar

1. **Decidir con operacion que es "encendido"** (ver la tabla de abajo). De la
   respuesta depende si el horno se mide por camara, por PLC, o no se mide.
2. Si la respuesta es "hay carga caliente trabajando", **la colada es la
   estacion que si tiene firma robusta** (brillo +120 contra ~95, R-G +28.8,
   25 % de area, ~2 min sostenidos). Vale mas para operacion que un
   encendido/apagado y esta al alcance: haria falta validar contra 3-4 coladas
   mas para fijar los dos estados.
3. El mantenedor sigue como estaba: fijar la PTZ de frente y darle SOLO VISTA
   unos minutos antes de INICIAR MEDICION (ver la seccion del 19-ago).

## Hallazgo aparte: un cuadro corrupto se lee como camara movida

En el video del 19-ago, **1 muestra de 6467** (t=1333.4 s) dio un desplazamiento
de **157.7 px** con la camara quieta. El video trae errores de decodificacion
HEVC en ese punto (`Could not find ref with POC 36`), asi que es un cuadro
corrupto, no un giro.

Con `DESPLAZAMIENTO_MAXIMO = 2.0` y `SEGUNDOS_DE_ASENTAMIENTO = 3.0` eso enclava
"camara movida" y mete ~3-4 s de hueco en el intervalo en curso. **Afecta al
mantenedor**, que es el que esta midiendo, y por RTSP un paquete perdido hace lo
mismo.

**No se corrigio a proposito**: el arreglo natural (exigir DOS muestras
consecutivas sobre el umbral) cambia el comportamiento de la vigilancia PTZ que
esta validada, y eso no se toca en el mismo paso que un analisis. Es un cambio
aparte, chico, con su prueba de regresion. El dano hoy es acotado: el intervalo
queda marcado `con_hueco`, que es justamente admitir que no se sabe.

## Lo que falta y bloquea

| Falta | Bloquea | Quien |
|---|---|---|
| **Que es "encendido"**: quemador ardiendo o carga caliente | Toda la camara 2 | Operacion |
| **Umbral de alarma por duracion** (segundos de puerta abierta) | Que el mantenedor avise, no solo cuente | Operacion |
| **Video de la tolva vacia y llena** | Umbral de llenado | Planta |
| `Tolva norte` vs `Tolva Sur` (archivo dice una cosa, el OSD otra) | Nombrar la fuente sin mentir | Jim |

Sobre **"girando"**: ya no esta bloqueado por falta de video. Estos videos
muestran el tambor **cambiando de posicion** (verificado: boca de frente a
03:20:59, oculta a 03:21:39). Falta decidir si a operacion le sirve "el tambor
se movio" o necesita "gira en continuo", que es un analisis distinto
(periodicidad, no posicion).

---

# Anterior: 19 de agosto de 2026 — fase 2 y GPU

Version **0.8.5**. **258 pruebas en verde**
(`python -m unittest discover -s tests -v`).

## Estado en una linea

Se abrio la **fase 2** (estados con duracion) y el **mantenedor ya mide**:
verificado contra su video real en **507.6 s vs 506.1 s manuales (0.3 % de
diferencia)**. El conteo de lingotes NO se toco. Ademas la laptop G15 ya corre
con **GPU NVIDIA** (3.2x mas rapido que en CPU).

## Lo primero al retomar

1. **Fijar la PTZ del mantenedor** de frente, como quedo acordado.
2. Abrir el modulo **Mantenedor** desde el centro de control y darle
   **SOLO VISTA** unos minutos con la camara en vivo: confirmar que el recuadro
   cae sobre el vano. Toda la validacion es sobre archivo; el encuadre en vivo
   puede diferir.
3. Si el recuadro no cae bien, moverlo con los deslizadores (estan en
   porcentaje del cuadro) y volver a guardar arrancando la medicion.
4. Luego **INICIAR MEDICION** y dejarlo. Al terminar, revisar el registro y el
   CSV del dia.

## Lo que falta y bloquea

| Falta | Bloquea | Quien |
|---|---|---|
| **Umbral de alarma por duracion** (segundos de puerta abierta) | Que el mantenedor avise, no solo cuente | Operacion |
| **Video del horno GIRANDO** | Detectar "girando" | Planta |
| **Video del horno APAGADO** | Umbral encendido/apagado | Planta |
| **Video de la tolva vacia y llena** | Umbral de llenado | Planta |
| `Tolva norte` vs `Tolva Sur` (archivo dice una cosa, el OSD otra) | Nombrar la fuente sin mentir | Jim |

Operacion ya confirmo que **8 de cada 10 minutos abierta NO es normal**; solo
falta el numero del limite.

## Como calibrar una estacion nueva (tolva, horno)

Cuando llegue el video, es **un comando**. No hay que rehacer analisis:

```bat
python -m tools.calibrar_estado "C:\ruta\video.mp4" --senal ocupacion --region 0.26,0.26,0.52,0.16
python -m tools.calibrar_estado "C:\ruta\video.mp4" --estacion mantenedor
```

Senales disponibles: `rosado` (R−G, la del mantenedor), `brillo` (candidata
para la llama del horno), `ocupacion` (candidata para el nivel de la tolva).

Deja en `data\calibracion\<video>\`: `region.jpg` (verificar ESTE primero),
cuadros antes/despues de cada transicion, y `senal.csv`.

Al final imprime los dos valores medidos. Si cuadran con las imagenes, se
copian a `core\pipeline\senales.py` (`inactivo_medido`, `activo_medido`), se
pone `calibrada: True`, y en `centro_control.py` la estacion pasa a
`available: True`. Nada mas.

**Verificado**: contra el video del mantenedor el calibrador dedujo solo
`-3.53 / +21.98` y encontro el mismo intervalo (8m 28s, 84.7 % del video).

## Lo que se hizo hoy

### 1. GPU NVIDIA en la laptop G15

- `torch 2.13.0+cu130` y `torchvision 0.28.0+cu130` (mismas versiones, solo
  cambia el build). Comandos exactos en `INSTALAR_WINDOWS_11.md`.
- La decision GPU/CPU vive **solo** en `core/runtime.py`. Ningun modulo vuelve
  a consultar `torch.cuda`.
- **Medido**: modelo de lingotes sobre el recorte real, CPU 56.9 ms/cuadro
  contra GPU 17.8 ms. **3.2x**, con 33 MB de VRAM de los 4 GB.
- Ojo: la precision se pide con `quantize` (16 o None), **no** con `half`;
  Ultralytics 8.4 dejo `half` deprecado.
- La PC de planta no requiere ningun cambio de codigo, solo el build `+cpu`.

### 2. Fase 2: motor de estados

- `core/pipeline/estados.py` — histeresis, permanencia minima e intervalos con
  duracion. Recibe una senal escalar y el tiempo desde fuera; **nunca consulta
  el reloj**, asi sirve igual a 2 Hz que a 25 Hz.
- Tabla `estados` en `EventStore` (solo agrega; las tres tablas anteriores
  intactas), con `resumen_de_estados()` para los totales del turno.
- `core/pipeline/senales.py` — las senales y la calibracion de cada estacion,
  sin dependencias de interfaz para que el calibrador las use.

### 3. Modulo del mantenedor

- `detector_estados.py` — **UN script para las tres estaciones**, lanzado como
  **tres procesos**. La estacion sale de `ARZYZ_MODULE_ID`. Asi hay una sola
  base de codigo sin perder el aislamiento de fallos.
- Registrado en `centro_control.py`: `mantenedor` disponible, `tolva` y `horno`
  en `available: False` hasta calibrarse.
- Tarjetas nuevas en el tablero (05, 06, 07). `?v=28` en styles y app.js.
- Todo documentado en **`MANTENEDOR.md`**.

### 4. Fase 0 medida sobre los videos reales

| Camara | Resultado |
|---|---|
| **Mantenedor** | Senal `R−G` en el vano: cerrada **−3.77**, abierta **+20.74**. Verificado 4/4 contra imagen |
| **Horno encendido** | La boca da 30-100x el fondo. Medible; falta muestra de apagado |
| **Horno girando** | **El tambor NO gira** en el video del 20-jul. Verificado a resolucion nativa y por ausencia de periodicidad, con el video de la tolva como control. El tambor SI tiene textura, asi que la tecnica no esta descartada |
| **Tolva** | Llenado progresivo y contrastado (chatarra clara sobre interior oscuro) |

Los videos traen **reloj en pantalla (OSD)**: es la base de la correlacion
entre camaras sin sincronizar procesos. Reconstruido del 20-jul: tolva se llena
03:27-03:37, el horno recibe la carga a las **03:52:34** (se ve la tolva en el
encuadre del horno).

## Bugs encontrados y corregidos hoy

1. **Titulo de ventana**: el detector de PERSONAS se anunciaba como
   "Deteccion de objetos". Con varios modulos abiertos a la vez es confusion
   garantizada. Corregido con prueba.
2. **CSV que perdia datos en silencio**: `append_csv` usa
   `extrasaction="ignore"`, asi que cualquier columna no declarada se descarta
   sin avisar — la duracion no habria llegado nunca al CSV. Se agregaron cuatro
   columnas **al final** (`duracion`, `duracion_s`, `fin`, `observaciones`) y
   `append_csv` ahora **migra solo** un archivo con encabezado viejo.
3. **Vigilancia de PTZ mal disenada** (mio): comparaba contra una referencia
   fija del arranque, asi que al abrirse la puerta el desplazamiento saltaba a
   **cientos de pixeles con la camara inmovil** y el modulo dejaba de detectar
   aperturas. Debe compararse entre muestras **consecutivas**.
4. **Prueba que daba falsa confianza**: la de antiparpadeo pasaba incluso con la
   histeresis eliminada, porque la senal oscilaba y nunca sostenia un valor
   dentro de la banda. Reescrita.

## Contratos nuevos (estan en CLAUDE.md)

- Estados con duracion viven SOLO en `core/pipeline/estados.py`.
- El intervalo empieza en el **cruce**, no en la confirmacion (si no, toda
  duracion sale corta justo por la permanencia: error sistematico).
- Histeresis con **dos** umbrales, derivados del **punto medio de los dos
  estados medidos**, nunca de percentiles del rango observado.
- Columnas nuevas del CSV **siempre al final**.
- El desplazamiento de camara se mide entre **consecutivas**.
- Una estacion sin `calibrada: True` **no arranca**.

## Deuda consciente

`detector_objetos.ContadorWorker._abrir` hace lo mismo que
`core.camera.abrir_fuente` y **no se migro a proposito**: ese modulo cuenta
lingotes en produccion verificado 15/15, y cambiarle la apertura de camara en
el mismo paso que se agrega un modulo nuevo mezcla dos riesgos. Es un cambio
aparte, aislado y con su propia verificacion.

## Donde estan los videos

```
C:\Users\Sistemas\Documents\PROYECTOS\ARGOS\Videos\Videos\Videos_ARGOS\
  PTZ Mantenedor sur-2026-08-19_03h15min00s000ms.mp4    10 min  2560x1440
  HornoR 1-2026-07-20_03h48min00s000ms.mp4              11 min  3840x2160
  Tolva norte-2026-07-20_03h27min00s000ms.mp4           10 min  3840x2160
  Lingotera\...                                         los del conteo
```

## Comandos utiles

```bat
python -m unittest discover -s tests -v      :: 258 pruebas
python -m tools.calibrar_estado <video> ...  :: calibrar una estacion
python detector_estados.py                   :: mantenedor (o ARZYZ_MODULE_ID)
python -m tools.diagnostico                  :: reporte tecnico
python -m core.failures --ultimas 30         :: ultimas fallas
iniciar_centro_control.bat                   :: tablero
```

---

# Anterior: 16 de agosto de 2026 — conteo de lingotes

El modulo cuenta con `lingotes_v2_20260815.pt` a confianza **0.40**, calibrado
contra el colado completo del 14-ago: **2359 vs 2352 fisicas (+0.3 %)**. La
configuracion actual ES la calibrada: no mover region (1280,1128,1356,1004),
linea (0.60) ni confianza (0.40).

**Pendiente de ese frente:** colado en vivo con fuente MODELO comparado contra
el conteo fisico de estibas; debe caer dentro de ±1 %. Si difiere, NO mover
perillas a mano: las detecciones crudas del colado estan en
`data\analisis_colado_20260814\` y permiten reproducir el conteo a cualquier
umbral en segundos.

Detalle completo con mediciones en `MODULO_OBJETOS.md`.

| Dato medido | Valor |
|---|---|
| Ciclo entre piezas | 3.8 s (~950/hora) |
| Linea de conteo | 0.53 calibrada (config actual usa 0.60) |
| Confianza del modelo | 0.40 (curva completa en MODULO_OBJETOS.md) |

**Regla acordada:** solo cuenta la **pieza completa**.

## Estado del proyecto

| Etapa | Estado |
|---|---|
| Pasos 1-4 + kernel, fallas, diagnostico, capturador | Hecho |
| Modulo Deteccion de Objetos (clasica) | Hecho — 15/15 verificado |
| Modelo de lingotes v2 como fuente de conteo | Operando — validar en turno real |
| GPU NVIDIA en la laptop G15 | Hecho — 3.2x medido |
| **Fase 2: motor de estados + tabla** | **Hecho** |
| **Mantenedor (camara 3)** | **Hecho — verificado contra video** |
| Tolva (camara 1) | Falta video para calibrar |
| Horno encendido (camara 2) | Falta muestra de apagado |
| Horno girando (camara 2) | Bloqueado: falta video con el tambor girando |
| Linea de tiempo de colada (fase 5) | Pendiente |
| PLC | Existe y gobierna horno y mantenedor. Esta etapa es solo camaras; la costura esta puesta (`origen` en la tabla `estados`) |

## Regla de trabajo

Cada entrega es una carpeta con nombre propio y version; no se sobrescribe
nada. Se analiza siempre la ultima entregada. Nada escribe fuera de su propia
carpeta.
