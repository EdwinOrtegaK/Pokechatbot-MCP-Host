import asyncio
import json
import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import subprocess
import sys
import os
import re
import shlex
from datetime import datetime
from dotenv import load_dotenv
import threading
from collections import deque

# Apariencia y modo debug
BOT_NAME = os.getenv("BOT_NAME", "🤖 Prof. Oak")
HOST_DEBUG = os.getenv("HOST_DEBUG", "0") == "1"

LIST_TOOLS_ON_CONNECT = os.getenv("LIST_TOOLS_ON_CONNECT", "0") == "1"
SHOW_TOOLS_AT_START   = os.getenv("SHOW_TOOLS_AT_START", "0") == "1"

# Instalar dependencias necesarias
def install_dependencies():
    """Instala las dependencias necesarias para el proyecto"""
    dependencies = [
        "anthropic",
        "mcp",
        "python-dotenv",
        "aiohttp",
        "pydantic"
    ]
    
    for dep in dependencies:
        try:
            if dep == "python-dotenv":
                import dotenv
            elif dep == "aiohttp":
                import aiohttp
            elif dep == "pydantic":
                import pydantic
            else:
                __import__(dep)
        except ImportError:
            print(f"Instalando {dep}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", dep])

try:
    import anthropic
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import aiohttp
    from dotenv import load_dotenv
except ImportError as e:
    print(f"Error importando dependencias: {e}")
    print("Ejecuta: pip install -r requirements.txt")
    sys.exit(1)

@dataclass
class MCPServer:
    """Configuración de un servidor MCP"""
    name: str
    command: str
    args: List[str]
    cwd: Optional[str] = None
    description: str = ""

class MCPLogger:
    """Logger para todas las interacciones MCP"""
    def __init__(self, log_file: str = "mcp_interactions.log"):
        self.log_file = log_file
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler() if HOST_DEBUG else logging.NullHandler()
            ]
        )
        if not HOST_DEBUG:
            logging.getLogger().setLevel(logging.CRITICAL)
        self.logger = logging.getLogger(__name__)
    
    def log_interaction(self, server_name: str, interaction_type: str, data: Any):
        """Registra una interacción con un servidor MCP"""
        timestamp = datetime.now().isoformat()
        log_entry = {
            "timestamp": timestamp,
            "server": server_name,
            "type": interaction_type,
            "data": data
        }
        self.logger.info(f"MCP Interaction: {json.dumps(log_entry, indent=2)}")
    
    def show_logs(self):
        """Muestra los logs de interacciones"""
        if os.path.exists(self.log_file):
            with open(self.log_file, 'r') as f:
                print("\n=== LOGS DE INTERACCIONES MCP ===")
                print(f.read())
        else:
            print("No hay logs disponibles.")

# ====== FRAMING LSP/MCP (STDIO) + CONEXIÓN MANUAL ======
def _send_frame(proc: subprocess.Popen, obj: dict):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
    proc.stdin.write(header)
    proc.stdin.write(data)
    proc.stdin.flush()

def _recv_frame(proc: subprocess.Popen, timeout: float = 60.0) -> Optional[dict]:
    """
    Lee una respuesta MCP con framing Content-Length usando lecturas por línea.
    Evita peek() (problemático en Windows).
    """
    import time
    start = time.time()
    out = proc.stdout

    headers = {}
    # 1) Leer cabeceras hasta el CRLF CRLF
    while True:
        if (time.time() - start) > timeout:
            return None

        line = out.readline()
        if not line:
            # si el proceso murió, salir
            if proc.poll() is not None:
                return None
            time.sleep(0.005)
            continue

        # algunos servers (o errores) podrían enviar JSON directo
        if line.lstrip().startswith(b"{"):
            try:
                return json.loads(line.decode("utf-8"))
            except Exception:
                # seguimos intentando leer cabecera bien formada
                pass

        # fin de cabeceras
        if line in (b"\r\n", b"\n"):
            break

        # parseo simple "Header: valor"
        if b":" in line:
            try:
                k, v = line.decode("latin1", errors="ignore").split(":", 1)
                headers[k.strip().lower()] = v.strip()
            except ValueError:
                continue

    # 2) Leer el cuerpo según Content-Length
    n = 0
    try:
        n = int(headers.get("content-length", "0"))
    except Exception:
        n = 0

    body = b""
    while len(body) < n:
        if (time.time() - start) > timeout:
            return None
        chunk = out.read(n - len(body))
        if not chunk:
            time.sleep(0.005)
            continue
        body += chunk

    # 3) Decodificar JSON
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None

class ManualMCPConnection:
    """Conexión MCP minimalista por STDIO (Content-Length framing)."""

    def __init__(self, command: str, args: list[str], cwd: Optional[str] = None, env: Optional[dict] = None):
        self.proc = subprocess.Popen(
            [command] + args,
            cwd=cwd or None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            env=env,
        )
        self._stderr_ring = deque(maxlen=20000)
        self._stderr_lock = threading.Lock()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def initialize(self, protocol_version: str = "2025-06-18", client_info: Optional[dict] = None, timeout: float = 20.0) -> dict:
        if client_info is None:
            client_info = {"name": "pokevgc-host", "version": "1.0.0"}
        init_msg = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": protocol_version, "capabilities": {}, "clientInfo": client_info}
        }
        _send_frame(self.proc, init_msg)
        resp = _recv_frame(self.proc, timeout=timeout)
        if not resp or "result" not in resp:
            raise RuntimeError(f"initialize sin respuesta válida: {resp}")
        # Enviar notification initialized
        _send_frame(self.proc, {"jsonrpc":"2.0","method":"initialized","params":{}})
        return resp

    def list_tools(self, timeout: float = 20.0) -> dict:
        _send_frame(self.proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = _recv_frame(self.proc, timeout=timeout)
        if not resp or "result" not in resp:
            raise RuntimeError(f"tools/list sin respuesta válida: {resp}")
        return resp

    def call_tool(self, name: str, arguments: dict, timeout: float = 30.0) -> dict:
        _send_frame(self.proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        resp = _recv_frame(self.proc, timeout=timeout)
        if not resp:
            raise RuntimeError("tools/call sin respuesta")
        return resp
            
    def _drain_stderr(self):
        """Lee continuamente stderr para evitar que el pipe se llene."""
        f = self.proc.stderr
        try:
            while True:
                chunk = f.read(1024)
                if not chunk:
                    break
                with self._stderr_lock:
                    self._stderr_ring.extend(chunk)
        except Exception:
            pass

    def read_stderr_snapshot(self, max_bytes: int = 8192) -> str:
        """Devuelve un snapshot de los últimos bytes drenados de stderr (no bloqueante)."""
        with self._stderr_lock:
            data = bytes(self._stderr_ring)
        if not data:
            return ""
        return data[-max_bytes:].decode(errors="ignore")


    def close(self):
        try:
            if self.proc.stdin:
                try:
                    self.proc.stdin.close()
                except Exception:
                    pass
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass

# Cliente HTTP MCP (simple)
# Cliente HTTP MCP (robusto)
import aiohttp

class HttpMCPConnection:
    def __init__(self, base_url: str, headers: Optional[dict] = None):
        self.base_url = base_url.rstrip("/")
        # Headers por defecto + opcionales
        base_headers = {"Content-Type": "application/json"}
        if headers:
            base_headers.update(headers)
        self._headers = base_headers

        self._session: Optional[aiohttp.ClientSession] = None
        # Mantener conexiones vivas un poco más para evitar desconexiones
        self._connector = aiohttp.TCPConnector(keepalive_timeout=30)

    async def _ensure(self):
        if self._session is None or getattr(self._session, "closed", False):
            timeout = aiohttp.ClientTimeout(total=30)
            connector = aiohttp.TCPConnector(keepalive_timeout=30, limit=50)
            default_headers = {"Content-Type": "application/json"}
            default_headers.update(self._headers)
            self._session = aiohttp.ClientSession(
                headers=default_headers,
                timeout=timeout,
                connector=connector
            )

    async def _post(self, payload: dict, timeout: float):
        await self._ensure()
        async with self._session.post(self.base_url, json=payload, timeout=timeout) as r:
            # Levanta si no es 2xx
            r.raise_for_status()
            return await r.json()

    async def initialize(self, protocol_version: str = "2025-06-18", client_info: Optional[dict] = None, timeout: float = 20.0):
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": client_info or {"name": "pokevgc-host", "version": "1.0.0"},
            },
        }
        return await self._post(payload, timeout)

    async def list_tools(self, timeout: float = 20.0):
        payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        return await self._post(payload, timeout)

    async def call_tool(self, name: str, arguments: dict, timeout: float = 30.0):
        payload = {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        return await self._post(payload, timeout)

    async def close(self):
        if self._session:
            try:
                await self._session.close()
            finally:
                self._session = None

class MCPChatbot:
    """Chatbot principal que actúa como host MCP"""
    
    def __init__(self, anthropic_api_key: str):
        self.anthropic_client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.conversation_history: List[Dict[str, str]] = []
        self.mcp_servers: Dict[str, MCPServer] = {}
        self.active_sessions: Dict[str, Any] = {} 
        self.available_tools: Dict[str, Any] = {}
        self.logger = MCPLogger()
        self.conversation_history.append({
            "role": "user",
            "content": "Eres Prof. Oak, un mentor amable de VGC. Responde de forma breve, clara y útil."
        })

    def _sanitize_tool_name(self, s: str) -> str:
        return re.sub(r'[^a-zA-Z0-9_-]', '_', s)[:128]
        
    def add_mcp_server(self, server: MCPServer):
        entry = server.args[-1] if server.args else ""
        if entry.lower().endswith(".py") and not os.path.exists(entry):
            print(f"[CONFIG ERROR] No existe el entrypoint: {entry}")
            return
    
        self.mcp_servers[server.name] = server
        print(f"✓ Servidor MCP '{server.name}' agregado: {server.description}")

    async def _connect_to_single_server(self, name: str, server: MCPServer):
        """Conecta usando framing manual (Content-Length) para evitar timeouts del stdio_client."""
        # Rama HTTP
        if server.command == "HTTP":
            base_url = server.args[0] if server.args else ""
            bearer = os.getenv("MCP_REMOTE_HTTP_BEARER", "").strip()
            headers = {"Authorization": f"Bearer {bearer}"} if bearer else None

            conn = HttpMCPConnection(base_url, headers=headers)

            # initialize (HTTP es asíncrono)
            init_resp = await conn.initialize()
            # tools/list
            tools_resp = await conn.list_tools()
            tools = (tools_resp or {}).get("result", {}).get("tools", []) or []

            # Guardar conexión
            self.active_sessions[name] = conn

            # Registrar herramientas
            server_tools = 0
            for t in tools:
                key = f"{name}_{t.get('name')}"
                self.available_tools[key] = {
                    "name": t.get("name"),
                    "description": t.get("description") or "",
                    "server": name,
                    "schema": t.get("inputSchema", {"type": "object", "properties": {}, "required": []}),
                }
                server_tools += 1
                if HOST_DEBUG and LIST_TOOLS_ON_CONNECT:
                    print(f"    → {t.get('name')}: {t.get('description') or 'Sin descripción'}")

            etiqueta = server.description or name
            print(f"\n✅ Conectado a {etiqueta} ({server_tools} herramientas)\n")
            self.logger.log_interaction(name, "CONNECT", {
                "status": "success",
                "tools_count": server_tools,
                "tools": [t.get("name") for t in tools],
            })
            return

        # Rama STDIO
        if HOST_DEBUG:
            print(f"→ Lanzando: {server.command} {' '.join(server.args)}")
            if server.cwd:
                print(f"  cwd: {server.cwd}")
            print("  Inicializando sesión MCP (manual framing)...")

        # Entorno del proceso hijo
        child_env = dict(os.environ)
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"

        # Lanzar conexión manual
        conn = ManualMCPConnection(server.command, server.args, cwd=server.cwd, env=child_env)

        # initialize
        try:
            init_resp = await asyncio.get_event_loop().run_in_executor(
                None, lambda: conn.initialize(timeout=60)
            )
            if HOST_DEBUG:
                print("  ✓ Sesión inicializada")
        except Exception:
            
            if HOST_DEBUG:
                err_snip = ""
                try:
                    err_snip = conn.read_stderr_snapshot()
                except Exception:
                    pass
                print("  ❌ Falló initialize")
                if err_snip.strip():
                    print("  [stderr del servidor]\n" + err_snip)
            raise

        # tools/list
        if HOST_DEBUG:
            print("  Obteniendo herramientas (manual framing)...")

        tools_resp = await asyncio.get_event_loop().run_in_executor(None, conn.list_tools)
        tools = (tools_resp or {}).get("result", {}).get("tools", []) or []

        if HOST_DEBUG:
            print(f"  ✓ Herramientas obtenidas: {len(tools)}")
            err = conn.read_stderr_snapshot()
            if err.strip():
                print("\n[stderr del servidor]")
                print(err)

        # Guardar conexión
        self.active_sessions[name] = conn

        # Procesar herramientas
        server_tools = 0
        for t in tools:
            key = f"{name}_{t.get('name')}"
            self.available_tools[key] = {
                "name": t.get("name"),
                "description": t.get("description") or "",
                "server": name,
                "schema": t.get("inputSchema", {"type":"object","properties":{},"required":[]})
            }
            server_tools += 1
            if HOST_DEBUG and LIST_TOOLS_ON_CONNECT:
                print(f"    → {t.get('name')}: {t.get('description') or 'Sin descripción'}")

        self.logger.log_interaction(name, "CONNECT", {
            "status": "success",
            "tools_count": server_tools,
            "tools": [t.get("name") for t in tools]
        })

        etiqueta = server.description or name
        print(f"\n✅ Conectado a {etiqueta} ({server_tools} herramientas)")

    async def _safe_cleanup(self, ctx, session):    
        """Limpia recursos de manera segura"""
        if session:
            try:
                await asyncio.wait_for(session.close(), timeout=5)
            except:
                pass
        if ctx:
            try:
                await asyncio.wait_for(ctx.__aexit__(None, None, None), timeout=5)
            except:
                pass
    
    async def connect_to_mcp_servers(self):
        """Conecta a todos los servidores MCP configurados"""
        if not self.mcp_servers:
            print("⚠️  No hay servidores MCP configurados")
            return
            
        print("\n🔌 Conectando a servidores MCP...")
        successful_connections = 0
        
        for name, server in self.mcp_servers.items():
            try:
                await self._connect_to_single_server(name, server)
                successful_connections += 1
            except Exception as e:
                print(f"❌ Error conectando a '{name}': {e}\n")
                self.logger.log_interaction(name, "CONNECT_ERROR", str(e))
        
        print(f"📊 Resumen: {successful_connections}/{len(self.mcp_servers)} servidores conectados\n")
    
    async def call_mcp_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Llama a una herramienta del servidor usando la conexión manual."""
        try:
            if server_name not in self.active_sessions:
                raise Exception(f"No hay sesión activa para el servidor '{server_name}'")

            conn = self.active_sessions[server_name]

            # Log a archivo
            self.logger.log_interaction(
                server_name, "TOOL_CALL",
                {"tool": tool_name, "arguments": arguments}
            )

            # HTTP: métodos async; STDIO: sync a executor
            async def _do_call():
                if isinstance(conn, HttpMCPConnection):
                    return await conn.call_tool(tool_name, arguments, timeout=30)
                return await asyncio.get_event_loop().run_in_executor(
                    None, lambda: conn.call_tool(tool_name, arguments)
                )
            
            # Primer intento
            try:
                resp = await asyncio.wait_for(_do_call(), timeout=35)

            except asyncio.TimeoutError as te:
                if isinstance(conn, HttpMCPConnection):
                    try:
                        await conn.close()
                        await conn.initialize()
                        resp = await asyncio.wait_for(_do_call(), timeout=35)
                    except Exception as e2:
                        raise e2
                else:
                    raise te
                
            except Exception as e:
                if isinstance(conn, HttpMCPConnection):
                    try:
                        await conn.close()
                        await conn.initialize()
                        resp = await asyncio.wait_for(_do_call(), timeout=35)
                    except Exception as e2:
                        raise e2
                else:
                    raise e

            # Log sólo a archivo
            self.logger.log_interaction(
                server_name, "TOOL_RESPONSE",
                {"tool": tool_name, "result": str(resp)[:500]}
            )

            if HOST_DEBUG:
                print(f"   ✓ {tool_name} listo")

            return resp
        
        except asyncio.TimeoutError:
            error_msg = f"⏰ Tiempo agotado en {tool_name}"
            if HOST_DEBUG:
                try:
                    err_snip = self.active_sessions[server_name].read_stderr_snapshot()
                    if err_snip.strip():
                        error_msg += f"\n[stderr]\n{err_snip}"
                except Exception:
                    pass
            print(error_msg)
            self.logger.log_interaction(server_name, "TOOL_ERROR", error_msg)
            return {"error": error_msg}

        except Exception as e:
            error_msg = f"❌ Error en {tool_name}: {str(e)}"

            if HOST_DEBUG:
                try:
                    err_snip = self.active_sessions[server_name].read_stderr_snapshot()
                    if err_snip.strip():
                        error_msg += f"\n[stderr]\n{err_snip}"
                except Exception:
                    pass

            print(error_msg)
            self.logger.log_interaction(server_name, "TOOL_ERROR", error_msg)
            return {"error": error_msg}
    
    def format_tools_for_anthropic(self) -> List[Dict[str, Any]]:
        """Formatea las herramientas MCP para usar con Anthropic (sin caracteres inválidos)."""
        anthropic_tools = []
        self.tool_name_map: Dict[str, Dict[str, str]] = {}

        for tool_key, tool_info in self.available_tools.items():
            sanitized = self._sanitize_tool_name(tool_key)

            self.tool_name_map[sanitized] = {
                "server": tool_info["server"],
                "name": tool_info["name"],
            }

            anthropic_tool = {
                "name": sanitized,
                "description": tool_info["description"],
                "input_schema": tool_info.get("schema", {
                    "type": "object",
                    "properties": {},
                    "required": []
                })
            }
            anthropic_tools.append(anthropic_tool)

        return anthropic_tools

    
    async def process_tool_calls(self, tool_calls: List[Any]) -> List[Dict[str, Any]]:
        """Procesa las llamadas a herramientas solicitadas por Anthropic."""
        tool_results = []

        for tool_call in tool_calls:
            tool_name = tool_call.name
            tool_input = tool_call.input

            if hasattr(self, "tool_name_map") and tool_name in self.tool_name_map:
                server_name = self.tool_name_map[tool_name]["server"]
                actual_tool_name = self.tool_name_map[tool_name]["name"]
            else:
                # Fallback (por si acaso): intenta buscar por clave “legacy”
                if tool_name in self.available_tools:
                    tool_info = self.available_tools[tool_name]
                    server_name = tool_info["server"]
                    actual_tool_name = tool_info["name"]
                else:
                    tool_results.append({
                        "tool_use_id": tool_call.id,
                        "type": "tool_result",
                        "content": f"Error: Herramienta '{tool_name}' no encontrada"
                    })
                    continue

            result = await self.call_mcp_tool(server_name, actual_tool_name, tool_input)

            tool_results.append({
                "tool_use_id": tool_call.id,
                "type": "tool_result",
                "content": str(result)
            })

        return tool_results
    
    async def chat(self, user_message: str) -> str:
        """Procesa un mensaje del usuario y genera una respuesta usando Anthropic + MCP"""
        try:
            # Agregar mensaje del usuario al historial
            self.conversation_history.append({
                "role": "user",
                "content": user_message
            })
            
            # Preparar herramientas para Anthropic
            tools = self.format_tools_for_anthropic()
            
            if HOST_DEBUG:
                print(f"🤖 Consultando Claude con {len(tools)} herramientas disponibles...")
            
            # Llamar a Anthropic
            response = self.anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4000,
                messages=self.conversation_history,
                tools=tools if tools else []
            )
            
            response_content = ""
            tool_calls = []
            
            # Procesar la respuesta
            for content_block in response.content:
                if content_block.type == "text":
                    response_content += content_block.text
                elif content_block.type == "tool_use":
                    tool_calls.append(content_block)
            
            # Si hay llamadas a herramientas, procesarlas
            if tool_calls:
                if HOST_DEBUG:
                    print(f"🔧 Procesando {len(tool_calls)} llamadas a herramientas...")

                self.conversation_history.append({
                    "role": "assistant",
                    "content": response.content
                })

                # Procesar herramientas
                tool_results = await self.process_tool_calls(tool_calls)

                # Agregar resultados de herramientas al historial
                self.conversation_history.append({
                    "role": "user",
                    "content": tool_results
                })

                # Obtener respuesta final
                final_response = self.anthropic_client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=4000,
                    messages=self.conversation_history,
                    tools=self.format_tools_for_anthropic()
                )

                final_content = ""
                for content_block in final_response.content:
                    if content_block.type == "text":
                        final_content += content_block.text

                # Agregar respuesta final al historial
                self.conversation_history.append({
                    "role": "assistant",
                    "content": final_content
                })

                return final_content
            else:
                # No hay herramientas, respuesta directa
                self.conversation_history.append({
                    "role": "assistant",
                    "content": response_content
                })
                return response_content
                
        except Exception as e:
            error_msg = f"Error en chat: {str(e)}"
            print(f"❌ {error_msg}")
            return error_msg
    
    def show_available_tools(self):
        """Muestra las herramientas MCP disponibles, agrupadas por servidor."""
        if not self.available_tools:
            print("⚠️  No hay herramientas MCP disponibles")
            return

        by_server = {}
        for tool_key, tool_info in self.available_tools.items():
            server = tool_info['server']
            by_server.setdefault(server, []).append(tool_info)

        for server_name, tools in by_server.items():
            print(f"\n📡 Servidor: {server_name}")
            for t in sorted(tools, key=lambda x: x["name"]):
                print(f"   • {t['name']}")
        print()
    
    def show_conversation_history(self):
        """Muestra el historial de la conversación"""
        if not self.conversation_history:
            print("📝 No hay historial de conversación")
            return
        
        print("\n📝 HISTORIAL DE CONVERSACIÓN:")
        print("=" * 50)
        
        for i, message in enumerate(self.conversation_history, 1):
            role = message["role"].upper()
            content = message["content"]
            if isinstance(content, list):
                content = str(content)[:200] + "..."
            elif len(str(content)) > 200:
                content = str(content)[:200] + "..."
            
            print(f"{i}. [{role}]: {content}")
            print()
    
    async def disconnect(self):
        """Desconecta todos los servidores MCP"""
        if not self.active_sessions and not hasattr(self, "_ctx_managers"):
            return
            
        print("\n🔌 Desconectando servidores MCP...\n")
        
        # Cierra sesiones
        for name, conn in list(self.active_sessions.items()):
            try:
                if isinstance(conn, HttpMCPConnection):
                    await conn.close()
                else:
                    conn.close()
                print(f"✓ Desconectado de '{name}'\n")
            except Exception as e:
                print(f"⚠️  Error desconectando '{name}': {str(e)}")        
        self.active_sessions.clear()

async def main():
    """Función principal del chatbot"""
    load_dotenv()
    
    print("\n🚀 Poke VGC — MCP Host")
    print("=" * 60)
    
    # Solicitar API key de Anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        api_key = input("🔑 Ingresa tu API key de Anthropic: ").strip()
        if not api_key:
            print("❌ API key requerida\n")
            return
    
    # Crear chatbot
    chatbot = MCPChatbot(api_key)
    
    # Configuración del servidor MCP personalizado desde .env
    print("\n⚙️  Configurando servidores MCP...\n")
    
    custom_cmd = os.getenv("CUSTOM_MCP_SERVER_CMD", "python")
    custom_args = os.getenv("CUSTOM_MCP_SERVER_ARGS", "").strip()
    custom_cwd = os.getenv("CUSTOM_MCP_CWD", "").strip()

    if not custom_args:
        print("❌ CUSTOM_MCP_SERVER_ARGS no configurado en .env")
        return

    args = custom_args.split()
    
    custom_server = MCPServer(
        name="PokeChatbot VGC",
        command=custom_cmd,
        args=args,
        cwd=custom_cwd or None,
        description="Servidor MCP para construcción de equipos Pokémon VGC"
    )

    chatbot.add_mcp_server(custom_server)

    # Registrar servidor MCP por HTTP si hay URL en .env
    remote_http_url = os.getenv("MCP_REMOTE_HTTP_URL", "").strip()
    if remote_http_url:
        remote_name = os.getenv("MCP_REMOTE_HTTP_NAME", "VGC HTTP Remote").strip()
        http_server = MCPServer(
            name=remote_name,
            command="HTTP",
            args=[remote_http_url],
            cwd=None,
            description="Servidor MCP remoto (HTTP)"
        )
        chatbot.add_mcp_server(http_server)
    
    try:
        # Conectar a servidores MCP
        await chatbot.connect_to_mcp_servers()
        
        # Verificar si hay herramientas disponibles
        if not chatbot.available_tools:
            print("\n⚠️  No se pudieron cargar herramientas MCP.")
            print("El chatbot funcionará sin capacidades MCP.")
        
        # Mostrar herramientas disponibles
        if HOST_DEBUG and SHOW_TOOLS_AT_START:
            chatbot.show_available_tools()
        
        print("✅ ¡Host listo!\n")
        print("💬 Comandos disponibles:")
        print("   help     - Muestra ayuda")
        print("   tools    - Lista herramientas MCP")
        print("   history  - Muestra historial")
        print("   logs     - Muestra logs MCP")
        print("   quit     - Salir")
        print("\n💭 Escribe tu mensaje para empezar...\n")
        
        # Loop principal del chat
        while True:
            try:
                user_input = input("👤 Entrenador: ").strip()
                if not user_input:
                    continue
                
                user_lower = user_input.lower()
                
                if user_lower == "quit":
                    break
                elif user_lower == "help":
                    print("\n📚 COMANDOS DISPONIBLES:")
                    print("   help     - Muestra esta ayuda")
                    print("   tools    - Lista de herramientas MCP disponibles")
                    print("   history  - Historial de conversación")
                    print("   logs     - Muestra logs de interacciones MCP")
                    print("   quit     - Salir del chatbot\n")
                    continue
                elif user_lower == "tools":
                    if HOST_DEBUG: chatbot.show_available_tools()
                    else: print("\n(Comando disponible sólo en modo debug)\n")
                    continue
                elif user_lower == "history":
                    chatbot.show_conversation_history()
                    continue
                elif user_lower == "logs":
                    chatbot.logger.show_logs()
                    continue
                
                if user_lower.startswith("call "):
                    try:
                        tokens = shlex.split(user_input)
                        if len(tokens) < 3:
                            print("Uso: call <SERVER_NAME> <TOOL_NAME> [JSON_ARGS]\n")
                            continue

                        _, server_name, tool_name, *rest = tokens
                        json_args = {}
                        if rest:
                            try:
                                json_args = json.loads(rest[0])
                            except json.JSONDecodeError:
                                print('JSON_ARGS inválido. Ejemplo: {"text":"Hola"}\n')
                                continue

                        resp = await chatbot.call_mcp_tool(server_name, tool_name, json_args)
                        print()
                        print(f"{BOT_NAME}: {resp}\n")
                    except Exception as e:
                        print(f"Error ejecutando call: {e}\n")
                    continue

                # Procesar mensaje normal
                response = await chatbot.chat(user_input)
                print()
                print(f"{BOT_NAME}: {response}\n")
                
            except KeyboardInterrupt:
                print("\n\n⏸️  Interrumpido por el usuario")
                break
            except Exception as e:
                print(f"\n❌ Error procesando mensaje: {str(e)}\n")
    
    except KeyboardInterrupt:
        print("\n⏸️  Interrumpido por el usuario")
    except Exception as e:
        print(f"❌ Error fatal: {str(e)}")
    finally:
        await chatbot.disconnect()
        print("👋 ¡Hasta luego!\n")

if __name__ == "__main__":
    asyncio.run(main())