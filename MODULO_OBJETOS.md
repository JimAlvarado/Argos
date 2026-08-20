# Módulo: Detección de Objetos

Cuenta piezas que cruzan una línea en una banda transportadora. **No necesita
modelo entrenado**: usa visión clásica, la misma que alimenta al capturador de
dataset.

## Cómo abrirlo

Desde el dashboard, tarjeta **01 · Detección de Objetos** → *Abrir detector*.
O por su cuenta: `python detector_objetos.py`

## Resultado verificado

Sobre 60 segundos de video real de la lingotera:

| | |
|---|---|
| Conteo del módulo | **15** |
| Conteo manual del operador | **15** |
| Exactitud | **100%** |

Los 15 conteos quedaron registrados en la base, así que aparecen en el dashboard.

## Configuración

| Ajuste | Para qué |
|---|---|
| Fuente | Cámara local, cámara IP/RTSP o archivo de video |
| Región de interés | La zona de la banda. Por defecto `1600,1140,560,1020` |
| Línea de conteo | Posición relativa. **Calibrada en 0.53** |
| Guardar evidencias | Imagen de cada pieza contada |
| Modelo de detección | Modelo `.pt` opcional que cuenta **en paralelo** como verificación |

### Sobre la línea de conteo

Es el parámetro más sensible. Barrido contra el conteo manual:

| Posición | Conteo | |
|---|---|---|
| 0.35 | 8 | demasiado arriba: la pieza aún no entró completa |
| 0.51 | 15 | correcto |
| **0.53** | **15** | **valor por defecto** |
| 0.75 | 13 | demasiado abajo: la pieza ya está saliendo |

Si mueves la cámara o cambias de banda, hay que recalibrarla.

## Las tres reglas que hacen fiable el conteo

**Antiparpadeo.** Sin tolerancia a huecos, la máscara parte una pieza en dos y
el conteo se infla al 193% (medido: 29 en vez de 15). Se toleran hasta 20
cuadros sin ver el objeto.

**Pieza completa.** Solo cuenta lo que entró entero al cuadro. Una pieza cortada
por el borde no cuenta. Es la regla acordada con operación.

**Sentido único.** Se cuenta al cruzar la línea hacia abajo, así cada pieza se
cuenta una vez y siempre en el mismo punto.

## Por qué el vapor no lo engaña

El material se reconoce por tres condiciones a la vez: destaca sobre el fondo,
es claro, y tiene **forma alargada**. El vapor cumple las dos primeras pero no
la tercera: es difuso. En 60 segundos con vapor denso no produjo un solo
falso positivo.

## Una sola implementación

`core/pipeline/classic.py` es el único detector. Lo usan el módulo y el
capturador de dataset. Si viviera duplicado, el dataset dejaría de corresponder
con lo que ve producción.

## Siguiente paso

Cuando el modelo entrenado esté listo se sumará como **segunda fuente**, no como
reemplazo. Dos métodos que fallan por razones distintas son la base del conteo
confiable: cuando coinciden, el dato es defendible; cuando discrepan, se guarda
la evidencia para revisión.

## Correccion 0.8.1 — el modulo no abria

**Sintoma:** la tarjeta del dashboard no abria nada, y el reporte tecnico decia
"sin fallas registradas".

**Causa:** `MetricCard` exige cuatro argumentos (master, titulo, valor, color) y
el modulo pasaba tres. La ventana lanzaba `TypeError` al construirse.

**Por que el reporte salio limpio:** el registro si atrapo la falla y la
escribio en `data\logs\`. El reporte se genero antes de intentar abrir el
modulo, por eso no aparecia. Si vuelve a pasar algo asi, genera el diagnostico
**despues** del intento.

**Por que ninguna prueba lo detecto:** las 114 pruebas ejercitaban la logica de
conteo, nunca la construccion de la interfaz. Un conteo perfecto en una ventana
que no abre no sirve de nada.

**Correcciones:**

- Llamada a `MetricCard` con los cuatro argumentos.
- Se usa `MetricCard.set()` en vez de tocar `value_label` por dentro.
- La vista de video usa `CTkImage` en vez de `ImageTk`: con escalado de pantalla
  (monitores 4K) la imagen salia borrosa.

**Prueba nueva que lo habria evitado:** compara por analisis del codigo cada
llamada a `MetricCard` contra la firma real del widget. Se verifico
reintroduciendo el error a proposito: la prueba falla e indica la linea exacta.

La ventana se probo ademas ejecutandola de verdad sobre un display virtual, no
solo importando el modulo.

## Correccion 0.8.2 — la tarjeta no abria el modulo

**Sintoma:** al pasar el puntero la tarjeta no reaccionaba como la de Personas,
y al pulsarla no ocurria nada.

**Causa: tres defectos en el marcado de la tarjeta.**

| Defecto | Consecuencia |
|---|---|
| Sin `data-start="objetos"` en el boton | El JS enlaza el clic con `$$("[data-start]")`. Sin ese atributo, **el boton no hace nada** |
| Sin la clase `available` | El efecto al pasar el puntero depende de `.module-card.available:hover` |
| Sin color de acento para `.objects` | La tarjeta perdia el resplandor de color propio |

El lanzamiento del lado del servidor funcionaba desde el principio: se verifico
que `POST /api/modules/objetos/start` arranca el proceso y el supervisor lo
registra. El problema era **solo** que el boton nunca llamaba a ese endpoint.

**Verificacion:** se simulo el clic real con el JS ejecutandose sobre el HTML.
Antes de la correccion la tarjeta de Personas lanzaba su peticion y la de
Objetos no hacia nada; despues, ambas se comportan igual.

**Pruebas nuevas:** verifican que **toda** tarjeta declarada disponible en
`MODULES` tenga `data-start`, la clase `available` y color de acento. Se
comprobo reintroduciendo cada defecto por separado: cada uno hace fallar su
prueba. Ya no depende de que alguien recuerde copiar bien la estructura.

## 0.8.3 — Camaras y botones

**Panel de fuente completo**, identico al del detector de personas: camara
local, camara IP/RTSP con marca, direccion IP, usuario y contrasena, o archivo
de video. Los campos cambian solos segun el tipo elegido.

Vive en `ui/source.py` y es **compartido**. Una camara configurada en un modulo
funciona igual en el otro, porque ambos usan el mismo componente. Si cada
detector armara sus campos por su cuenta, con el tiempo dejarian de comportarse
igual.

La contrasena se codifica en la URL (funciona con `/`, espacios o acentos) pero
**nunca se escribe en `config.json`**: se teclea en cada arranque.

**Botones de accion** a 38 px de alto con los mismos colores que Personas.

## La region de interes, explicada

Es el recorte del cuadro donde el modulo busca material. **No** cambia lo que
graba la camara: solo delimita donde mirar.

Se define con cuatro numeros: `x, y, ancho, alto` en pixeles sobre el cuadro
original. Por defecto `1600, 1140, 560, 1020`, es decir un rectangulo de
560x1020 px cuya esquina superior izquierda esta a 1600 px del borde izquierdo
y 1140 px del superior.

### Por que existe

Sin recorte, sobre el cuadro 4K completo el lingote quedaria en **8 pixeles de
alto** al escalar a 640 para procesar: indetectable. Recortado, mide 31 px, que
es el umbral util. Ademas el vapor queda fuera del recorte, y el costo en CPU
baja.

| Vista | Tamano del lingote al procesar |
|---|---|
| Cuadro completo 4K | 34 x 8 px — no se detecta |
| Region de interes | 128 x 31 px — se detecta bien |

### Como ajustarla si cambia la camara

La camara puede reposicionarse desde su configuracion; cuando eso pasa la region
deja de caer sobre la banda. Se corrige cambiando los cuatro numeros:

- **Mover a la derecha**: sube `x`. A la izquierda: bajalo.
- **Mover hacia abajo**: sube `y`. Hacia arriba: bajalo.
- **Abarcar mas banda**: sube `ancho` o `alto`.

Referencia medida: entre el 6 y el 11 de agosto la camara se movio 56 px, y la
correccion fue pasar `x` de 1600 a 1544.

La linea de conteo se define aparte, como fraccion de la altura del recorte:
0.53 significa a poco mas de la mitad. Si mueves la region, revisa que la linea
siga quedando donde la pieza se ve completa.

## 0.8.4 — Correccion RTSP y boton de prueba

**Fallo:** al iniciar con una camara Provision aparecia
`Expected 'filename' to be a str or path-like object`.

**Causa:** Provision publica el flujo en tres perfiles (`profile1`, `profile2`,
`profile3`), asi que la fuente es una **lista** de rutas candidatas. El modulo
se la pasaba entera a OpenCV, que espera una sola.

**Correccion:** se prueban una por una, con 4 segundos de espera cada una, igual
que hace el detector de personas. Mientras busca, el estado muestra
"Buscando flujo 1/3...". Si ninguna abre, el mensaje indica que revise IP,
usuario y contrasena, y muestra la URL **con las credenciales ocultas**.

### Tercer boton: PROBAR CÁMARA

Muestra la camara en vivo con la region y la linea dibujadas, **sin contar ni
guardar nada**. Sirve para verificar el encuadre y ajustar la region de interes
sin ensuciar los conteos del dia.

El estado en la cabecera lo distingue: "VISTA DE CÁMARA" en azul contra
"CONTANDO" en verde.

### Botones

Los tres miden 38 px y viven **dentro del panel de video**, no a lo ancho de la
ventana, donde quedaban desproporcionados.

## 0.8.5 — Video a ritmo real, botones fijos y fuente de conteo única

### Corrección: el video de prueba no se podía detener

**Síntoma:** al probar con un archivo de video, no daba tiempo de usar DETENER.

**Causa:** un archivo entrega cuadros tan rápido como se lean, sin el ritmo que
impone una cámara. El módulo consumía un video de 30 segundos en menos de 2:
terminaba solo y deshabilitaba DETENER antes de que el operador alcanzara a
tocarlo (medido con la ventana real).

**Corrección:** los archivos se reproducen a su ritmo real (según los fps del
propio video) y la espera entre cuadros despierta al instante si se pide
detener. De paso, el fin del archivo ya no depende de la extensión: un `.mov`
también termina solo (antes quedaba esperando para siempre).

**Pruebas nuevas:** el video respeta su duración, DETENER responde en menos de
2 segundos, y un `.mov` termina solo. Verificadas reintroduciendo el bug: la
prueba de ritmo falla e indica el tiempo medido.

### Corrección definitiva: los botones desaparecían al abrir la imagen

**Síntoma:** al pulsar PROBAR CÁMARA o INICIAR CONTEO aparecía la imagen y los
tres botones (y el registro de conteos) desaparecían de la ventana.

**Causa:** orden de empaquetado. El lienzo de video se empaquetaba primero y
los botones al final; en `pack`, el último empaquetado es el primero en
quedarse sin espacio. La imagen se escala a la altura actual del lienzo, el
lienzo crece con ella y en cada repintado pedía más: los botones terminaban
empujados fuera de la ventana.

**Corrección:** botones y registro se empaquetan primero y **anclados abajo**;
el lienzo va al final y recibe solo el espacio sobrante, así la imagen no puede
expulsarlos jamás. Además la imagen ahora respeta el espacio en ambos ejes
(antes solo el alto: un video ancho desbordaba hacia los lados).

**Verificación:** con la ventana real pintando video, los botones siguen
mapeados y dentro de la ventana en PROBAR y en INICIAR. La prueba nueva revisa
el orden y el anclaje del empaquetado; reintroduciendo el orden viejo, falla.

### Región de interés con deslizadores

Los cuatro campos numéricos se reemplazaron por **deslizadores** (pedido de
operación): Izquierda (x), Arriba (y), Ancho y Alto, con el valor en píxeles a
la vista. No hay valores inválidos posibles y el paso es de 4 px — la
corrección real medida fue de 56 px, así que sobra precisión para recuadrar.

### Panel de configuración rediseñado

- **El botón CLÁSICA / MODELO va arriba**, justo bajo la palabra
  CONFIGURACIÓN: es la decisión más importante del módulo. Lo elegido se
  guarda en `objetos_fuente` y aplica al iniciar.
- Las opciones viven en **submenús desplegables**: Fuente de video, Región de
  interés, Línea de conteo y Modelo de detección. Al elegir MODELO, el submenú
  del modelo se abre solo.
- **Guardar evidencias va siempre hasta abajo** del panel.

### Región de interés en vivo

- La región **se puede ajustar con el conteo activo**: los deslizadores viajan
  al ciclo sin reiniciarlo. Se conserva el total contado y solo se reaprende
  el fondo (~1 s); las piezas en tránsito durante el ajuste pueden no contarse.
- Una región fuera del cuadro **ya no detiene el módulo**: se recorta al
  cuadro real (antes lanzaba "La región no cabe" y tumbaba el ciclo).
- Hubo brevemente una vista completa de cámara con clic sobre el video; se
  retiró a pedido de operación el mismo 14-ago.

### Fuente de conteo única: el modelo O la visión clásica

**Regla de operación (14-ago-2026): una sola fuente cuenta a la vez.** El
botón CLÁSICA/MODELO decide; en el submenú **Modelo de detección** se elige el
`.pt` de `modelos\` o se agrega uno con **Agregar modelo .pt…** (se valida y
se copia a `modelos\`; la selección persiste en `objetos_modelo`).

- **Con modelo elegido, cuenta el modelo.** Sus cajas se dibujan en violeta y
  la tarjeta **FUENTE DE CONTEO** dice MODELO. La visión clásica no participa.
- **Sin modelo, cuenta la visión clásica** (cajas verdes, tarjeta CLÁSICA).
- Cada conteo queda auditado en la base con su fuente en `model_name`: el
  nombre del `.pt` o `vision-clasica`.
- Si el modelo falta o no carga, se avisa y **la clásica retoma el conteo**:
  la banda nunca se queda sin contar por un archivo dañado.
- En CPU la inferencia no frena el ciclo: si tarda más de 80 ms se espacia
  (hasta 1 de cada 5 cuadros); el antiparpadeo tolera esos huecos.
- En PROBAR CÁMARA ninguna fuente cuenta, solo se dibuja.

**Pruebas nuevas:** con modelo, la clásica no cuenta (un video que la clásica
sí sabe contar queda en 0 cuando el modelo está activo); el conteo del modelo
queda auditado en la base; en solo vista nadie cuenta.

### Reporte "la clásica no cuenta cruces" — diagnóstico medido (14-ago)

Se corrió el pipeline clásico contra las muestras reales de 60 s con la
región de producción (`1552,1140,560,968`):

| Video | Línea 0.53 (calibrada) | Línea 0.608 (config del día) |
|---|---|---|
| Noche | **16 cruces** | **8 cruces** — pierde la mitad |
| Día | **14 cruces** | 14 cruces |

La clásica **sí detecta y cuenta en ambas escenas**. Lo que pasó en planta:
(1) la versión previa tronaba con "La región no cabe" cuando los deslizadores
generaban una región inválida (corregido: ahora se recorta al cuadro), y
(2) la línea estaba en 0.608, que de noche cuenta la mitad. La línea volvió
a **0.53**, el valor calibrado contra conteo manual.

### Overlay rediseñado (referencia: el detector de personas)

- Detecciones marcadas **solo con las cuatro esquinas, en azul** (pedido de
  operación, 14-ago) y **rótulo "lingote"** sobre banda azul; con modelo
  activo el rótulo incluye la confianza ("lingote 0.87").
- Línea de conteo **delgada con remates** (punto a la izquierda, aro a la
  derecha), **sin texto**: el total vive en la tarjeta PIEZAS CONTADAS.
- Verificado renderizando el overlay sobre el video real de día: lingotes
  con esquinas azules y rótulo, contando 14 cruces en la muestra de 60 s.

### Candados contra el doble conteo (reporte de planta, 14-ago)

**Síntoma:** algunas piezas se contaban dos veces, sobre todo con el modelo.

**Los dos mecanismos, reproducidos en prueba:**

1. **Re-detección:** la pieza cruza y se cuenta; una caja parcial (solo la
   punta detectada) desplaza el centro hacia arriba más allá de la tolerancia
   de asociación, nace una pista nueva pegada a la línea y al volver la caja
   completa el centro re-cruza. **Candado:** una pista nacida a menos de
   media pieza de la línea (~3% del recorte) que cruza dentro de las 12
   actualizaciones siguientes a un conteo no suma — es la pieza recién
   contada. La condición temporal importa: en el video de día hay piezas
   reales detectadas tarde junto a la línea SIN conteo previo, y esas sí
   cuentan (verificado: día sigue en 14).
2. **Pieza partida:** el modelo o la máscara parten una pieza en dos cajas
   que viajan pegadas y cruzan casi juntas. **Candado:** dos conteos a 3
   actualizaciones o menos son la misma pieza — la cadencia real mínima
   medida entre piezas es de 64 cuadros.

**Identidad:** el id de pista es interno; en el video solo se ve el punto de
rastreo (ámbar = contada, gris = en tránsito) y el id aparece en cada renglón
de ÚLTIMOS CONTEOS ("pieza #22 · 01:52 PM") para auditar conteos puntuales.

### La oclusión del tubo: el conteo perdido (14-ago, tarde)

**Síntoma:** en el encuadre actual un tubo cruza la vista justo sobre la
línea. La pieza se detectaba bajando, el tubo la tapaba más de las 20
ausencias toleradas, la pista moría, y la pieza reaparecía DEBAJO de la línea
como pista nueva que ya no podía cruzar: **el cruce jamás se registraba**
(bitácora: pistas naciendo en cy≈260-276 bajo la línea 237).

**Correcciones, todas medidas contra los videos reales:**

1. **Espera extendida:** una pista completa que aún no cruza tolera el triple
   de ausencias (60) antes de morir — si desaparece junto a la línea es el
   tubo, no una pieza que se fue.
2. **Corredor de reasociación:** hacia adelante crece con la velocidad
   observada × el tiempo ausente (tope 4×), lateral se queda en el carril
   (60 px). Un radio inflado en todas direcciones robaba detecciones de la
   charola y contó 16 en vez de 15.
3. **Ventana de re-detección en 30** (antes 12): el re-cruce más tardío
   medido fue 18 actualizaciones después del conteo; el hueco real mínimo
   entre piezas es 68. **Margen de origen al 10%** del recorte: la
   re-detección más lejana nació a 13 px de la línea y el margen del 3% la
   dejó pasar por medio píxel.

**Resultado final medido (muestras de 60 s, región del operador):**

| Modo | Conteo | Nota |
|---|---|---|
| Clásica día | 14 | igual que antes de los candados |
| Clásica noche | 16 | igual |
| Modelo día | **15** | 14 + una pieza que la clásica perdió; el duplicado de la charola queda SUPRIMIDO en bitácora |

### Modelo lingotes_v2 (15-ago) — entrenado con el colado real

**Origen:** el colado del 14-ago (17:27–20:40) contó 2114 contra 2352 físicas
(−10%). Corrió con visión CLÁSICA; las pérdidas estaban dispersas (~100/hora)
más 70 minutos de pausas de producción reales (no fallas).

**Dataset:** 2487 imágenes de las evidencias del colado (más las previas),
etiquetadas por fondo de mediana rodante + modelo v1 + corredor medido +
refinamiento de caja, con **3 rondas de auditoría visual** (faltantes
39→15→8 por ronda). Política de zonas: solo se etiqueta del labio de salida
hacia abajo; mesa y piezas en reposo en charola son fondo intencional.

**Entrenamiento:** yolov8n desde el prototipo v1, 60 épocas (6.75 h CPU),
imgsz 512. Métricas: P 0.867 · R 0.847 · mAP50 0.908. Inferencia 42 ms.

**Validación funcional (muestras de 60 s):**

| | v1 | v2 | clásica |
|---|---|---|---|
| Noche | 4 (ciego de noche) | **18** — cadencia uniforme 67-94 ticks, incluye 2 piezas que la clásica salta | 16 |
| Día | 15 | **15** = verdad establecida | 14 |
| Punto ciego (45 imgs difíciles del colado) | 0/45 | **42/45** | — |

**Candados nuevos que exigió v2** (más detección = más disciplina):

- Las cajas solapadas del modelo se **fusionan antes de rastrear** (dos cajas
  sobre la misma pieza creaban una pista gemela que duplicaba).
- Ventana de gemelas de 3 → **22 actualizaciones**: los duplicados del modelo
  cruzan hasta 16 ticks después de la pieza real; la pieza real más próxima
  llega a las 35 (ciclo mínimo 2.8 s).
- Un cruce logrado con un **salto >60 px** en una pista nacida junto a la
  línea y vista menos de 8 veces no cuenta (ruido); la pieza ocluida legítima
  nace arriba y sí cuenta.
- El fondo de la clásica **no se alimenta con la banda detenida**: en las
  pausas del colado la mediana se aprendía las piezas quietas y al reanudar
  eran invisibles.

El archivo es `modelos\lingotes_v2_20260815.pt` y quedó seleccionado en la
configuración. La clásica queda como estaba (sus umbrales tienen margen
enorme, medido: brillo p1=139 vs umbral 110) más el congelador de pausas.

### Calibración contra el colado completo (15-ago) — el número final

Con el video íntegro del colado del 14-ago (2.6 h de producción activa en dos
archivos; los 35 min sin video coinciden exactos con las pausas de producción
medidas en la base), se reprodujo el conteo de lingotes_v2 a cada umbral de
confianza sobre los 234,932 cuadros:

| Confianza | Conteo total | vs 2352 físicas |
|---|---|---|
| 0.25 | 2459 | +4.5% |
| 0.30 | 2407 | +2.3% |
| **0.40** | **2359** | **+0.3%** |
| 0.50 | 2281 | −3.0% |
| 0.60 (la usada ese día) | 2166 | −7.9% |
| 0.70 | 2031 | −13.6% |

- La reproducción a 0.60 dio **exactamente 1677** en la parte 1: el mismo
  número de la corrida en vivo del operador — el análisis replica al módulo
  al cruce exacto.
- **La confianza queda calibrada en 0.40** (±1%: quedan ~22 huecos entre
  conteos de 0.9-1.4 s que pueden ser duplicados finos compensando pérdidas;
  resolverlos requiere revisar esos momentos contra el video).
- De noche el umbral alto castiga el doble (piezas más opacas): a 0.60 la
  parte nocturna perdió 19% contra 7% de la diurna.
- La clásica sobre el mismo video: 2147 (−8.7%), consistente con los 2110 de
  la corrida en vivo — su déficit disperso es estructural, no del día.

### Confianza del modelo ajustable

El submenú **Modelo de detección** tiene el deslizador **Confianza** (5–95%),
igual que el detector de personas. Se guarda en `objetos_confianza`, se aplica
a la inferencia (`conf=`) y **puede moverse en vivo** con el conteo activo.
Por defecto 30%: el punto útil medido en CPU (35 de 53 muestras al 30% contra
23 al 40%).
