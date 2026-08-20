# Módulo de estados — Mantenedor

Mide **cuántas veces se abre la puerta del mantenedor y cuánto tiempo
permanece abierta**. Cada apertura queda con su hora de inicio, su duración y
su hora de cierre.

Es el primer usuario de `core/pipeline/estados.py`, el motor compartido de
estados con duración. El mismo módulo (`detector_estados.py`) servirá a la tolva
y al horno rotatorio cuando sus señales estén calibradas.

## Uso

Desde el centro de control: tarjeta **Mantenedor**.

Por su cuenta:

```bat
python detector_estados.py
```

La estación se toma de `ARZYZ_MODULE_ID` (por omisión `mantenedor`). El centro
de control la inyecta al lanzar, y el módulo late con ese mismo nombre.

Botones:

| Botón | Qué hace |
|---|---|
| **INICIAR MEDICIÓN** | Mide y **guarda** en la base y en el CSV |
| **SOLO VISTA** | Muestra y mide en pantalla, **sin guardar nada**. Para calibrar la región |
| **DETENER** | Cierra la fuente y registra el intervalo en curso |

Funciona igual con archivo de video que con RTSP. Con archivo se reproduce al
ritmo de la cámara para poder mirarlo.

## Los tres datos que entrega

| Dato | Dónde se ve | Dónde queda guardado |
|---|---|---|
| **Inicio del contador** | Tarjeta ESTADO cambia a ABIERTO y arranca el cronómetro | `estados.inicio` · columna `fecha_hora` del CSV |
| **Tiempo abierto** | Tarjeta TIEMPO EN ESE ESTADO, en vivo | `estados.duracion_s` · columnas `duracion` y `duracion_s` |
| **Cierre** | Aparece la línea en el registro | `estados.fin` · columna `fin` del CSV |

Además, dos acumulados del día leídos de la base (no de memoria, para que
reiniciar el módulo no ponga el turno en cero): **veces abierto** y **tiempo
acumulado**.

El registro en pantalla se ve así:

```
── inicio de medición · 19/08/2026 18:37:30 · PTZ Mantenedor sur.mp4
CERRADO   inicio 18:37:30  cierre 18:38:41  duración     1m 12s  [parcial]
ABIERTO   inicio 18:38:41  cierre 18:47:09  duración     8m 28s
CERRADO   inicio 18:47:09  cierre 18:47:30  duración        21s  [parcial]
```

## Cómo se detecta, y por qué así

La señal **no es el brillo**, es el color: el promedio de **R menos G** dentro
del vano de la puerta.

Medido el 19-ago-2026 sobre `PTZ Mantenedor sur`:

| | brillo medio | R − G |
|---|---|---|
| Puerta cerrada | 70.8 | **−3.77** |
| Puerta abierta | 98.1 | **+20.74** |

El brillo también separa, pero peor y con más riesgo: una lámpara, un reflejo o
el cambio de turno lo mueven. El resplandor del metal fundido es **lo único
magenta** en una nave gris, y en gris `R − G` vale ~0 por definición. Por eso la
señal resiste cambios de iluminación, que era el riesgo principal.

Los umbrales salen del **punto medio entre los dos estados medidos**, no de
percentiles del rango observado. Esa distinción importa: como la puerta estuvo
abierta el 84 % del video de referencia, un umbral por percentiles caía *dentro*
de la distribución de «abierta» y funcionaba por casualidad.

```
entra  cuando R−G > +11.55
sale   cuando R−G < +5.42
```

Dos umbrales y no uno: con uno solo, una señal apoyada justo ahí transiciona en
cada muestra. Y un cambio debe **sostenerse 3 segundos** antes de aceptarse,
porque en esa escena hay un montacargas estacionado y personal caminando frente
al vano: alguien cruzando no es que la puerta se cerró.

Muestreo a **2 Hz**. Basta de sobra para una puerta que se abre por minutos, y
deja el CPU libre.

## Cámara PTZ: la salvaguarda

La cámara del mantenedor es PTZ, así que **puede reposicionarse** y dejar el
recorte apuntando a otro lado. Si eso pasa sin avisar, el módulo reportaría
«cerrada» para siempre sin quejarse.

El módulo compara el desplazamiento global entre muestras **consecutivas**. Si
supera 2 px, marca la cámara como movida, avisa en pantalla y **entrega «sin
dato»** al motor en vez de una lectura inventada. El intervalo afectado queda
con la bandera `con_hueco`.

Medido con la PTZ quieta: **0.06 px máximo en 10 minutos**, incluida la apertura
completa de la puerta. El umbral de 2 px deja 28 veces de margen.

> **Por qué consecutivas y no contra una imagen de referencia.** Se probó con
> referencia fija y falla: al abrirse la puerta aparece una región magenta
> enorme, la correlación de fase pierde su pico en cero y el desplazamiento
> salta a 68, 97 y hasta 464 px **con la cámara perfectamente quieta**. Es
> decir, detectaba el cambio de escena, que es justo lo que se quiere medir.
> Por la misma razón el módulo **no intenta confirmar** que la cámara «volvió» a
> su posición: esa comparación no es fiable. Suelta el aviso cuando el cuadro
> lleva 3 s quieto y deja el intervalo marcado, para que el dato no se lea como
> limpio.

La señal depende de que la escena tenga textura repartida (pilares, vigas,
líneas de piso). La nave real la tiene de sobra; un encuadre contra una pared
lisa degradaría esta salvaguarda.

## Calidad del dato

Dos banderas viajan con cada intervalo, y ambas están en la base y en el CSV:

- **`parcial`** — la duración es una **cota inferior**: no se observó el inicio
  (el módulo arrancó con la puerta ya abierta) o no se observó el fin (se
  detuvo el módulo). Sin esta bandera, un promedio de duraciones mezclaría
  intervalos truncados con reales.
- **`con_hueco`** — hubo muestras sin dato confiable durante el intervalo.

El resumen del tablero cuenta los parciales aparte por esa razón.

## Dónde queda el dato

Tabla `estados` de `data\detecciones.db`, escrita **solo** por `EventStore`:

| Columna | Contenido |
|---|---|
| `estacion` | `mantenedor` |
| `estado` | `abierto` / `cerrado` |
| `inicio`, `fin`, `duracion_s` | El intervalo |
| `source` | La cámara o el archivo |
| `origen` | `camara:rosado` — cómo se midió |
| `parcial`, `con_hueco` | Calidad del dato |
| `valor_medio` | Promedio de la señal en el intervalo, para auditar |

`origen` es el equivalente de `model_name` en las otras tablas y es **la costura
del PLC**: el día que una señal venga del autómata valdrá `plc:...` y lo ya
guardado seguirá siendo distinguible sin migrar nada.

En el CSV diario de evidencias se agregaron cuatro columnas **al final**
(`duracion`, `duracion_s`, `fin`, `observaciones`) para no correr de lugar las
que operación ya usa en Excel. `duracion_s` es numérica a propósito, para poder
sumar el tiempo abierto en una tabla dinámica.

## Verificación

Contra el video real `PTZ Mantenedor sur-2026-08-19`, 10 minutos, 2560×1440:

| | |
|---|---|
| Aperturas detectadas | **1** |
| Duración medida | **507.6 s** (8m 28s) |
| Duración por conteo manual | 506.1 s |
| Diferencia | **1.5 s** (0.3 %) |
| Desplazamiento máximo | 0.060 px → cámara quieta |
| Intervalos con hueco | 0 |

La diferencia de 1.5 s tiene explicación: el módulo detecta la apertura ~1 s
antes que la medición manual porque su umbral es más bajo, así que toma el
flanco de subida más temprano.

El estado abierto/cerrado se verificó **contra la imagen** en los cuatro
instantes de transición (t = 65, 80, 570 y 585 s): 4 de 4 correctos.

## Pendiente

- **Umbral de alarma por duración.** El campo existe y en 0 no avisa. No se
  puso un valor porque no está acordado con operación, y en este proyecto los
  umbrales salen de mediciones y acuerdos, no de suposiciones. Operación ya
  confirmó que 8 de cada 10 minutos abierta **no es normal**; falta el número.
- **Muestra diurna.** La validación es de una grabación nocturna. Se espera que
  `R − G` sea robusto por ser una diferencia de canales y no un nivel absoluto,
  pero conviene confirmarlo.
- **Fijar la PTZ** en su posición de frente, como quedó acordado.
