"""
Gestor de servidores MCP - Maneja conexiones y herramientas
"""
import asyncio
import json
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import subprocess
import os
from pathlib import Path

# Imports MCP
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import mcp.types as types
except ImportError:
    print("Error: Instala MCP con 'pip install mcp'")
    raise

from .logging_mcp import MCPLogger

@dataclass
class MCPServerConfig:
    """Configuración de un servidor MCP"""
    name: str
    command: str
    args: List[str]
    description: str = ""
    enabled: bool = True
    env_vars: Dict[str, str] = None
    working_directory: str = None

class MCPManager:
    """Gestor principal de servidores MCP"""
    
    def __init__(self, logger: MCPLogger = None):
        self.logger = logger or MCPLogger()
        self.servers: Dict[str, MCPServerConfig] = {}
        self.active_sessions: Dict[str, ClientSession] = {}
        self.available_tools: Dict[str, Any] = {}
        self.server_capabilities: Dict[str, Dict] = {}
        
    def add_server(self, config: MCPServerConfig):
        """Agrega un servidor MCP a la configuración"""
        self.servers[config.name] = config
        self.logger.log_interaction(config.name, "SERVER_REGISTERED", {
            "command": config.command,
            "args": config.args,
            "enabled": config.enabled
        })
        print(f"Servidor '{config.name}' registrado: {config.description}")
    
    def remove_server(self, server_name: str):
        """Remueve un servidor MCP"""
        if server_name in self.servers:
            del self.servers[server_name]
            if server_name in self.active_sessions:
                asyncio.create_task(self.disconnect_server(server_name))
            print(f"Servidor '{server_name}' removido")
    
    async def connect_all_servers(self):
        """Conecta a todos los servidores MCP configurados y habilitados"""
        print("🔌 Iniciando conexiones a servidores MCP...")
        
        connection_tasks = []
        for name, config in self.servers.items():
            if config.enabled:
                task = asyncio.create_task(self.connect_server(name))
                connection_tasks.append(task)
        
        if connection_tasks:
            results = await asyncio.gather(*connection_tasks, return_exceptions=True)
            
            successful = sum(1 for r in results if r is True)
            failed = len(results) - successful
            
            print(f"Conexiones completadas: {successful} exitosas, {failed} fallidas")
        else:
            print("No hay servidores habilitados para conectar")
    
    async def connect_server(self, server_name: str) -> bool:
        """Conecta a un servidor MCP específico"""
        if server_name not in self.servers:
            self.logger.log_error(server_name, f"Servidor '{server_name}' no encontrado en configuración")
            return False
        
        config = self.servers[server_name]
        
        try:
            self.logger.log_connection(server_name, "ATTEMPTING", f"Comando: {config.command}")
            print(f"Conectando a '{server_name}'...")
            
            # Preparar parámetros del servidor
            server_params = StdioServerParameters(
                command=config.command,
                args=config.args,
                env=config.env_vars or {},
                cwd=config.working_directory
            )
            
            # Crear sesión
            session = await stdio_client(server_params)
            self.active_sessions[server_name] = session
            
            # Inicializar servidor y obtener capacidades
            await self._initialize_server_session(server_name, session)
            
            # Obtener herramientas disponibles
            await self._load_server_tools(server_name, session)
            
            self.logger.log_connection(server_name, "SUCCESS", f"Herramientas cargadas: {len(self._get_server_tools(server_name))}")
            print(f"Conectado a '{server_name}' - {len(self._get_server_tools(server_name))} herramientas disponibles")
            
            return True
            
        except Exception as e:
            error_msg = f"Error conectando a '{server_name}': {str(e)}"
            self.logger.log_connection(server_name, "FAILED", error_msg)
            print(f"  {error_msg}")
            
            # Limpiar sesión fallida
            if server_name in self.active_sessions:
                del self.active_sessions[server_name]
            
            return False
    
    async def _initialize_server_session(self, server_name: str, session: ClientSession):
        """Inicializa la sesión con el servidor MCP"""
        try:
            # Realizar handshake/inicialización si es necesario
            # Algunos servidores MCP requieren inicialización específica
            pass
        except Exception as e:
            self.logger.log_error(server_name, f"Error en inicialización: {str(e)}")
            raise
    
    async def _load_server_tools(self, server_name: str, session: ClientSession):
        """Carga las herramientas disponibles de un servidor"""
        try:
            # Obtener herramientas
            tools_result = await session.list_tools()
            
            server_tools = {}
            if hasattr(tools_result, 'tools'):
                for tool in tools_result.tools:
                    tool_key = f"{server_name}_{tool.name}"
                    tool_info = {
                        "name": tool.name,
                        "description": getattr(tool, 'description', ''),
                        "server": server_name,
                        "schema": getattr(tool, 'inputSchema', {}),
                        "original_tool": tool
                    }
                    
                    server_tools[tool_key] = tool_info
                    self.available_tools[tool_key] = tool_info
            
            # Obtener recursos si están disponibles
            try:
                resources_result = await session.list_resources()
                if hasattr(resources_result, 'resources'):
                    self.server_capabilities[server_name] = {
                        "tools": len(server_tools),
                        "resources": len(resources_result.resources),
                        "supports_resources": True
                    }
            except:
                self.server_capabilities[server_name] = {
                    "tools": len(server_tools),
                    "resources": 0,
                    "supports_resources": False
                }
            
            self.logger.log_interaction(server_name, "TOOLS_LOADED", {
                "tools_count": len(server_tools),
                "tool_names": list(server_tools.keys())
            })
            
        except Exception as e:
            self.logger.log_error(server_name, f"Error cargando herramientas: {str(e)}")
            raise
    
    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any], 
                       request_id: str = None) -> Any:
        """Llama a una herramienta específica de un servidor MCP"""
        import time
        start_time = time.time()
        
        try:
            if server_name not in self.active_sessions:
                raise Exception(f"No hay sesión activa para el servidor '{server_name}'")
            
            session = self.active_sessions[server_name]
            
            # Log de la llamada
            self.logger.log_tool_call(server_name, tool_name, arguments, request_id)
            
            # Realizar la llamada
            result = await session.call_tool(tool_name, arguments)
            
            # Calcular duración
            duration = (time.time() - start_time) * 1000
            
            # Log de la respuesta
            self.logger.log_tool_response(server_name, tool_name, result, request_id, duration)
            
            return result
            
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            error_msg = f"Error llamando herramienta '{tool_name}' en '{server_name}': {str(e)}"
            
            self.logger.log_error(server_name, error_msg, {
                "tool": tool_name,
                "arguments": arguments,
                "duration_ms": duration
            })
            
            return {"error": error_msg, "server": server_name, "tool": tool_name}
    
    async def disconnect_server(self, server_name: str):
        """Desconecta de un servidor MCP específico"""
        if server_name not in self.active_sessions:
            return
        
        try:
            session = self.active_sessions[server_name]
            await session.close()
            
            # Limpiar datos del servidor
            del self.active_sessions[server_name]
            
            # Remover herramientas del servidor
            tools_to_remove = [key for key in self.available_tools.keys() 
                             if self.available_tools[key]["server"] == server_name]
            for tool_key in tools_to_remove:
                del self.available_tools[tool_key]
            
            if server_name in self.server_capabilities:
                del self.server_capabilities[server_name]
            
            self.logger.log_connection(server_name, "DISCONNECTED", "Desconexión exitosa")
            print(f"Desconectado de '{server_name}'")
            
        except Exception as e:
            error_msg = f"Error desconectando de '{server_name}': {str(e)}"
            self.logger.log_connection(server_name, "DISCONNECT_ERROR", error_msg)
            print(f"  {error_msg}")
    
    async def disconnect_all_servers(self):
        """Desconecta de todos los servidores MCP"""
        print("Desconectando todos los servidores MCP...")
        
        disconnect_tasks = []
        for server_name in list(self.active_sessions.keys()):
            task = asyncio.create_task(self.disconnect_server(server_name))
            disconnect_tasks.append(task)
        
        if disconnect_tasks:
            await asyncio.gather(*disconnect_tasks, return_exceptions=True)
        
        print("Todas las desconexiones completadas")
    
    def get_available_tools(self) -> Dict[str, Any]:
        """Retorna todas las herramientas disponibles"""
        return self.available_tools.copy()
    
    def _get_server_tools(self, server_name: str) -> Dict[str, Any]:
        """Obtiene herramientas específicas de un servidor"""
        return {k: v for k, v in self.available_tools.items() 
                if v["server"] == server_name}
    
    def get_server_status(self) -> Dict[str, Dict]:
        """Obtiene el estado de todos los servidores"""
        status = {}
        
        for name, config in self.servers.items():
            is_connected = name in self.active_sessions
            tools_count = len(self._get_server_tools(name))
            
            status[name] = {
                "configured": True,
                "enabled": config.enabled,
                "connected": is_connected,
                "tools_available": tools_count,
                "capabilities": self.server_capabilities.get(name, {}),
                "description": config.description
            }
        
        return status
    
    def show_status(self):
        """Muestra el estado de todos los servidores MCP"""
        status = self.get_server_status()
        
        if not status:
            print("No hay servidores MCP configurados")
            return
        
        print("\nESTADO DE SERVIDORES MCP")
        print("=" * 50)
        
        for name, info in status.items():
            status_icon = "🟢" if info["connected"] else "🔴"
            enabled_text = "habilitado" if info["enabled"] else "deshabilitado"
            
            print(f"{status_icon} {name} ({enabled_text})")
            print(f"   Descripción: {info['description']}")
            print(f"   Herramientas: {info['tools_available']}")
            
            if info["capabilities"]:
                caps = info["capabilities"]
                if caps.get("supports_resources"):
                    print(f"   Recursos: {caps.get('resources', 0)}")
            print()
    
    def show_available_tools(self):
        """Muestra todas las herramientas MCP disponibles"""
        if not self.available_tools:
            print("No hay herramientas MCP disponibles")
            return
        
        print("\nHERRAMIENTAS MCP DISPONIBLES")
        print("=" * 50)
        
        # Agrupar por servidor
        by_server = {}
        for tool_key, tool_info in self.available_tools.items():
            server_name = tool_info["server"]
            if server_name not in by_server:
                by_server[server_name] = []
            by_server[server_name].append((tool_key, tool_info))
        
        for server_name, tools in by_server.items():
            print(f"Servidor: {server_name}")
            for tool_key, tool_info in tools:
                print(f"      {tool_info['name']}")
                print(f"      Descripción: {tool_info['description']}")
                print(f"      ID completo: {tool_key}")
            print()
    
    def load_config_from_file(self, config_path: str):
        """Carga configuración de servidores desde archivo JSON"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            if "mcp_servers" in config_data:
                for server_config in config_data["mcp_servers"]:
                    config = MCPServerConfig(
                        name=server_config["name"],
                        command=server_config["command"],
                        args=server_config.get("args", []),
                        description=server_config.get("description", ""),
                        enabled=server_config.get("enabled", True),
                        env_vars=server_config.get("env_vars"),
                        working_directory=server_config.get("working_directory")
                    )
                    self.add_server(config)
            
            print(f"Configuración cargada desde: {config_path}")
            
        except FileNotFoundError:
            print(f"Archivo de configuración no encontrado: {config_path}")
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON en {config_path}: {e}")
        except Exception as e:
            print(f"Error cargando configuración: {e}")