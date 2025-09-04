"""
Cliente HTTP para servidores MCP remotos
"""
import asyncio
import aiohttp
import json
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import time

from .logging_mcp import MCPLogger
from ..utils.jsonrpc import JsonRpcClient, JsonRpcRequest, JsonRpcResponse

@dataclass
class MCPHttpServerConfig:
    """Configuración para servidor MCP remoto via HTTP"""
    name: str
    url: str
    headers: Dict[str, str] = None
    timeout: int = 30
    description: str = ""
    enabled: bool = True
    auth_token: str = None

class MCPHttpClient:
    """Cliente HTTP para servidores MCP remotos"""
    
    def __init__(self, logger: MCPLogger = None):
        self.logger = logger or MCPLogger()
        self.jsonrpc_client = JsonRpcClient()
        self.servers: Dict[str, MCPHttpServerConfig] = {}
        self.sessions: Dict[str, aiohttp.ClientSession] = {}
        self.available_tools: Dict[str, Any] = {}
        self.server_capabilities: Dict[str, Dict] = {}
    
    def add_server(self, config: MCPHttpServerConfig):
        """Agrega un servidor HTTP MCP"""
        self.servers[config.name] = config
        self.logger.log_interaction(config.name, "HTTP_SERVER_REGISTERED", {
            "url": config.url,
            "enabled": config.enabled
        })
        print(f"Servidor HTTP '{config.name}' registrado: {config.url}")
    
    async def connect_all_servers(self):
        """Conecta a todos los servidores HTTP MCP"""
        print("Conectando a servidores MCP remotos...")
        
        connection_tasks = []
        for name, config in self.servers.items():
            if config.enabled:
                task = asyncio.create_task(self.connect_server(name))
                connection_tasks.append(task)
        
        if connection_tasks:
            results = await asyncio.gather(*connection_tasks, return_exceptions=True)
            successful = sum(1 for r in results if r is True)
            failed = len(results) - successful
            print(f"Conexiones HTTP completadas: {successful} exitosas, {failed} fallidas")
        else:
            print("No hay servidores HTTP habilitados")
    
    async def connect_server(self, server_name: str) -> bool:
        """Conecta a un servidor HTTP MCP específico"""
        if server_name not in self.servers:
            return False
        
        config = self.servers[server_name]
        
        try:
            self.logger.log_connection(server_name, "HTTP_ATTEMPTING", f"URL: {config.url}")
            print(f"Conectando a servidor remoto '{server_name}'...")
            
            # Crear sesión HTTP
            headers = config.headers or {}
            if config.auth_token:
                headers["Authorization"] = f"Bearer {config.auth_token}"
            
            timeout = aiohttp.ClientTimeout(total=config.timeout)
            session = aiohttp.ClientSession(
                headers=headers,
                timeout=timeout,
                connector=aiohttp.TCPConnector(ssl=False)  # Para desarrollo
            )
            
            self.sessions[server_name] = session
            
            # Probar conexión con ping o initialize
            await self._test_connection(server_name)
            
            # Cargar herramientas disponibles
            await self._load_server_tools_http(server_name)
            
            tools_count = len(self._get_server_tools(server_name))
            self.logger.log_connection(server_name, "HTTP_SUCCESS", f"Herramientas: {tools_count}")
            print(f"Conectado a '{server_name}' - {tools_count} herramientas disponibles")
            
            return True
            
        except Exception as e:
            error_msg = f"Error conectando a servidor remoto '{server_name}': {str(e)}"
            self.logger.log_connection(server_name, "HTTP_FAILED", error_msg)
            print(f"  {error_msg}")
            
            if server_name in self.sessions:
                await self.sessions[server_name].close()
                del self.sessions[server_name]
            
            return False
    
    async def _test_connection(self, server_name: str):
        """Prueba la conexión con el servidor"""
        config = self.servers[server_name]
        session = self.sessions[server_name]
        
        # Crear request de inicialización o ping
        request = self.jsonrpc_client.create_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": {"listChanged": True},
                "sampling": {}
            },
            "clientInfo": {
                "name": "MCP-Chatbot-UVG",
                "version": "1.0.0"
            }
        })
        
        async with session.post(config.url, json=request.to_dict()) as response:
            if response.status != 200:
                raise Exception(f"HTTP {response.status}: {await response.text()}")
            
            result = await response.json()
            if "error" in result:
                raise Exception(f"Server error: {result['error']}")
    
    async def _load_server_tools_http(self, server_name: str):
        """Carga herramientas de servidor HTTP"""
        config = self.servers[server_name]
        session = self.sessions[server_name]
        
        try:
            # Solicitar lista de herramientas
            request = self.jsonrpc_client.create_request("tools/list", {})
            
            async with session.post(config.url, json=request.to_dict()) as response:
                if response.status != 200:
                    raise Exception(f"HTTP {response.status}")
                
                result = await response.json()
                
                if "error" in result:
                    raise Exception(f"Server error: {result['error']}")
                
                tools_data = result.get("result", {})
                tools_list = tools_data.get("tools", [])
                
                server_tools = {}
                for tool in tools_list:
                    tool_key = f"{server_name}_{tool['name']}"
                    tool_info = {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "server": server_name,
                        "schema": tool.get("inputSchema", {}),
                        "is_remote": True
                    }
                    
                    server_tools[tool_key] = tool_info
                    self.available_tools[tool_key] = tool_info
                
                self.server_capabilities[server_name] = {
                    "tools": len(server_tools),
                    "is_remote": True,
                    "url": config.url
                }
                
                self.logger.log_interaction(server_name, "HTTP_TOOLS_LOADED", {
                    "tools_count": len(server_tools),
                    "tool_names": list(server_tools.keys())
                })
        
        except Exception as e:
            self.logger.log_error(server_name, f"Error cargando herramientas HTTP: {str(e)}")
            raise
    
    async def call_tool_http(self, server_name: str, tool_name: str, arguments: Dict[str, Any], 
                           request_id: str = None) -> Any:
        """Llama a herramienta en servidor HTTP"""
        start_time = time.time()
        
        try:
            if server_name not in self.sessions:
                raise Exception(f"No hay sesión HTTP para '{server_name}'")
            
            config = self.servers[server_name]
            session = self.sessions[server_name]
            
            # Log de la llamada
            self.logger.log_tool_call(server_name, tool_name, arguments, request_id)
            
            # Crear request JSON-RPC
            request = self.jsonrpc_client.create_request("tools/call", {
                "name": tool_name,
                "arguments": arguments
            })
            
            # Realizar llamada HTTP
            async with session.post(config.url, json=request.to_dict()) as response:
                duration = (time.time() - start_time) * 1000
                
                if response.status != 200:
                    raise Exception(f"HTTP {response.status}: {await response.text()}")
                
                result = await response.json()
                
                if "error" in result:
                    raise Exception(f"Server error: {result['error']}")
                
                tool_result = result.get("result", {})
                
                # Log de respuesta exitosa
                self.logger.log_tool_response(server_name, tool_name, tool_result, request_id, duration)
                
                return tool_result
        
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            error_msg = f"Error en herramienta HTTP '{tool_name}' en '{server_name}': {str(e)}"
            
            self.logger.log_error(server_name, error_msg, {
                "tool": tool_name,
                "arguments": arguments,
                "duration_ms": duration,
                "is_http": True
            })
            
            return {"error": error_msg, "server": server_name, "tool": tool_name, "type": "http_error"}
    
    async def disconnect_server(self, server_name: str):
        """Desconecta servidor HTTP"""
        if server_name not in self.sessions:
            return
        
        try:
            session = self.sessions[server_name]
            await session.close()
            
            del self.sessions[server_name]
            
            # Remover herramientas
            tools_to_remove = [key for key in self.available_tools.keys() 
                             if self.available_tools[key]["server"] == server_name]
            for tool_key in tools_to_remove:
                del self.available_tools[tool_key]
            
            if server_name in self.server_capabilities:
                del self.server_capabilities[server_name]
            
            self.logger.log_connection(server_name, "HTTP_DISCONNECTED", "Desconexión exitosa")
            print(f"Desconectado servidor HTTP '{server_name}'")
            
        except Exception as e:
            error_msg = f"Error desconectando servidor HTTP '{server_name}': {str(e)}"
            self.logger.log_connection(server_name, "HTTP_DISCONNECT_ERROR", error_msg)
            print(f"   {error_msg}")
    
    async def disconnect_all_servers(self):
        """Desconecta todos los servidores HTTP"""
        print("Desconectando servidores HTTP...")
        
        disconnect_tasks = []
        for server_name in list(self.sessions.keys()):
            task = asyncio.create_task(self.disconnect_server(server_name))
            disconnect_tasks.append(task)
        
        if disconnect_tasks:
            await asyncio.gather(*disconnect_tasks, return_exceptions=True)
    
    def _get_server_tools(self, server_name: str) -> Dict[str, Any]:
        """Obtiene herramientas específicas de un servidor HTTP"""
        return {k: v for k, v in self.available_tools.items() 
                if v["server"] == server_name}
    
    def get_available_tools(self) -> Dict[str, Any]:
        """Retorna todas las herramientas HTTP disponibles"""
        return self.available_tools.copy()
    
    def get_server_status(self) -> Dict[str, Dict]:
        """Estado de servidores HTTP"""
        status = {}
        
        for name, config in self.servers.items():
            is_connected = name in self.sessions
            tools_count = len(self._get_server_tools(name))
            
            status[name] = {
                "configured": True,
                "enabled": config.enabled,
                "connected": is_connected,
                "tools_available": tools_count,
                "is_remote": True,
                "url": config.url,
                "capabilities": self.server_capabilities.get(name, {}),
                "description": config.description
            }
        
        return status