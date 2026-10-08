# KV de sesiones con OTT

La primera integración conserva archivos KV nativos de llama.cpp. OTT verifica
y replica sus bytes; llama.cpp mantiene el control de su disposición en memoria
y de la atención. Los mensajes y parámetros de generación siguen en la app cliente.

## Configuración

La ejecución base de RuntimeDeck sigue usando la biblioteca estándar. Para esta
función opcional necesitas el repositorio `ott-core` y `numpy` en el mismo Python.
Puedes instalar OTT con `py -m pip install "C:\ruta\ott-core"`, o configurar su
carpeta en la interfaz e instalar solo `numpy` con `py -m pip install numpy`.
El backend OTT utilizado es CPU para almacenamiento y verificación; CuPy no es
necesario. La inferencia y la KV activa siguen en el backend de llama.cpp.

1. En **KV de sesiones**, indica la raíz de `ott-core` o deja ese campo vacío si
   el paquete está instalado. Selecciona una carpeta dedicada para las copias.
2. Activa **Habilitar sesiones KV** y configura los límites antes de iniciar el
   servidor. Se añaden `--slots` y `--slot-save-path` al comando.
3. Procesa un prompt desde tu cliente. Elige el slot usado y pulsa **Guardar slot**.
4. Selecciona una copia y pulsa **Restaurar copia** con el servidor listo y el slot
   inactivo. Puede ser una nueva ejecución del mismo modelo y runtime.

La configuración se captura al iniciar el servidor. Editar rutas o límites prepara
el siguiente arranque. El valor predeterminado es 512 MiB de KV nativa por copia y
4096 MiB para la carpeta. Las dos réplicas se almacenan en el mismo volumen OTT;
la copia ocupa aproximadamente dos veces los bytes KV más 8 MiB de metadatos.
El presupuesto incluye los archivos temporales del adaptador. La API nativa
termina de guardar el archivo temporal antes de que pueda comprobarse su tamaño.
Durante su construcción y verificación hay varias copias del bloque en RAM; el
límite por sesión limita el archivo nativo, no el consumo total de memoria.

## Uso desde la aplicación cliente

Guardar KV no guarda el historial de conversación de la aplicación. Para
reutilizarla, el cliente debe reenviar el mismo prefijo de tokens y dirigir su
petición al slot restaurado. La API nativa `/completion` permite:

```json
{
  "prompt": "El prefijo original seguido de la continuación",
  "id_slot": 0,
  "cache_prompt": true,
  "n_predict": 32,
  "temperature": 0.7
}
```

Los campos adicionales que acepta `/v1/chat/completions` y la selección de slot
dependen del cliente y de la versión de llama.cpp. Con un solo slot no hay
ambigüedad de selección, pero otro prompt puede sustituir el prefijo conservado.
Revisa `timings.cache_n` en la respuesta nativa para verificar reutilización;
`tokens_cached` puede representar el total que quedó almacenado.

Pausa las peticiones del cliente mientras guardas o restauras. El adaptador
rechaza slots que observa ocupados, y llama.cpp ejecuta sus operaciones dentro
de su propia cola. El cliente puede enviar otra petición entre la comprobación
y la operación; RuntimeDeck no intercepta su tráfico ni reserva el slot. Restaurar
sustituye la KV actual. Temperatura, semilla, límites de salida y otras opciones
de generación continúan viajando en cada petición.

## Formato y compatibilidad

Cada copia tiene un ID aleatorio, un volumen `.ott` y un catálogo `.json`.
El tensor verificado contiene tanto los metadatos como los bytes del archivo
KV nativo, incluyendo su SHA-256. Un catálogo alterado o una copia incompatible
se rechaza antes de enviar `restore` al runtime.

La identidad incluye el SHA-256 del GGUF, del ejecutable y de las DLL adyacentes,
el contexto solicitado, el contexto efectivo de los slots, el paralelismo y
Flash Attention. Es conservadora: cambios en el runtime requieren una nueva
copia. Esta versión cubre el servidor de texto que lanza RuntimeDeck, sin
adaptadores LoRA, proyectores ni modificaciones externas de su configuración.
La primera operación calcula las huellas en segundo plano; las siguientes
reutilizan esa identidad mientras los archivos conservan su firma de filesystem.

El adaptador usa una región inmutable por volumen, sin promover ni modificar HOT
en OTT. Eso evita depender del writeback y la coherencia de tensores mutables del
prototipo. La corrupción de la réplica primaria puede repararse; la corrupción
de ambas se rechaza. Los archivos nativos temporales se eliminan al terminar la
operación. Las copias `.ott` y `.json` permanecen en la carpeta configurada.

## Evidencia y alcance

```powershell
py tools\verify_kv_ott.py --ott-core "C:\ruta\ott-core" --model "F:\Modelos\ruta\modelo-compatible.gguf"
```

El verificador usa un puerto libre y preferencias temporales. Conserva cada
ejecución bajo `verification\kv-ott\<fecha-id>`, incluyendo informe, salida del
servidor, volumen de sesión y captura de la interfaz. Solo detiene el proceso
que inició. La prueba de corrupción total usa otra copia del volumen.

Validación local del 2026-10-08 con RTX 3060 y Bonsai-2-27B PQ2_0, contexto 1024:

- 268 tokens KV guardados y restaurados; archivo nativo de 166.38 MiB.
- Recuperación de una réplica primaria dañada y auditoría OTT válida.
- Rechazo de identidad incompatible y de corrupción en las dos réplicas.
- Restauración después de reiniciar el servidor, con 268 tokens de prefijo
  reutilizados y 9 tokens de prompt nuevos procesados.
- Los 8 tokens de continuación coincidieron con la referencia en esta prueba.
- Petición de chat cliente con límite de 4 tokens y valores globales de
  generación conservados; temporales limpios y proceso detenido.

Estos resultados prueban persistencia e integración de KV para ese modelo y
binario. Los tiempos del informe incluyen efectos de la caché del sistema
operativo y no son un benchmark de lecturas NVMe en frío. La compatibilidad debe
comprobarse por modelo y runtime. Borrar o restaurar un slot permite reutilizar
sus casillas; los buffers de KV reservados por llama.cpp siguen asignados.

El laboratorio histórico en `E:\villa\ahi\centro\Codex\2026-05-10\ott`
conserva evidencia de residencia por ventanas. Su motor OTTLlama asigna K y V
con `cp.zeros` para todo el contexto. El reporte de julio en
`rerun_2026-07-16\astra_hot_audit\campaign_report.json` audita ventanas de
pesos de ComfyUI; conserva 32 casos `review_required`, y estadísticas con valores
no finitos. Es antecedente experimental, no evidencia de atención paginada o
persistencia KV. La integración nueva no escribe en esa carpeta.

La siguiente etapa requerirá bloques KV sellados, gestión de una cola mutable,
metadatos incrementales y presupuestos de RAM/VRAM, con sincronización explícita
del runtime. Ampliar una generación activa más allá de la VRAM exige coordinar
transferencias y cómputo de atención; almacenar archivos por sí solo no amplía
el contexto máximo del servidor.
