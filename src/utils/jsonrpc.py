"""
Implementación de JSON-RPC para MCP
"""
import json
import uuid
from typing import Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class JsonRpcMessage:
    jsonrpc: str = "2.0"
    id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {"jsonrpc": self.jsonrpc}
        if self.id is not None:
            result["id"] = self.id
        return result

@dataclass 
class JsonRpcRequest(JsonRpcMessage):
    method: str
    params: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result["method"] = self.method
        if self.params is not None:
            result["params"] = self.params
        return result

@dataclass
class JsonRpcResponse(JsonRpcMessage):
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        if self.result is not None:
            result["result"] = self.result
        if self.error is not None:
            result["error"] = self.error
        return result

class JsonRpcClient:
    """Cliente JSON-RPC para MCP"""
    
    def __init__(self):
        self.request_id = 0
    
    def create_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> JsonRpcRequest:
        """Crea una nueva petición JSON-RPC"""
        self.request_id += 1
        return JsonRpcRequest(
            id=str(self.request_id),
            method=method,
            params=params
        )
    
    def create_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> JsonRpcRequest:
        """Crea una notificación JSON-RPC (sin ID)"""
        return JsonRpcRequest(
            method=method,
            params=params
        )
    
    def parse_response(self, data: str) -> JsonRpcResponse:
        """Parsea una respuesta JSON-RPC"""
        try:
            json_data = json.loads(data)
            return JsonRpcResponse(
                id=json_data.get("id"),
                result=json_data.get("result"),
                error=json_data.get("error")
            )
        except json.JSONDecodeError as e:
            return JsonRpcResponse(
                error={"code": -32700, "message": f"Parse error: {str(e)}"}
            )