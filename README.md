# Pokechatbot-MCP-Host

Un **host de consola** para conectar modelos (Anthropic/Claude) con un **servidor MCP (Model Context Protocol)** por **STDIO** y habilitar **herramientas** como _suggest_team_, _pool_filter_, etc. Diseñado para el proyecto de VGC Pokémon (MCP-PokeVGC-Teambuilder), con una salida limpia, nombre personalizable del bot **“🤖 Prof. Oak”** y modo _debug_ opcional.

## ✨ Características

- **Conexión MCP por STDIO** con _framing_ **Content-Length** (robusto en Windows, Linux y macOS).
- **Descubrimiento de herramientas** vía `tools/list` y **ejecución** vía `tools/call`.
- **Integración con Anthropic** (Claude) con soporte de **tool use**.
- **Nombre del bot configurable** (`BOT_NAME`) y **modo debug** (`HOST_DEBUG`) para ver trazas técnicas cuando se requiera.
- Comandos de utilidad (en modo debug): `help`, `tools`, `history`, `logs`, `quit`.

> Scope: este README cubre solo el host (`Pokechatbot‑MCP‑Host`). El servidor MCP (por ejemplo, `MCP-PokeVGC-Teambuilder`) se configura externamente y se lanza como subproceso.

## 🧱 Arquitectura (vista rápida)

```
┌────────────────────────┐
│  Pokechatbot-MCP-Host  │   ← CLI (este repo)
│  (src/host/cli.py)     │
└───────────┬────────────┘
            │ STDIO (Content-Length)
            ▼
┌────────────────────────┐
│   Servidor MCP (externo) │  ← p. ej. MCP-PokeVGC-Teambuilder
│   tools: suggest_team,   │
│          pool_filter, …  │
└────────────────────────┘
            ▲
            │ HTTP (API Anthropic)
            ▼
┌────────────────────────┐
│     Anthropic/Claude    │
└────────────────────────┘
```

Flujo a alto nivel:
1. El host lee configuración de `.env` y levanta el **servidor MCP** por STDIO.
2. Negocia MCP: `initialize` → `initialized` → `tools/list`.
3. Al chatear, envía el historial + catálogo de **tools** a Claude.
4. Si Claude decide usar una herramienta, el host llama **`tools/call`** al servidor MCP y devuelve el resultado al usuario.

## ⚙️ Configuración

Crea un archivo **`.env`** en la raíz del proyecto con, al menos:

```dotenv
# Clave de Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Cómo lanzar el servidor MCP (ejemplos de Windows)
CUSTOM_MCP_SERVER_CMD=C:/ruta/a/tu/venv/Scripts/python.exe
CUSTOM_MCP_SERVER_ARGS=-u -m server.main
CUSTOM_MCP_CWD=C:/ruta/a/MCP-PokeVGC-Teambuilder
```

**Variables soportadas**

- `ANTHROPIC_API_KEY` — Tu clave de Anthropic.
- `CUSTOM_MCP_SERVER_CMD` — Ejecutable que inicia el servidor MCP (p. ej., el `python` del venv del servidor).
- `CUSTOM_MCP_SERVER_ARGS` — Argumentos para iniciar el servidor (p. ej., `-u -m server.main`).
- `CUSTOM_MCP_CWD` — Directorio de trabajo donde vive el servidor MCP.
- `BOT_NAME` — Nombre que verás en la consola al responder (por defecto: `🤖 Prof. Oak`).
- `HOST_DEBUG` — `1` para modo depuración (muestra herramientas, logs y JSONs útiles), `0` para modo limpio.

## 🚀 Instalación

1. Clona el repositorio:

```bash
git clone <repository-url>
cd Pokechatbot-MCP-Host
```

2. Crea y activa un entorno virtual:

```bash
python -m venv .venv

# En Linux/macOS
source .venv/bin/activate

# En Windows (PowerShell)
.venv\Scripts\activate
```

3. Instala dependencias:

```bash
pip install -r requirements.txt
```

## ▶️ Ejecución
En raiz del proyecto
```bash
python -m src.host.cli
```

Salida esperada (modo **no debug**, estilizada):

```
🚀 Poke VGC — MCP Host
============================================================

⚙️  Configurando servidores MCP...

✓ Servidor MCP 'PokeChatbot VGC' agregado: Servidor MCP para construcción de equipos Pokémon VGC

🔌 Conectando a servidores MCP...

✅ Conectado a Servidor MCP para construcción de equipos Pokémon VGC (5 herramientas)

📊 Resumen: 1/1 servidores conectados

📡 Servidor: PokeChatbot VGC

✅ ¡Host listo!

💭 Escribe tu mensaje para empezar...

👤 Entrenador: (Ingresa lo que quieras preguntar)

🤖 Prof. Oak: ¡Excelente! Aquí tienes ...
```

**Comandos útiles** (cuando `HOST_DEBUG=1`):
- `help` — Muestra ayuda.
- `tools` — Lista herramientas MCP detectadas.
- `history` — Muestra el historial de conversación enviado a Claude.
- `logs` — Muestra `mcp_interactions.log`.
- `quit` — Sale del host.

## 🔍 Modo Debug (HOST_DEBUG=1)

Actívalo para ver:
- Lanzamiento y conexión (`initialize`, `tools/list`).
- Herramientas registradas.
- Llamadas a herramientas (`tools/call`) y tiempos de espera.
- Posibles errores o _stderr_ del servidor MCP.

Esto es ideal para diagnosticar por qué una herramienta no responde o si hay inconsistencias en el framing MCP.

## 🧠 Notas de implementación

- El host usa un lector **line-based** para MCP (evita problemas con `peek()` en Windows).
- `mcp_interactions.log` registra las interacciones (consola silenciosa si `HOST_DEBUG=0`).
- Se puede personalizar el nombre del bot con `BOT_NAME` (por defecto “🤖 Prof. Oak”).
- En producción, la consola **no muestra JSONs** ni trazas, solo mensajes amables para el usuario final.

## 🧰 Solución de problemas (FAQ)

### 1) `invalid_request_error: tools.*.name: String should match pattern '^[a-zA-Z0-9_-]{1,128}$'`
Claude requiere que el nombre de cada tool **no tenga espacios** ni caracteres fuera de `[A-Za-z0-9_-]`. Si ves este error:
- Asegúrate de que **el nombre compuesto** de la herramienta que envías a Anthropic no incluya espacios.
- Recomendación: evita espacios en el **nombre del servidor** o ajusta el código para **sanitizar** (reemplazar espacios por `_` o `-`).

### 2) “initialize sin respuesta válida” / timeouts
- Verifica rutas en `.env`: `CUSTOM_MCP_SERVER_CMD` y `CUSTOM_MCP_CWD`.
- Ejecuta el servidor MCP manualmente para confirmar que inicia sin errores.
- Activa `HOST_DEBUG=1` para ver `stderr` del servidor en la consola.

### 3) No aparecen herramientas
- Confirma que el servidor MCP responde a `tools/list`.
- Activa `HOST_DEBUG=1` para ver el payload de tools y asegurarte de que tengan `name`, `description`, `inputSchema`.

### 4) 401 / problemas con Anthropic
- Confirma `ANTHROPIC_API_KEY` en `.env` y que tu cuenta tenga acceso al modelo en uso.
- Reintenta con otro modelo soportado si tu suscripción no incluye el configurado.

### 5) Caracteres raros o codificación
- El host fuerza `PYTHONUNBUFFERED=1` y `PYTHONIOENCODING=utf-8` al lanzar el servidor MCP.
- Si tu servidor MCP imprime binarios por `stdout`, puede romper el framing — imprime solo JSON/UTF-8 por `stdout`.

## 🗂️ Estructura relevante

```
src/
└─ host/
   └─ cli.py           ← Punto de entrada del host (este archivo)
tests/
└─ debug_server_script.py (opcional, si lo usas en tu flujo)
mcp_interactions.log   ← Log de interacciones (generado en runtime)
.env.example           ← (sugerido) ejemplo de variables de entorno
```
