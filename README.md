# RuntimeDeck

**Carga. Mide. Compara. Ajusta. Verifica.**

RuntimeDeck descubre tus modelos y runtimes, administra un servidor local y
te da una URL compatible con OpenAI para conectarlo a otras aplicaciones.
La generación se configura en cada app cliente. El adaptador incluido arranca
tus propios binarios de [llama.cpp](https://github.com/ggml-org/llama.cpp).
También conserva las herramientas de inferencia y evaluación del banco de trabajo.

> RuntimeDeck coordina tus herramientas; no incluye modelos ni runtimes.

## Requisitos

- Windows recomendado (el lanzador incluido es `run.cmd` y la limpieza completa
  de procesos utiliza Windows Job Objects).
- Python con `tkinter` disponible. No se requieren paquetes externos de Python.
- Una instalación de llama.cpp con los ejecutables que quieras usar.
- Modelos GGUF para las operaciones del adaptador llama.cpp.

Al instalar Python en Windows, asegúrate de incluir Tcl/Tk para disponer de
`tkinter`.

## Inicio rápido

1. Organiza tus archivos bajo una carpeta de espacio de trabajo. Por defecto,
   RuntimeDeck usa la **carpeta superior a la aplicación**, sin fijar una unidad:

   ```text
   workspace\
   ├── RuntimeDeck\
   │   ├── runtime_deck.py
   │   └── run.cmd
   ├── Modelos\
   │   └── familia\
   │       └── modelo.gguf
   └── Runtimes\
       └── cuda\
           ├── llama-cli.exe
           ├── llama-bench.exe
           ├── llama-server.exe
           ├── llama-perplexity.exe
           └── ... DLL del runtime ...
   ```

   Puedes cambiar la carpeta desde **Workspace**. Se aceptan `models` o `Modelos`
   y `runtimes` o `Runtimes`; se incluyen subdirectorios como `build\bin\Debug`.
   Si eliges una de esas carpetas, se detecta su padre compartido. Las preferencias
   de una ubicación que ya no existe vuelven al workspace de esta instalación.
   El escaneo omite `.git`, entornos Python y `node_modules`.

2. Inicia la aplicación con doble clic en `run.cmd` o desde PowerShell:

   ```powershell
   py .\runtime_deck.py
   ```

3. En **Servidor**, selecciona un modelo y un runtime, revisa los recursos de
   arranque y pulsa **INICIAR SERVIDOR**. Cuando aparezca **API lista**, copia
   la **URL base** y el **ID del modelo** a tu otra app. Ambos se verifican contra
   `/v1/models`. **Copiar conexión** copia los dos campos en JSON.

   La selección de modelo y runtime se conserva entre sesiones. Si cambias la
   selección o el puerto mientras el servidor sigue activo, la conexión publicada
   conserva los datos del proceso que ya está ejecutándose.

RuntimeDeck detecta modelos `.gguf`, `.safetensors`, `.bin`, `.onnx`, `.pt` y
`.pth`. El adaptador actual de llama.cpp solo ejecuta modelos GGUF; los demás
formatos se muestran en el inventario, pero necesitan otro adaptador para poder
ejecutarse.

RuntimeDeck reconoce `llama-cli.exe`, `llama-bench.exe`, `llama-server.exe`,
`llama-perplexity.exe` y `llama-quantize.exe`. Inferencia, benchmarks, servidor
y perplexity requieren el ejecutable correspondiente; `llama-quantize.exe` se
registra en el inventario, pero la interfaz aún no ofrece un flujo de cuantización.

Las carpetas de otros motores o repositorios sin binarios aparecen como
**sin ejecutable compatible**. Detectar una carpeta no instala ni implementa
su adaptador. Un archivo GGUF también debe usar una arquitectura y cuantización
que admita el binario seleccionado; si el runtime lo rechaza, consulta su salida.

## Funciones

### Inferencia y comandos

La pestaña **Inference** permite ajustar contexto, capas GPU, hilos, batch,
ubatch, tokens de salida, temperatura, top-p, top-k, semilla, Flash Attention
y mmap. Puedes revisar el comando antes de lanzarlo; RuntimeDeck construye una
lista de argumentos y ejecuta el binario directamente, sin `shell=True`.
La salida separa la respuesta del texto de razonamiento que emita el modelo.

La pestaña **Comandos** conserva los comandos inspeccionados y permite revisar
o reconstruir configuraciones. La consola inferior muestra salida y diagnóstico;
**Stop** cancela el proceso activo.

### Benchmark y experimentos

**Benchmark** ejecuta `llama-bench` con tamaños de prompt y generación,
repeticiones, capas GPU, hilos y parámetros de batch. **Experiments** permite:

- Buscar combinaciones de capas GPU, hilos y batch y elegir una métrica objetivo.
- Comparar tamaños de prompt y ejecutar pruebas de estabilidad repetidas.
- Consultar el historial, comparar resultados y exportar CSV.
- Inspeccionar métricas, parámetros reportados por el runtime, diagnóstico,
  salida original y muestras de telemetría.

Las tareas guardan una instantánea del modelo, runtime, comando y configuración
al iniciarse. Cambiar la selección después no altera los resultados de esa tarea.
Los candidatos fallidos, cancelados o con discrepancias de parámetros no se
consideran para elegir el mejor resultado.

### Servidor para otras aplicaciones

**Servidor** es la pestaña inicial e inicia `llama-server` con el modelo seleccionado. El host y puerto
predeterminados son `127.0.0.1:8080`. El proceso queda administrado por
RuntimeDeck y se puede detener desde la aplicación.

La URL base tiene la forma `http://127.0.0.1:<puerto>/v1`; úsala tal como se
muestra, sin añadir `/chat/completions` al campo URL base de tu cliente.
El ID se obtiene del servidor, con el nombre del archivo como alias de este
adaptador. Para otras APIs compatibles que ya estén escuchando en el host y
puerto elegidos, **Verificar API** consulta sus modelos publicados.
Si tu cliente requiere una clave aunque el servidor local no tenga autenticación,
puedes usar un texto como `local`.

| Se ajusta en la app cliente, por petición | Se ajusta en RuntimeDeck, al arrancar |
| --- | --- |
| Mensajes, instrucciones de sistema y conversación | Modelo cargado y ejecutable |
| Temperatura, top-p, top-k y semilla | Contexto máximo, capas GPU y hilos |
| Tokens de salida, streaming, stop y penalizaciones | Batch, µBatch, Flash Attention y mmap |
| Herramientas o formato de respuesta si la API/modelo los admite | Host, puerto y peticiones simultáneas |

RuntimeDeck no interpone un proxy ni sobrescribe las peticiones de otra app.
Los ajustes de generación de **Inference** o **Chat** solo afectan a esas
herramientas locales y no se pasan al comando del servidor. Si un cliente omite
un parámetro, el runtime usa su valor predeterminado. El contexto máximo depende
de los recursos asignados y las peticiones simultáneas; no puede ampliarse con
una petición. Mantén RuntimeDeck abierto mientras uses su servidor.

El contrato HTTP de la conexión es compatible con OpenAI; el lanzamiento de
modelos en esta versión sigue usando el adaptador llama.cpp. Las opciones extra
como top-k dependen de la API y de que tu cliente permita enviarlas.

### Chat local opcional

En **Chat** puedes conversar mediante el servidor local, configurar una
instrucción de sistema, activar el modo de razonamiento si el modelo lo admite,
detener una respuesta, iniciar otra conversación y guardar el transcript.

### Calidad

La sección **Quality** de Experiments incluye dos evaluaciones distintas:

- **Perplexity** ejecuta `llama-perplexity` sobre un corpus de texto UTF-8.
  Para que la comparación sea útil, usa el mismo corpus, tokenizer y contexto;
  un PPL menor solo indica mejor resultado en ese corpus, no calidad general.
- **Evaluación de respuestas** ejecuta casos JSONL mediante el chat local.
  Cada línea debe incluir `prompt`, `check` (`exact`, `contains`, `regex` o
  `json`) y `expected` (salvo que la comprobación JSON solo verifique que la
  respuesta sea JSON válido).

Ejemplo de `casos.jsonl`:

```jsonl
{"name":"suma","prompt":"Responde únicamente el resultado de 12 + 7.","check":"exact","expected":"19"}
{"name":"estructura","prompt":"Devuelve un objeto JSON con ok igual a true.","check":"json","expected":{"ok":true}}
```

La evaluación admite de 1 a 1000 casos y archivos de hasta 16 MiB. RuntimeDeck
guarda una copia de cada corpus con su SHA-256 para identificar exactamente los
datos evaluados. Los resultados describen únicamente el dataset proporcionado;
los ejemplos incluidos verifican el flujo de evaluación, no la calidad general
del modelo.

### Planes y monitor

**Plan y objetivo** permite definir una pregunta de uso, criterios de aceptación
y un dataset. Ejecuta etapas de referencia, ajuste opcional, escalado opcional,
estabilidad y evaluación de respuestas; conserva borradores, resultados y un
dictamen basado en la evidencia disponible. Una medición ausente queda pendiente,
no se considera aprobada.

**Monitor** y la franja superior muestran temperatura, memoria, utilización y
potencia de las GPU disponibles, cuando el equipo y sus sensores lo permiten.
También puedes configurar un límite térmico y el tiempo máximo de las tareas.
El muestreo no es continuo: si no hay sensor disponible, RuntimeDeck no puede
aplicar el límite térmico. La memoria observada incluye el uso de otros
programas en la GPU.

## Archivos y datos

- Preferencias: `%APPDATA%\RuntimeDeck\settings.json`.
- Benchmarks, experimentos y corpus congelados: `<workspace>\benchmarks\`.
- Los modelos y runtimes originales no se modifican.
- Si eliges la raíz del repositorio como workspace, `.gitignore` excluye
  `/benchmarks/` para evitar añadir resultados generados al control de versiones.

En Windows, los procesos iniciados se asignan a un Job Object para terminar sus
descendientes cuando finaliza el proceso principal o se cierra la aplicación.
En otros sistemas, RuntimeDeck solo puede detener el proceso directo.

## Pruebas

Las pruebas unitarias usan la biblioteca estándar:

```powershell
py -m unittest discover -s tests -v
```

Hay además una verificación de integración opcional contra los modelos y runtimes
instalados. Consume recursos de inferencia y escribe resultados bajo
`<workspace>\benchmarks\`; el informe y las capturas se guardan en `verification\`.
Por ejemplo, para probar inferencia, ajuste, suites, perplexity, chat y respuestas:

```powershell
py tools\verify_local.py --inference --tune --suites --quality --chat --answers
```

Puedes fijar un modelo concreto con `--model "F:\Modelos\ruta\modelo.gguf"`.
Para consultar las opciones disponibles:

```powershell
py tools\verify_local.py --help
```

Para verificar específicamente el servidor y su uso desde clientes HTTP:

```powershell
py tools\verify_server.py --model "F:\Modelos\ruta\modelo-compatible.gguf"
```

Esta comprobación usa un puerto libre, contexto de 1024 tokens y preferencias
temporales. Verifica el inventario visible, `/v1/models`, dos peticiones de chat
con distintos límites y temperaturas, streaming, parámetros efectivos reportados
por llama.cpp, conservación de los valores globales y cierre del proceso.
Guarda el informe, la salida y una captura en `verification\server\`, sin cambiar
tus preferencias. El modelo debe ser compatible con el runtime instalado.

## Estructura del proyecto

```text
runtime_deck.py          Punto de entrada
runtime_deck_core/       Interfaz, controladores, descubrimiento y lógica
tests/                   Pruebas unitarias
tools/                   Verificación de integración local
examples/                Dataset JSONL de ejemplo
verification/            Informes y capturas de verificación
run.cmd                  Lanzador para Windows
```

RuntimeDeck es el banco de control, no el runtime:

```text
Modelos + runtimes + parámetros
              │
              ▼
    RuntimeDeck y sus planes
       ┌──────┴──────┐
       ▼             ▼
   Inferencia    Experimentos
                      │
                      ▼
             resultados y evidencia
```
