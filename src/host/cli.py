import asyncio
import json
import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import subprocess
import sys
import os
from datetime import datetime
from dotenv import load_dotenv
import threading
from collections import deque

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
                logging.StreamHandler()
            ]
        )
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

class MCPChatbot:
    """Chatbot principal que actúa como host MCP"""
    
    def __init__(self, anthropic_api_key: str):
        self.anthropic_client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.conversation_history: List[Dict[str, str]] = []
        self.mcp_servers: Dict[str, MCPServer] = {}
        self.active_sessions: Dict[str, ManualMCPConnection] = {}
        self.available_tools: Dict[str, Any] = {}
        self.logger = MCPLogger()
        
    def add_mcp_server(self, server: MCPServer):
        entry = server.args[-1] if server.args else ""
        if entry.lower().endswith(".py") and not os.path.exists(entry):
            print(f"[CONFIG ERROR] No existe el entrypoint: {entry}")
            return
    
        self.mcp_servers[server.name] = server
        print(f"✓ Servidor MCP '{server.name}' agregado: {server.description}")

    async def _connect_to_single_server(self, name: str, server: MCPServer):
        """Conecta usando framing manual (Content-Length) para evitar timeouts del stdio_client."""
        print(f"→ Lanzando: {server.command} {' '.join(server.args)}")
        if server.cwd:
            print(f"  cwd: {server.cwd}")

        # Entorno del proceso hijo (desbloquear buffering y setear encoding)
        child_env = dict(os.environ)
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"

        # Lanzar conexión manual
        conn = ManualMCPConnection(server.command, server.args, cwd=server.cwd, env=child_env)

        # initialize
        print("  Inicializando sesión MCP (manual framing)...")
        try:
            init_resp = await asyncio.get_event_loop().run_in_executor(
                None, lambda: conn.initialize(timeout=60)
            )
            print(f"  ✓ Sesión inicializada: {init_resp.get('result', {})}")
        except Exception as e:
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
        print("  Obteniendo herramientas (manual framing)...")
        tools_resp = await asyncio.get_event_loop().run_in_executor(None, conn.list_tools)
        tools = (tools_resp or {}).get("result", {}).get("tools", []) or []
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
            print(f"    → {t.get('name')}: {t.get('description') or 'Sin descripción'}")

        self.logger.log_interaction(name, "CONNECT", {
            "status": "success",
            "tools_count": server_tools,
            "tools": [t.get("name") for t in tools]
        })
        print(f"✅ Conectado a '{name}' - {server_tools} herramientas disponibles\n")

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
                print(f"❌ Error conectando a '{name}': {e}")
                self.logger.log_interaction(name, "CONNECT_ERROR", str(e))
        
        print(f"📊 Resumen: {successful_connections}/{len(self.mcp_servers)} servidores conectados")
    
    async def call_mcp_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Llama a una herramienta del servidor usando la conexión manual."""
        try:
            if server_name not in self.active_sessions:
                raise Exception(f"No hay sesión activa para el servidor '{server_name}'")

            conn = self.active_sessions[server_name]

            self.logger.log_interaction(server_name, "TOOL_CALL", {"tool": tool_name, "arguments": arguments})
            print(f"🔧 Llamando herramienta '{tool_name}' en servidor '{server_name}'...")

            resp = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, lambda: conn.call_tool(tool_name, arguments)),
                timeout=30  
            )
            self.logger.log_interaction(server_name, "TOOL_RESPONSE", {"tool": tool_name, "result": str(resp)[:500]})

            return resp
        
        except asyncio.TimeoutError:
            err_snip = ""
            try:
                err_snip = self.active_sessions[server_name].read_stderr_snapshot()
            except Exception:
                pass
            error_msg = f"Timeout llamando herramienta '{tool_name}' en '{server_name}'"
            if err_snip.strip():
                error_msg += f"\n[stderr]\n{err_snip}"
            print(f"⏰ {error_msg}")
            self.logger.log_interaction(server_name, "TOOL_ERROR", error_msg)
            return {"error": error_msg}

        except Exception as e:
            error_msg = f"Error llamando herramienta '{tool_name}' en '{server_name}': {str(e)}"
            print(f"❌ {error_msg}")
            self.logger.log_interaction(server_name, "TOOL_ERROR", error_msg)
            return {"error": error_msg}
    
    def format_tools_for_anthropic(self) -> List[Dict[str, Any]]:
        """Formatea las herramientas MCP para usar con Anthropic"""
        anthropic_tools = []
        
        for tool_key, tool_info in self.available_tools.items():
            anthropic_tool = {
                "name": tool_key,
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
        """Procesa las llamadas a herramientas solicitadas por Anthropic"""
        tool_results = []
        
        for tool_call in tool_calls:
            tool_name = tool_call.name
            tool_input = tool_call.input
            
            # Encontrar el servidor correspondiente
            if tool_name in self.available_tools:
                tool_info = self.available_tools[tool_name]
                server_name = tool_info["server"]
                actual_tool_name = tool_info["name"]
                
                # Llamar a la herramienta
                result = await self.call_mcp_tool(server_name, actual_tool_name, tool_input)
                
                tool_results.append({
                    "tool_use_id": tool_call.id,
                    "type": "tool_result",
                    "content": str(result)
                })
            else:
                tool_results.append({
                    "tool_use_id": tool_call.id,
                    "type": "tool_result",
                    "content": f"Error: Herramienta '{tool_name}' no encontrada"
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
                print(f"🔧 Procesando {len(tool_calls)} llamadas a herramientas...")
                
                # Agregar la respuesta con tool calls al historial
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
                    tools=tools
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
        """Muestra las herramientas MCP disponibles"""
        if not self.available_tools:
            print("⚠️  No hay herramientas MCP disponibles")
            return
        
        print("\n🛠️  HERRAMIENTAS MCP DISPONIBLES:")
        print("=" * 50)
        
        by_server = {}
        for tool_key, tool_info in self.available_tools.items():
            server = tool_info['server']
            if server not in by_server:
                by_server[server] = []
            by_server[server].append(tool_info)
        
        for server_name, tools in by_server.items():
            print(f"\n📡 Servidor: {server_name}")
            for tool in tools:
                print(f"   • {tool['name']}: {tool['description']}")
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
            
        print("\n🔌 Desconectando servidores MCP...")
        
        # Cierra sesiones
        for name, conn in list(self.active_sessions.items()):
            try:
                conn.close()
                print(f"✓ Desconectado de '{name}'")
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
    print("\n⚙️  Configurando servidores MCP...")
    
    custom_cmd = os.getenv("CUSTOM_MCP_SERVER_CMD", "python")
    custom_args = os.getenv("CUSTOM_MCP_SERVER_ARGS", "").strip()
    custom_cwd = os.getenv("CUSTOM_MCP_CWD", "").strip()

    if not custom_args:
        print("❌ CUSTOM_MCP_SERVER_ARGS no configurado en .env")
        return

    args = custom_args.split()
    
    custom_server = MCPServer(
        name="pokevgc",
        command=custom_cmd,
        args=args,
        cwd=custom_cwd or None,
        description="Servidor MCP para construcción de equipos Pokémon VGC"
    )
    
    chatbot.add_mcp_server(custom_server)
    
    print(f"📋 Configuración:")
    print(f"   Comando: {custom_cmd}")
    print(f"   Args: {' '.join(args)}")
    if custom_cwd:
        print(f"   Directorio: {custom_cwd}")
    
    try:
        # Conectar a servidores MCP
        await chatbot.connect_to_mcp_servers()
        
        # Verificar si hay herramientas disponibles
        if not chatbot.available_tools:
            print("\n⚠️  No se pudieron cargar herramientas MCP.")
            print("El chatbot funcionará sin capacidades MCP.")
        
        # Mostrar herramientas disponibles
        chatbot.show_available_tools()
        
        print("\n✅ ¡Host listo!")
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
                user_input = input("👤 Tú: ").strip()
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
                    chatbot.show_available_tools()
                    continue
                elif user_lower == "history":
                    chatbot.show_conversation_history()
                    continue
                elif user_lower == "logs":
                    chatbot.logger.show_logs()
                    continue
                
                # Procesar mensaje normal
                print("🤖 Claude: ", end="", flush=True)
                response = await chatbot.chat(user_input)
                print(response + "\n")
                
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
        print("👋 ¡Hasta luego!")

if __name__ == "__main__":
    asyncio.run(main())