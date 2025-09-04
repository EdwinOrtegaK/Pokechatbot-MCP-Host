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
        print(f" Servidor MCP '{server.name}' agregado: {server.description}")

    async def _connect_to_single_server(self, name: str, server: MCPServer):
        child_env = dict(os.environ)
        child_env["PYTHONUNBUFFERED"] = "1"
        if server.cwd:
            child_env["PYTHONPATH"] = "."

        params = StdioServerParameters(
            command=server.command,
            args=server.args,   # p.ej. ["-u","-m","server.main"]
            cwd=server.cwd,     # p.ej. C:\...\MCP-PokeVGC-Teambuilder
            env=child_env
        )

        print(f"→ Lanzando: {server.command} {' '.join(server.args)}")
        if server.cwd:
            print(f"  cwd: {server.cwd}")

        ctx = stdio_client(params)
        try:
            reader, writer = await asyncio.wait_for(ctx.__aenter__(), timeout=30)

            session = ClientSession(reader, writer)
            await asyncio.wait_for(session.initialize(), timeout=30)

            tools_result = await asyncio.wait_for(session.list_tools(), timeout=30)

            self.active_sessions[name] = session
            if not hasattr(self, "_ctx_managers"):
                self._ctx_managers = {}
            self._ctx_managers[name] = ctx

            server_tools = 0
            if getattr(tools_result, "tools", None):
                for tool in tools_result.tools:
                    key = f"{name}_{tool.name}"
                    self.available_tools[key] = {
                        "name": tool.name,
                        "description": getattr(tool, "description", "") or "",
                        "server": name,
                        "schema": getattr(tool, "inputSchema",
                                         {"type":"object","properties":{}, "required":[]})
                    }
                    server_tools += 1

            self.logger.log_interaction(name, "CONNECT",
                                        {"status":"success","tools_count":server_tools})
            print(f" ✓ Conectado a '{name}' - {server_tools} herramientas disponibles")

        except asyncio.TimeoutError:
            await self._safe_context_exit(ctx)
            raise RuntimeError("Timeout durante conexión (STDIO/initialize/tools). "
                               "Revisa logging del servidor y framing.")
        except Exception:
            await self._safe_context_exit(ctx)
            raise
    
    async def connect_to_mcp_servers(self):
        print("\nConectando a servidores MCP...")
        for name, server in self.mcp_servers.items():
            try:
                await self._connect_to_single_server(name, server)
            except Exception as e:
                print(f"Error conectando a '{name}': {e}")
                self.logger.log_interaction(name, "CONNECT_ERROR", str(e))
    
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
            
            # Llamar a la herramienta
            result = await session.call_tool(tool_name, arguments)
            
            # Log de la respuesta
            self.logger.log_interaction(server_name, "TOOL_RESPONSE", {
                "tool": tool_name,
                "result": str(result)[:500] + "..." if len(str(result)) > 500 else str(result)
            })
            
            return result
            
        except Exception as e:
            error_msg = f"Error llamando herramienta '{tool_name}' en '{server_name}': {str(e)}"
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
            
            # Llamar a Anthropic
            response = self.anthropic_client.messages.create(
                model="claude-3-sonnet-20240229",
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
                    model="claude-3-sonnet-20240229",
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
            print(f" {error_msg}")
            return error_msg
    
    def show_available_tools(self):
        """Muestra las herramientas MCP disponibles"""
        if not self.available_tools:
            print("No hay herramientas MCP disponibles")
            return
        
        print("\n HERRAMIENTAS MCP DISPONIBLES:")
        print("=" * 50)
        
        for tool_key, tool_info in self.available_tools.items():
            print(f"   {tool_key}")
            print(f"   Servidor: {tool_info['server']}")
            print(f"   Descripción: {tool_info['description']}")
            print()
    
    def show_conversation_history(self):
        """Muestra el historial de la conversación"""
        if not self.conversation_history:
            print("No hay historial de conversación")
            return
        
        print("\nHISTORIAL DE CONVERSACIÓN:")
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
        print("Desconectando servidores MCP...")
        # Cierra sesiones
        for name, session in self.active_sessions.items():
            try:
                await session.close()
                print(f"Desconectado de '{name}'")
            except Exception as e:
                print(f"Error desconectando '{name}': {str(e)}")
        # Cierra context managers (stdio)
        if hasattr(self, "_ctx_managers"):
            for name, ctx in self._ctx_managers.items():
                try:
                    await ctx.__aexit__(None, None, None)
                except Exception:
                    pass
            self._ctx_managers.clear()

    async def _safe_context_exit(self, ctx):
        try:
            await ctx.__aexit__(None, None, None)
        except Exception:
            pass

    async def _safe_session_close(self, session):
        try:
            await session.close()
        except Exception:
            pass

async def main():
    load_dotenv()

    """Función principal del chatbot"""
    print("\nPoke VGC — MCP Host")
    print("=" * 60)
    
    # Solicitar API key de Anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        api_key = input("Ingresa tu API key de Anthropic: ").strip()
        if not api_key:
            print("API key requerida\n")
            return
    
    # Crear chatbot
    chatbot = MCPChatbot(api_key)
    
    # Ejemplo de servidores MCP (personaliza según tus servidores)
    print("\nConfigurando servidores MCP...\n")
    
    # Servidor de sistema de archivos (oficial de Anthropic)
    filesystem_server = MCPServer(
        name="filesystem",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/directory"],
        description="Servidor para operaciones de sistema de archivos"
    )
    
    # Servidor MCP personalizado
    custom_path = os.getenv("CUSTOM_MCP_SERVER_PATH")
    custom_args = os.getenv("CUSTOM_MCP_SERVER_ARGS", "").strip()
    custom_cmd  = os.getenv("CUSTOM_MCP_SERVER_CMD", "python")
    custom_cwd  = os.getenv("CUSTOM_MCP_CWD", "").strip()

    if custom_args:
        args = custom_args.split()
    else:
        args = []

    custom_server = MCPServer(
        name="custom",
        command=custom_cmd,
        args=args,
        cwd=custom_cwd or None,
        description="Tu servidor MCP personalizado"
    )
    chatbot.add_mcp_server(custom_server)

    print(f"Servidor personalizado configurado: {custom_cmd} {' '.join(args)}")
    if custom_cwd:
        print(f"cwd del servidor: {custom_cwd}")
    else:
        print("Sin CUSTOM_MCP_CWD: si usas '-m server.main' necesitas poner el repo como CWD.")

    
    # Agregar más servidores según sea necesario
    # chatbot.add_mcp_server(filesystem_server)  # Descomenta si quieres usar filesystem
    
    try:
        # Conectar a servidores MCP
        await chatbot.connect_to_mcp_servers()
        
        # Mostrar herramientas disponibles
        chatbot.show_available_tools()
        
        print("\n¡Host listo!")
        print("Comandos: help • tools • history • logs • quit")
        print("Escribe tu mensaje para empezar.\n")
        
        # Loop principal del chat
        while True:
                    user = input("👤 Tú: ").strip()
                    if not user:
                        continue
                    low = user.lower()
                    if low == "quit":
                        break
                    if low == "help":
                        print("\nCOMANDOS")
                        print("  help     Muestra esta ayuda")
                        print("  tools    Lista de herramientas MCP disponibles")
                        print("  history  Historial de conversación")
                        print("  logs     Muestra logs de interacciones MCP")
                        print("  quit     Salir\n")
                        continue
                    if low == "tools":
                        chatbot.show_available_tools()
                        continue
                    if low == "history":
                        chatbot.show_conversation_history()
                        continue
                    if low == "logs":
                        chatbot.logger.show_logs()
                        continue
                    
                    print("Claude: ", end="", flush=True)
                    answer = await chatbot.chat(user)
                    print(answer + "\n")
    
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario")
    except Exception as e:
        print(f"Error: {str(e)}")
    finally:
        await chatbot.disconnect()
        print("¡Hasta luego!")

if __name__ == "__main__":
    asyncio.run(main())