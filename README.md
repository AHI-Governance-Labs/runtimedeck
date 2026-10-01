# RuntimeDeck

**Carga. Mide. Compara. Ajusta. Verifica.**

RuntimeDeck es un banco de trabajo de escritorio para ejecutar y evaluar modelos
locales con tus propios binarios de [llama.cpp](https://github.com/ggml-org/llama.cpp).
Permite descubrir modelos y runtimes, lanzar inferencias y benchmarks, conversar
con un servidor local y conservar evidencia reproducible de los experimentos.

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
   RuntimeDeck busca en `G:\Runtimes`:

   ```text
   G:\Runtimes\
   ├── models\
   │   └── familia\
   │       └── modelo.gguf
   └── runtimes\
       └── cuda\
           ├── llama-cli.exe
           ├── llama-bench.exe
           ├── llama-server.exe
           ├── llama-perplexity.exe
           └── ... DLL del runtime ...
   ```

   Puedes cambiar la carpeta desde **Workspace**. Los modelos se buscan bajo
   `models\` y los ejecutables bajo `runtimes\`, incluyendo sus subdirectorios.
   Cada carpeta que contiene ejecutables reconocidos se registra como un runtime.

2. Inicia la aplicación con doble clic en `run.cmd` o desde PowerShell:

   ```powershell
   py .\runtime_deck.py
   ```

3. Selecciona un modelo y un runtime del inventario, configura los parámetros
   y ejecuta la operación desde su pestaña.

RuntimeDeck detecta modelos `.gguf`, `.safetensors`, `.bin`, `.onnx`, `.pt` y
`.pth`. El adaptador actual de llama.cpp solo ejecuta modelos GGUF; los demás
formatos se muestran en el inventario, pero necesitan otro adaptador para poder
ejecutarse.

RuntimeDeck reconoce `llama-cli.exe`, `llama-bench.exe`, `llama-server.exe`,
`llama-perplexity.exe` y `llama-quantize.exe`. Inferencia, benchmarks, servidor
y perplexity requieren el ejecutable correspondiente; `llama-quantize.exe` se
registra en el inventario, pero la interfaz aún no ofrece un flujo de cuantización.

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

### Servidor y chat local

**Server** inicia `llama-server` con el modelo seleccionado. El host y puerto
predeterminados son `127.0.0.1:8080`. El proceso queda administrado por
RuntimeDeck y se puede detener desde la aplicación.

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

Puedes fijar un modelo concreto con `--model "G:\Runtimes\models\ruta\modelo.gguf"`.
Para consultar las opciones disponibles:

```powershell
py tools\verify_local.py --help
```

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
