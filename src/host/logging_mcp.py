"""
Sistema de logging para interacciones MCP
"""
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict
from pathlib import Path

class MCPLogger:
    """Logger especializado para interacciones MCP"""
    
    def __init__(self, log_file: str = "logs/mcp_interactions.log", max_history: int = 1000):
        self.log_file = log_file
        self.max_history = max_history
        
        # Crear directorio de logs si no existe
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        
        # Configurar logging
        self.logger = logging.getLogger("mcp_logger")
        self.logger.setLevel(logging.INFO)
        
        # Handler para archivo
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(file_formatter)
        
        # Handler para consola
        console_handler = logging.StreamHandler()
        console_formatter = logging.Formatter(
            '%(levelname)s - %(message)s'
        )
        console_handler.setFormatter(console_formatter)
        
        # Evitar duplicados
        if not self.logger.handlers:
            self.logger.addHandler(file_handler)
            self.logger.addHandler(console_handler)
    
    def log_interaction(self, server_name: str, interaction_type: str, data: Any, 
                       request_id: str = None, duration: float = None):
        """Registra una interacción con un servidor MCP"""
        timestamp = datetime.now().isoformat()
        
        log_entry = {
            "timestamp": timestamp,
            "server": server_name,
            "type": interaction_type,
            "request_id": request_id,
            "duration_ms": duration,
            "data": self._sanitize_data(data)
        }
        
        self.logger.info(f"MCP: {json.dumps(log_entry, ensure_ascii=False, indent=None)}")
    
    def log_connection(self, server_name: str, status: str, details: str = ""):
        """Registra eventos de conexión"""
        self.log_interaction(server_name, f"CONNECTION_{status.upper()}", {
            "details": details
        })
    
    def log_tool_call(self, server_name: str, tool_name: str, arguments: Dict[str, Any], 
                     request_id: str = None):
        """Registra llamada a herramienta"""
        self.log_interaction(server_name, "TOOL_CALL", {
            "tool": tool_name,
            "arguments": arguments
        }, request_id)
    
    def log_tool_response(self, server_name: str, tool_name: str, result: Any, 
                         request_id: str = None, duration: float = None):
        """Registra respuesta de herramienta"""
        self.log_interaction(server_name, "TOOL_RESPONSE", {
            "tool": tool_name,
            "result": result
        }, request_id, duration)
    
    def log_error(self, server_name: str, error: str, context: Dict[str, Any] = None):
        """Registra errores"""
        self.log_interaction(server_name, "ERROR", {
            "error": error,
            "context": context or {}
        })
    
    def _sanitize_data(self, data: Any) -> Any:
        """Sanitiza datos para logging (trunca si es muy largo)"""
        if isinstance(data, str) and len(data) > 1000:
            return data[:1000] + "... [truncated]"
        elif isinstance(data, dict):
            return {k: self._sanitize_data(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._sanitize_data(item) for item in data[:10]]  # Max 10 items
        return data
    
    def get_logs(self, server_name: str = None, interaction_type: str = None, 
                limit: int = 50) -> list:
        """Obtiene logs filtrados"""
        logs = []
        
        if not os.path.exists(self.log_file):
            return logs
        
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if "MCP:" in line:
                        try:
                            # Extraer JSON del log
                            json_part = line.split("MCP: ", 1)[1].strip()
                            log_entry = json.loads(json_part)
                            
                            # Aplicar filtros
                            if server_name and log_entry.get("server") != server_name:
                                continue
                            if interaction_type and log_entry.get("type") != interaction_type:
                                continue
                            
                            logs.append(log_entry)
                        except (json.JSONDecodeError, IndexError):
                            continue
        except Exception as e:
            self.logger.error(f"Error leyendo logs: {e}")
        
        # Retornar los más recientes
        return logs[-limit:] if logs else []
    
    def show_logs_summary(self):
        """Muestra un resumen de los logs"""
        logs = self.get_logs(limit=100)
        
        if not logs:
            print("No hay logs de interacciones MCP disponibles")
            return
        
        # Estadísticas
        servers = set(log.get("server") for log in logs)
        types = {}
        
        for log in logs:
            log_type = log.get("type", "UNKNOWN")
            types[log_type] = types.get(log_type, 0) + 1
        
        print("\nRESUMEN DE LOGS MCP")
        print("=" * 40)
        print(f"Total de interacciones: {len(logs)}")
        print(f"Servidores activos: {', '.join(servers)}")
        print("\nTipos de interacciones:")
        for log_type, count in sorted(types.items()):
            print(f"  {log_type}: {count}")
        
        print(f"\nArchivo completo: {self.log_file}")
    
    def clear_logs(self):
        """Limpia los logs"""
        try:
            if os.path.exists(self.log_file):
                os.remove(self.log_file)
            self.logger.info("Logs limpiados")
            print("Logs de MCP limpiados")
        except Exception as e:
            self.logger.error(f"Error limpiando logs: {e}")
            print(f"Error limpiando logs: {e}")