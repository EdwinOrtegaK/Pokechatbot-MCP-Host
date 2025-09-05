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

class MCPChatbot:
    """Chatbot principal que actúa como host MCP"""
    
    def __init__(self, anthropic_api_key: str):
        self.anthropic_client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.conversation_history: List[Dict[str, str]] = []
        self.mcp_servers: Dict[str, MCPServer] = {}
        self.active_sessions: Dict[str, ClientSession] = {}
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
        """Conecta a un servidor MCP individual con mejor manejo de errores"""
        child_env = dict(os.environ)
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        if server.cwd:
            child_env["PYTHONPATH"] = "."

        params = StdioServerParameters(
            command=server.command,
            args=server.args,
            cwd=server.cwd,
            env=child_env
        )

        print(f"→ Lanzando: {server.command} {' '.join(server.args)}")
        if server.cwd:
            print(f"  cwd: {server.cwd}")

        ctx = None
        session = None
        
        try:
            # Crear conexión STDIO con timeout más largo
            ctx = stdio_client(params)
            print(f"  Estableciendo conexión STDIO...")
            reader, writer = await asyncio.wait_for(ctx.__aenter__(), timeout=30)

            # Crear sesión MCP
            session = ClientSession(reader, writer)
            print(f"  Inicializando sesión MCP...")
            
            # Initialize con timeout extendido
            init_result = await asyncio.wait_for(session.initialize(), timeout=30)
            print(f"  ✓ Sesión inicializada: {init_result}")
            
            # List tools con timeout extendido
            print(f"  Obteniendo herramientas...")
            tools_result = await asyncio.wait_for(session.list_tools(), timeout=15)
            print(f"  ✓ Herramientas obtenidas: {len(tools_result.tools if tools_result.tools else 0)} tools")

            # Guardar sesión y contexto
            self.active_sessions[name] = session
            if not hasattr(self, "_ctx_managers"):
                self._ctx_managers = {}
            self._ctx_managers[name] = ctx

            # Procesar herramientas
            server_tools = 0
            if hasattr(tools_result, "tools") and tools_result.tools:
                for tool in tools_result.tools:
                    key = f"{name}_{tool.name}"
                    self.available_tools[key] = {
                        "name": tool.name,
                        "description": getattr(tool, "description", "") or "",
                        "server": name,
                        "schema": getattr(tool, "inputSchema", {
                            "type": "object",
                            "properties": {},
                            "required": []
                        })
                    }
                    server_tools += 1
                    print(f"    → {tool.name}: {getattr(tool, 'description', 'Sin descripción')}")

            self.logger.log_interaction(name, "CONNECT", {
                "status": "success",
                "tools_count": server_tools,
                "tools": [t.name for t in (tools_result.tools or [])]
            })
            print(f"✅ Conectado a '{name}' - {server_tools} herramientas disponibles\n")

        except asyncio.TimeoutError as e:
            error_msg = f"Timeout durante conexión. El servidor puede estar tardando en responder."
            print(f"❌ {error_msg}")
            await self._safe_cleanup(ctx, session)
            raise RuntimeError(error_msg)
        except Exception as e:
            error_msg = f"Error de conexión: {str(e)}"
            print(f"❌ {error_msg}")
            await self._safe_cleanup(ctx, session)
            raise RuntimeError(error_msg)

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
        """Llama a una herramienta de un servidor MCP específico"""
        try:
            if server_name not in self.active_sessions:
                raise Exception(f"No hay sesión activa para el servidor '{server_name}'")
            
            session = self.active_sessions[server_name]
            
            # Log de la llamada
            self.logger.log_interaction(server_name, "TOOL_CALL", {
                "tool": tool_name,
                "arguments": arguments
            })
            
            print(f"🔧 Llamando herramienta '{tool_name}' en servidor '{server_name}'...")
            
            # Llamar a la herramienta con timeout
            result = await asyncio.wait_for(
                session.call_tool(tool_name, arguments), 
                timeout=30
            )
            
            # Log de la respuesta
            result_str = str(result)
            self.logger.log_interaction(server_name, "TOOL_RESPONSE", {
                "tool": tool_name,
                "result": result_str[:500] + "..." if len(result_str) > 500 else result_str
            })
            
            return result
            
        except asyncio.TimeoutError:
            error_msg = f"Timeout llamando herramienta '{tool_name}' en '{server_name}'"
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
        for name, session in self.active_sessions.items():
            try:
                await asyncio.wait_for(session.close(), timeout=5)
                print(f"✓ Desconectado de '{name}'")
            except Exception as e:
                print(f"⚠️  Error desconectando '{name}': {str(e)}")
        
        # Cierra context managers (stdio)
        if hasattr(self, "_ctx_managers"):
            for name, ctx in self._ctx_managers.items():
                try:
                    await asyncio.wait_for(ctx.__aexit__(None, None, None), timeout=5)
                except Exception:
                    pass
            self._ctx_managers.clear()
        
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