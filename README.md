# Pokechatbot-MCP-Host

A **console host** to connect models (Anthropic/Claude) with an **MCP (Model Context Protocol) server** via **STDIO** and enable **tools** like _suggest_team_, _pool_filter_, etc. Designed for the Pokémon VGC project (MCP-PokeVGC-Teambuilder), with clean output, customizable bot name **“🤖 Prof. Oak”**, and optional _debug_ mode.

## ✨ Features

- **MCP connection via STDIO** with **Content-Length** framing (robust on Windows, Linux, and macOS).
- **Tool discovery** via `tools/list` and **execution** via `tools/call`.
- **Integration with Anthropic** (Claude) with **tool use** support.
- **Configurable bot name** (`BOT_NAME`) and **debug mode** (`HOST_DEBUG`) to see technical traces when required.
- Utility commands (in debug mode): `help`, `tools`, `history`, `logs`, `quit`.

> Scope: this README only covers the host (`Pokechatbot‑MCP‑Host`). The MCP server (e.g., `MCP-PokeVGC-Teambuilder`) is configured externally and launched as a subprocess.

## 🧱 Architecture (quick view)

```
┌────────────────────────┐
│  Pokechatbot-MCP-Host  │   ← CLI (this repo)
│  (src/host/cli.py)     │
└───────────┬────────────┘
            │ STDIO (Content-Length)
            ▼
┌────────────────────────┐
│   MCP Server (external)  │  ← e.g. MCP-PokeVGC-Teambuilder
│   tools: suggest_team,   │
│          pool_filter, …  │
└────────────────────────┘
            ▲
            │ HTTP (Anthropic API)
            ▼
┌────────────────────────┐
│     Anthropic/Claude    │
└────────────────────────┘
```

High-level flow:
1. The host reads configuration from `.env` and launches the **MCP server** via STDIO.
2. Negotiates MCP: `initialize` → `initialized` → `tools/list`.
3. When chatting, it sends the history + catalog of **tools** to Claude.
4. If Claude decides to use a tool, the host calls **`tools/call`** to the MCP server and returns the result to the user.

## ⚙️ Configuration

Create a **`.env`** file at the project root with at least:

```dotenv
# Anthropic key
ANTHROPIC_API_KEY=sk-ant-...

# How to launch the MCP server (Windows examples)
CUSTOM_MCP_SERVER_CMD=C:/path/to/venv/Scripts/python.exe
CUSTOM_MCP_SERVER_ARGS=-u -m server.main
CUSTOM_MCP_CWD=C:/path/to/MCP-PokeVGC-Teambuilder
```

**Supported variables**

- `ANTHROPIC_API_KEY` — Your Anthropic key.
- `CUSTOM_MCP_SERVER_CMD` — Executable that starts the MCP server (e.g., the `python` of the server’s venv).
- `CUSTOM_MCP_SERVER_ARGS` — Arguments to start the server (e.g., `-u -m server.main`).
- `CUSTOM_MCP_CWD` — Working directory where the MCP server lives.
- `BOT_NAME` — Name shown in the console when responding (default: `🤖 Prof. Oak`).
- `HOST_DEBUG` — `1` for debug mode (shows tools, logs, and useful JSONs), `0` for clean mode.

## 🚀 Installation

1. Clone the repository:

```bash
git clone <repository-url>
cd Pokechatbot-MCP-Host
```

2. Create and activate a virtual environment:

```bash
python -m venv .venv

# On Linux/macOS
source .venv/bin/activate

# On Windows (PowerShell)
.venv\Scripts\activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

## ▶️ Execution
From the project root:
```bash
python -m src.host.cli
```

Expected output (in **non-debug** mode, styled):

```
🚀 Poke VGC — MCP Host
============================================================

⚙️  Configuring MCP servers...

✓ MCP server 'PokeChatbot VGC' added: MCP server for Pokémon VGC team building

🔌 Connecting to MCP servers...

✅ Connected to MCP server for Pokémon VGC team building (5 tools)

📊 Summary: 1/1 servers connected

📡 Server: PokeChatbot VGC

✅ Host ready!

💭 Type your message to start...

👤 Trainer: (Type whatever you want to ask)

🤖 Prof. Oak: Excellent! Here you go ...
```

**Useful commands** (when `HOST_DEBUG=1`):
- `help` — Show help.
- `tools` — List detected MCP tools.
- `history` — Show conversation history sent to Claude.
- `logs` — Show `mcp_interactions.log`.
- `quit` — Exit the host.

## 🔍 Debug Mode (HOST_DEBUG=1)

Enable it to see:
- Launch and connection (`initialize`, `tools/list`).
- Registered tools.
- Tool calls (`tools/call`) and timeouts.
- Possible errors or MCP server _stderr_.

This is ideal for diagnosing why a tool does not respond or if there are inconsistencies in MCP framing.

## 🧠 Implementation notes

- The host uses a **line-based** reader for MCP (avoids issues with `peek()` on Windows).
- `mcp_interactions.log` records the interactions (silent console if `HOST_DEBUG=0`).
- The bot name can be customized with `BOT_NAME` (default “🤖 Prof. Oak”).
- In production, the console **does not show JSONs** or traces, only user-friendly messages.

## 🧰 Troubleshooting (FAQ)

### 1) `invalid_request_error: tools.*.name: String should match pattern '^[a-zA-Z0-9_-]{1,128}$'`
Claude requires each tool name to **not contain spaces** or characters outside `[A-Za-z0-9_-]`. If you see this error:
- Make sure the **composite name** of the tool you send to Anthropic does not include spaces.
- Recommendation: avoid spaces in the **server name** or adjust the code to **sanitize** (replace spaces with `_` or `-`).

### 2) “initialize without valid response” / timeouts
- Check paths in `.env`: `CUSTOM_MCP_SERVER_CMD` and `CUSTOM_MCP_CWD`.
- Run the MCP server manually to confirm it starts without errors.
- Enable `HOST_DEBUG=1` to see the server’s `stderr` in the console.

### 3) Tools not showing up
- Confirm that the MCP server responds to `tools/list`.
- Enable `HOST_DEBUG=1` to see the tools payload and ensure they have `name`, `description`, `inputSchema`.

### 4) 401 / Anthropic issues
- Confirm `ANTHROPIC_API_KEY` in `.env` and that your account has access to the model in use.
- Retry with another supported model if your subscription does not include the configured one.

### 5) Strange characters or encoding issues
- The host enforces `PYTHONUNBUFFERED=1` and `PYTHONIOENCODING=utf-8` when launching the MCP server.
- If your MCP server prints binaries to `stdout`, it may break framing — only print JSON/UTF-8 to `stdout`.

## 🗂️ Relevant structure

```
src/
└─ host/
   └─ cli.py           ← Host entry point (this file)
tests/
└─ debug_server_script.py (optional, if you use it in your flow)
mcp_interactions.log   ← Interaction log (generated at runtime)
.env.example           ← (suggested) example of environment variables
```
