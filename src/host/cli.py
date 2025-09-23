import asyncio
import json
import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
import subprocess
import sys
import os
import re
import shlex
from datetime import datetime
from dotenv import load_dotenv
import threading
from collections import deque
import aiohttp
import anthropic
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.session import ClientSession

# Apariencia y modo debug
global BOT_NAME, HOST_DEBUG, LIST_TOOLS_ON_CONNECT, SHOW_TOOLS_AT_START
BOT_NAME = os.getenv("BOT_NAME", "🤖 Prof. Oak")
HOST_DEBUG = os.getenv("HOST_DEBUG", "0") == "1"
LIST_TOOLS_ON_CONNECT = os.getenv("LIST_TOOLS_ON_CONNECT", "0") == "1"
SHOW_TOOLS_AT_START   = os.getenv("SHOW_TOOLS_AT_START", "0") == "1"
PROTOCOL_DEFAULT = "2024-11-05"

@dataclass
class MCPServer:
    name: str
    command: str
    args: List[str]
    cwd: Optional[str] = None
    description: str = ""
    protocol_version: Optional[str] = None
    type: str = "stdio"
    init: str = "modern"
    base_url: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    env: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    framing: str = "lsp"
    quirks: Dict[str, Any] = field(default_factory=dict)
    transport: str = "manual" 

# Conexión con el SDK oficial MCP (stdio)
class SdkMCPConnection:
    def __init__(self, command: str, args: List[str], cwd: Optional[str], env: Optional[dict]):
        self._params = StdioServerParameters(command=command, args=args, cwd=cwd, env=env or {})
        self._ctx = None
        self._read = None
        self._write = None
        self.session: Optional[ClientSession] = None

    async def initialize(self, protocol_version: str = PROTOCOL_DEFAULT, client_info: Optional[dict] = None):
        self._ctx = stdio_client(self._params)
        try:
            self._read, self._write = await self._ctx.__aenter__()
            self.session = ClientSession(self._read, self._write)

            # Intentos de compatibilidad (distintas versiones del SDK)
            tried = []

            async def _try(**kwargs):
                tried.append(kwargs)
                return await self.session.initialize(**kwargs)

            try:
                # 1) SDK moderno (camelCase)
                await _try(
                    protocolVersion=protocol_version,
                    capabilities={},
                    clientInfo=client_info or {"name": "pokevgc-host", "version": "1.0.0"},
                )
            except TypeError:
                try:
                    # 2) variante snake_case
                    await _try(
                        protocol_version=protocol_version,
                        capabilities={},
                        client_info=client_info or {"name": "pokevgc-host", "version": "1.0.0"},
                    )
                except TypeError:
                    try:
                        # 3) sin protocolo, con clientInfo/capabilities camelCase
                        await _try(
                            capabilities={},
                            clientInfo=client_info or {"name": "pokevgc-host", "version": "1.0.0"},
                        )
                    except TypeError:
                        try:
                            # 4) sin kwargs (SDK muy viejo)
                            await self.session.initialize()
                        except TypeError as e:
                            raise TypeError(
                                f"No pude llamar a session.initialize con ninguna firma compatible. Intentos: {tried}"
                            ) from e

            # Algunos servers requieren el 'initialized'
            try:
                await self.session.initialized()
            except Exception:
                pass

        except Exception:
            # Cierre limpio si falla
            if self._ctx is not None:
                try:
                    await self._ctx.__aexit__(None, None, None)
                except Exception:
                    pass
                self._ctx = None
            self.session = None
            self._read = None
            self._write = None
            raise

    async def list_tools(self) -> list[dict]:
        # Devuelve una lista de dicts con name/description/inputSchema
        resp = await self.session.list_tools()
        tools = []
        for t in getattr(resp, "tools", []) or []:
            name = getattr(t, "name", None) or (isinstance(t, dict) and t.get("name"))
            desc = getattr(t, "description", None) or (isinstance(t, dict) and t.get("description"))
            schema = getattr(t, "inputSchema", None) or (isinstance(t, dict) and t.get("inputSchema")) or {}

            if hasattr(schema, "model_dump"):
                schema = schema.model_dump()
            elif hasattr(schema, "dict"):
                schema = schema.dict()

            tools.append({"name": name, "description": desc or "", "inputSchema": schema or {}})
        return tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        resp = await self.session.call_tool(name=name, arguments=arguments or {})
        blocks = []
        for b in getattr(resp, "content", []) or []:
            t = getattr(b, "text", None) or (isinstance(b, dict) and b.get("text"))
            if t is not None:
                blocks.append({"type": "text", "text": t})
            else:
                try:
                    import json as _json
                    blocks.append({"type": "text", "text": _json.dumps(b, ensure_ascii=False)})
                except Exception:
                    blocks.append({"type": "text", "text": str(b)})
        return {"result": {"content": blocks}}

    async def close(self):
        try:
            if self.session:
                await self.session.close()
        finally:
            if self._ctx is not None:
                try:
                    await self._ctx.__aexit__(None, None, None)
                except Exception:
                    pass
            self._ctx = None
            self.session = None
            self._read = None
            self._write = None

def load_config(path: str = "src/host/config.json") -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"No se encontró {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

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

def _recv_frame(proc: subprocess.Popen, timeout: float = 60.0) -> Optional[dict]:
    """
    Lector robusto de frames MCP:
    - Soporta framing con Content-Length y JSON crudo (línea que empieza con '{')
    - Ignora ruido en stdout hasta ver un encabezado válido o un JSON
    - Lee exactamente N bytes del cuerpo si hay Content-Length
    """
    import time
    start = time.time()
    out = proc.stdout

    def _deadline() -> bool:
        return (time.time() - start) > timeout

    # 1) Buscar cabecera Content-Length o JSON crudo
    content_len = None
    while True:
        if _deadline():
            return None

        line = out.readline()
        if not line:
            if proc.poll() is not None:
                return None
            time.sleep(0.005)
            continue

        if line.lstrip().startswith(b"{"):
            try:
                return json.loads(line.decode("utf-8"))
            except Exception:
                continue

        s = line.strip().decode("latin1", errors="ignore")
        if not s:
            continue

        if s.lower().startswith("content-length:"):
            try:
                content_len = int(s.split(":", 1)[1].strip())
            except Exception:
                content_len = None

            while True:
                if _deadline():
                    return None
                l2 = out.readline()
                if not l2:
                    if proc.poll() is not None:
                        return None
                    time.sleep(0.005)
                    continue
                if l2 in (b"\r\n", b"\n"):
                    break
            break

    if content_len is None or content_len < 0:
        return None

    # 2) Leer exactamente 'content_len' bytes del cuerpo
    body = b""
    while len(body) < content_len:
        if _deadline():
            return None
        chunk = out.read(content_len - len(body))
        if not chunk:
            time.sleep(0.005)
            continue
        body += chunk

    # 3) Parsear JSON del cuerpo
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None

class ManualMCPConnection:
    """Conexión MCP minimalista por STDIO (Content-Length framing)."""

    def __init__(
        self,
        command: str,
        args: list[str],
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        framing: str = "lsp",
    ):
        self.framing = (framing or "lsp").lower()
        merged_env = dict(os.environ)
        merged_env.update(env or {})
        merged_env.setdefault("PYTHONUNBUFFERED", "1")
        merged_env.setdefault("PYTHONIOENCODING", "utf-8")

        self.proc = subprocess.Popen(
            [command] + args,
            cwd=cwd or None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            env=merged_env,
        )
        self._stderr_ring = deque(maxlen=20000)
        self._stderr_lock = threading.Lock()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        self._rpc_id = 0
    
    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _initialize_once(self, payload: dict, timeout: float = 25.0) -> Optional[dict]:
        msg = {"jsonrpc": "2.0", "id": self._next_id(), "method": "initialize", "params": payload}
        self._send(msg)
        import time; time.sleep(0.15)
        return _recv_frame(self.proc, timeout=timeout)
    
    def initialize_modern(self, protocol_version: str, client_info: Optional[dict] = None, timeout: float = 25.0) -> dict:
        client_info = client_info or {"name": "pokevgc-host", "version": "1.0.0"}
        payload = {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {}},
            "clientInfo": client_info,
        }
        resp = self._initialize_once(payload, timeout=timeout)
        if resp and "result" in resp:
            self._send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            return resp
        raise RuntimeError(f"initialize_modern: sin respuesta válida: {resp}") 

    def initialize_compat(self, protocol_version: str, client_info: Optional[dict] = None) -> dict:
        """
        Envia varias variantes de 'initialize' SIN esperar respuesta inmediata,
        luego lee UNA sola respuesta con timeout y manda 'initialized'.
        """
        import time
        client_info = client_info or {"name": "pokevgc-host", "version": "1.0.0"}
    
        variants = [
            {"protocolVersion": protocol_version},  # mínima
            {"protocolVersion": protocol_version, "capabilities": {}},  # media
            {"protocolVersion": protocol_version, "capabilities": {"tools": {}}, "clientInfo": client_info},  # completa
        ]
    
        # 1) Enviar TODAS sin bloquear lectura
        for payload in variants:
            try:
                msg = {"jsonrpc": "2.0", "id": self._next_id(), "method": "initialize", "params": payload}
                self._send(msg)
                time.sleep(0.15)  # pequeña pausa para no coalescer frames
            except Exception:
                pass
            
        # 2) Leer UNA respuesta válida con timeout holgado
        resp = _recv_frame(self.proc, timeout=25.0)
        if resp and "result" in resp:
            self._send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            return resp
    
        raise RuntimeError("initialize_compat: sin respuesta del servidor tras enviar variantes")

    
    def initialize_legacy_minimal(self, protocol_version: str, timeout: float = 6.0) -> dict:
        """Para servers 1.x cabrones (AutoAdvisor): UN initialize mínimo y 'initialized'."""
        payload = {"protocolVersion": protocol_version}
        resp = self._initialize_once(payload, timeout=timeout)
        if resp and "result" in resp:
            self._send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            return resp
        raise RuntimeError(f"initialize_legacy_minimal: sin respuesta válida: {resp}")

    def list_tools(
        self,
        timeout: float = 20.0,
        method_order: Optional[List[str]] = None,
        param_order: Optional[List[str]] = None
    ) -> dict:
        """
        Lista herramientas con compatibilidad mejorada:
        - Prueba ambos métodos: 'tools/list' y 'tools.list'
        - Prueba múltiples variantes de params (empty, omit, null, includeSchema, cursor, pagination...)
        - Retorna en cuanto vea result.tools
        """
        import json as _json, time
    
        methods = method_order or ["tools/list", "tools.list"]
    
        # Usamos el string SENTINEL_OMIT para distinguir "no incluir params"
        SENTINEL_OMIT = "__OMIT_PARAMS__"
    
        param_variants = {
            "empty": {},                                   # {}
            "omit": SENTINEL_OMIT,                         # sin 'params'
            "null": None,                                  # "params": null
            "includeSchema_true": {"includeSchema": True},
            "includeSchema_false": {"includeSchema": False},
            "cursor_null": {"cursor": None},
            "pagination_cursor": {"pagination": {"cursor": None}},
            "limit_50": {"limit": 50},
            "page_1": {"page": 1},
            "page_size_50": {"page_size": 50},
        }
    
        # Orden amplio por defecto (puedes sobreescribir con quirks)
        order = param_order or [
            "empty", "omit", "includeSchema_true", "includeSchema_false",
            "cursor_null", "pagination_cursor",
            "limit_50", "page_1", "page_size_50", "null"
        ]
    
        last_error = None
        for m in methods:
            for key in order:
                msg_id = self._next_id()
                payload = {"jsonrpc": "2.0", "id": msg_id, "method": m}
    
                variant = param_variants[key]
                if variant is SENTINEL_OMIT:
                    pass  # no agregamos 'params'
                elif key == "null":
                    payload["params"] = None
                else:
                    payload["params"] = variant
    
                self._send(payload)
                resp = _recv_frame(self.proc, timeout=timeout)
    
                if resp and "result" in resp and isinstance(resp["result"], dict) and "tools" in resp["result"]:
                    return resp
    
                if resp and "error" in resp:
                    if HOST_DEBUG:
                        print(f"  [tools/list compat] {m} | {key} -> { _json.dumps(resp['error'], ensure_ascii=False) }")
                    last_error = RuntimeError(f"[{m} | {key}] error: {_json.dumps(resp['error'], ensure_ascii=False)}")
                    time.sleep(0.03)
                    continue
                
                last_error = RuntimeError(f"[{m} | {key}] resp inesperada: {_json.dumps(resp, ensure_ascii=False)}")
    
        raise last_error if last_error else RuntimeError("tools/list: todos los intentos fallaron")
    
    def list_resources(self, timeout: float = 20.0, method_order=None, param_order=None) -> dict:
        import json as _json, time
        methods = method_order or ["resources/list", "resources.list"]
        variants = {
            "omit": None, "empty": {}, "cursor_null": {"cursor": None},
            "pagination_full": {"pagination": {"cursor": None, "limit": 50}}, "limit_50": {"limit": 50},
        }
        order = param_order or ["omit", "empty", "pagination_full", "cursor_null"]
        last_error = None
        for m in methods:
            for key in order:
                payload = {"jsonrpc":"2.0","id": self._next_id(),"method": m}
                p = variants[key]
                if p is not None:
                    payload["params"] = p
                self._send(payload)
                resp = _recv_frame(self.proc, timeout=timeout)
                if resp and "result" in resp and isinstance(resp["result"], dict) and "resources" in resp["result"]:
                    return resp
                if resp and "error" in resp:
                    last_error = RuntimeError(f"[{m} | {key}] error: {_json.dumps(resp['error'], ensure_ascii=False)}")
                    time.sleep(0.03)
                    continue
                last_error = RuntimeError(f"[{m} | {key}] resp inesperada: {_json.dumps(resp, ensure_ascii=False)}")
        raise last_error if last_error else RuntimeError("resources/list: todos fallaron")

    def read_resource(self, uri: str, timeout: float = 20.0) -> dict:
        for m in ("resources/read", "resources.read"):
            payload = {"jsonrpc":"2.0","id": self._next_id(),"method": m,"params": {"uri": uri}}
            self._send(payload)
            resp = _recv_frame(self.proc, timeout=timeout)
            if resp and "result" in resp:
                return resp
        raise RuntimeError("resources/read sin respuesta válida")

    def call_tool(self, name: str, arguments: dict, timeout: float = 30.0) -> dict:
        msg_id = self._next_id()
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        resp = _recv_frame(self.proc, timeout=timeout)
        if not resp:
            raise RuntimeError("tools/call sin respuesta")
        return resp
    
    def _send(self, obj: dict):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        if self.framing == "raw":
            # JSON en una línea + newline
            self.proc.stdin.write(data + b"\n")
            self.proc.stdin.flush()
        else:
            # LSP / Content-Length
            header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
            self.proc.stdin.write(header + data)
            self.proc.stdin.flush()
            
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

# Cliente HTTP MCP (robusto)
class HttpMCPConnection:
    def __init__(self, base_url: str, headers: Optional[dict] = None):
        self.base_url = base_url.rstrip("/")
        # Headers por defecto + opcionales
        base_headers = {"Content-Type": "application/json"}
        if headers:
            base_headers.update(headers)
        self._headers = base_headers
        self._session: Optional[aiohttp.ClientSession] = None
        self._connector: Optional[aiohttp.TCPConnector] = None

    async def _ensure(self):
        if self._session is None or getattr(self._session, "closed", False):
            # Un solo connector compartido
            if self._connector is None or self._connector.closed:
                self._connector = aiohttp.TCPConnector(keepalive_timeout=30, limit=50)
            timeout = aiohttp.ClientTimeout(total=30)
            default_headers = {"Content-Type": "application/json"}
            default_headers.update(self._headers)
            self._session = aiohttp.ClientSession(
                headers=default_headers,
                timeout=timeout,
                connector=self._connector,
            )

    async def _post(self, payload: dict, timeout: float):
        await self._ensure()
        async with self._session.post(self.base_url, json=payload, timeout=timeout) as r:
            # Levanta si no es 2xx
            r.raise_for_status()
            return await r.json()

    async def initialize(self, protocol_version: str = PROTOCOL_DEFAULT, client_info: Optional[dict] = None, timeout: float = 20.0):
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
        if getattr(self, "_connector", None) and not self._connector.closed:
            await self._connector.close()
            self._connector = None

class LibraryMCPConnection:
    """
    Envuelve stdio_client + ClientSession con la misma interfaz que
    ManualMCPConnection/HttpMCPConnection: initialize/list_tools/call_tool/close.
    """
    def __init__(self, command: str, args: List[str], cwd: Optional[str], env: Optional[dict]):
        self._params = StdioServerParameters(command=command, args=args, cwd=cwd, env=env or {})
        self._ctx = None
        self._read = None
        self._write = None
        self.session: Optional[ClientSession] = None

    async def initialize(self, protocol_version: str = PROTOCOL_DEFAULT, client_info: Optional[dict] = None, timeout: float = 10.0):
        self._ctx = stdio_client(self._params)
        self._read, self._write = await self._ctx.__aenter__()
        self.session = ClientSession(self._read, self._write)

        # Handshake tolerante a versiones + timeout
        try:
            await asyncio.wait_for(self.session.initialize(
                protocolVersion=protocol_version,
                capabilities={},
                clientInfo=client_info or {"name": "pokevgc-host", "version": "1.0.0"},
            ), timeout=timeout)
        except TypeError:
            # SDK viejo (sin kwargs)
            await asyncio.wait_for(self.session.initialize(), timeout=timeout)

        # Algunos servers requieren notification
        try:
            await self.session.initialized()
        except Exception:
            pass

    async def list_tools(self, timeout: float = 10.0) -> dict:
        tools_resp = await asyncio.wait_for(self.session.list_tools(), timeout=timeout)
        # Normaliza a dict tipo JSON-RPC como tu Manual/HTTP
        tools = []
        for t in tools_resp.tools or []:
            schema = getattr(t, "inputSchema", None)
            if hasattr(schema, "model_dump"):
                schema = schema.model_dump()
            elif hasattr(schema, "dict"):
                schema = schema.dict()
            tools.append({
                "name": t.name,
                "description": t.description or "",
                "inputSchema": schema or {"type": "object", "properties": {}, "required": []},
            })
        return {"result": {"tools": tools}}

    async def call_tool(self, name: str, arguments: dict, timeout: float = 30.0) -> dict:
        resp = await asyncio.wait_for(self.session.call_tool(name=name, arguments=arguments or {}), timeout=timeout)
        # Normaliza a bloques de contenido (como haces en SdkMCPConnection)
        blocks = []
        for b in getattr(resp, "content", []) or []:
            t = getattr(b, "text", None) or (isinstance(b, dict) and b.get("text"))
            if t is not None:
                blocks.append({"type": "text", "text": t})
            else:
                try:
                    blocks.append({"type": "text", "text": json.dumps(b, ensure_ascii=False)})
                except Exception:
                    blocks.append({"type": "text", "text": str(b)})
        return {"result": {"content": blocks}}

    async def close(self):
        # Cerrar session/ctx si existen
        if self.session:
            try:
                await self.session.close()
            except Exception:
                pass
            self.session = None
        if self._ctx:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._ctx = None
        self._read = None
        self._write = None

class MCPChatbot:
    """Chatbot principal que actúa como host MCP"""
    
    def __init__(self, anthropic_api_key: str, model: str, max_tokens: int):
        self.anthropic_client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.conversation_history: List[Dict[str, str]] = []
        self.mcp_servers: Dict[str, MCPServer] = {}
        self.active_sessions: Dict[str, Any] = {}
        self.available_tools: Dict[str, Any] = {}
        self.logger = MCPLogger()


    def _register_resources_as_tools(self, server_name: str, resources: list):
        # expone resources como dos tools genéricas
        self.available_tools[f"{server_name}_resources_list"] = {
            "name": "resources_list",
            "description": "Lista recursos del servidor MCP.",
            "server": server_name,
            "schema": {
                "type": "object",
                "properties": {
                    "cursor": {"type": ["string","null"]},
                    "limit": {"type": "integer", "default": 50}
                },
                "required": []
            }
        }
        self.available_tools[f"{server_name}_resource_read"] = {
            "name": "resource_read",
            "description": "Lee un recurso por URI.",
            "server": server_name,
            "schema": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string", "description": "URI del recurso a leer"}
                },
                "required": ["uri"]
            }
        }

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
        # ---------- Rama HTTP ----------
        if server.type == "http":
            base_url = server.base_url or (server.args[0] if server.args else "")
            bearer = os.getenv("MCP_REMOTE_HTTP_BEARER", "").strip()
            headers = server.headers or ({"Authorization": f"Bearer {bearer}"} if bearer else None)
        
            conn = HttpMCPConnection(base_url, headers=headers)
            try:
                proto = server.protocol_version or PROTOCOL_DEFAULT
                await conn.initialize(protocol_version=proto)
        
                tools_resp = await conn.list_tools()
                tools = (tools_resp or {}).get("result", {}).get("tools", []) or []
        
                # ÉXITO: recién aquí registramos la sesión
                self.active_sessions[name] = conn
        
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
        
            except Exception as e:
                try:
                    await conn.close()
                except Exception:
                    pass
                self.logger.log_interaction(name, "CONNECT_ERROR", str(e))
                raise

        # ---------- Rama STDIO ----------
        if server.transport == "library":
            if HOST_DEBUG:
                print(f"→ Lanzando (library): {server.command} {' '.join(server.args)}")
                if server.cwd:
                    print(f"  cwd: {server.cwd}")
                print("  Inicializando sesión MCP (library)...")

            conn = LibraryMCPConnection(server.command, server.args, cwd=server.cwd, env=server.env)
            proto = server.protocol_version or PROTOCOL_DEFAULT

            try:
                # initialize + list_tools con timeout
                await conn.initialize(protocol_version=proto)
                tools_resp = await conn.list_tools()
                tools = (tools_resp or {}).get("result", {}).get("tools", []) or []

                # Registrar herramientas
                self.active_sessions[name] = conn
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
                print(f"\n✅ Conectado a {etiqueta} ({server_tools} herramientas)")
                self.logger.log_interaction(name, "CONNECT", {
                    "status": "success",
                    "tools_count": server_tools,
                    "tools": [t.get("name") for t in tools],
                })
                return

            except Exception as e:
                if HOST_DEBUG:
                    print(f"  ⚠️ library falló ({type(e).__name__}: {e}). Fallback a framing manual...")

                # === FALLBACK: conexión manual LSP ===
                conn_m = ManualMCPConnection(
                    server.command,
                    server.args,
                    cwd=server.cwd,
                    env=server.env,
                    framing=server.framing,
                )
                loop = asyncio.get_running_loop()
                try:
                    await loop.run_in_executor(None, lambda: conn_m.initialize_modern(proto, timeout=10.0))
                except Exception:
                    await loop.run_in_executor(None, lambda: conn_m.initialize_modern("2024-11-05", timeout=10.0))

                # tools/list (con quirks)
                quirks = server.quirks or {}
                lt_methods = quirks.get("list_methods") or ["tools/list"]
                lt_params  = quirks.get("list_params_order") or ["omit", "empty"]

                tools_resp = await loop.run_in_executor(
                    None, lambda: conn_m.list_tools(timeout=45, method_order=lt_methods, param_order=lt_params)
                )
                tools = (tools_resp or {}).get("result", {}).get("tools", []) or []

                # Registrar herramientas
                self.active_sessions[name] = conn_m
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

                etiqueta = server.description or name
                print(f"\n✅ Conectado a {etiqueta} ({server_tools} herramientas) [fallback manual]")
                self.logger.log_interaction(name, "CONNECT", {
                    "status": "success_fallback",
                    "tools_count": server_tools,
                    "tools": [t.get("name") for t in tools],
                })
                return

        if HOST_DEBUG:
            print(f"→ Lanzando: {server.command} {' '.join(server.args)}")
            if server.cwd:
                print(f"  cwd: {server.cwd}")
            print("  Inicializando sesión MCP (manual framing)...")

        conn = ManualMCPConnection(
            server.command,
            server.args,
            cwd=server.cwd,
            env=server.env,
            framing=server.framing,
        )

        # Protocolo preferido del server (del config); si no, moderno
        proto = server.protocol_version or PROTOCOL_DEFAULT

        async def do_initialize(p: str):
            # IMPORTANTE: obtener el loop DENTRO de la función
            running_loop = asyncio.get_running_loop()
            if server.init == "skip":
                if HOST_DEBUG:
                    print("  (init=skip) Saltando initialize, probando tools/list directo...")
                return None
            if server.init == "legacy":
                return await running_loop.run_in_executor(
                    None, lambda: conn.initialize_legacy_minimal(protocol_version=p, timeout=10.0)
                )
            elif server.init == "compat":
                return await running_loop.run_in_executor(
                    None, lambda: conn.initialize_compat(protocol_version=p)
                )
            else:
                return await running_loop.run_in_executor(
                    None, lambda: conn.initialize_modern(protocol_version=p, timeout=10.0)
                )

        # Initialize con fallback de protocolo
        try:
            await do_initialize(proto)
            if HOST_DEBUG and server.init != "skip":
                print("  ✓ Sesión inicializada")
        except Exception:
            alt = "2024-11-05" if proto != "2024-11-05" else PROTOCOL_DEFAULT
            if HOST_DEBUG:
                print(f"  ⚠️ initialize falló con {proto}, probando {alt}...")
            try:
                await do_initialize(alt)
                if HOST_DEBUG and server.init != "skip":
                    print("  ✓ Sesión inicializada (fallback)")
            except Exception as init_err:
                if HOST_DEBUG:
                    err_snip = conn.read_stderr_snapshot() or ""
                    print("  ❌ Falló initialize, intento fallback a tools/list")
                    if err_snip.strip():
                        print("  [stderr del servidor]\n" + err_snip[-4000:])
                # NO salimos: seguimos a tools/list como último recurso

        # tools/list
        if HOST_DEBUG:
            print("  Obteniendo herramientas (manual framing)...")

        loop = asyncio.get_running_loop()

        try:
            quirks = server.quirks or {}
            lt_methods = quirks.get("list_methods")          # p.ej ["tools/list"] solo
            lt_params  = quirks.get("list_params_order")     # p.ej ["omit","empty"]

            tools_resp = await loop.run_in_executor(
                None, lambda: conn.list_tools(timeout=45, method_order=lt_methods, param_order=lt_params)
            )
            tools = (tools_resp or {}).get("result", {}).get("tools", []) or []

        except Exception as tools_err:
            if HOST_DEBUG:
                print(f"  ⚠️ tools/list falló: {tools_err}\n  → Probando resources/list como fallback...")
            try:
                res_resp = await loop.run_in_executor(None, lambda: conn.list_resources(timeout=45))
                resources = (res_resp or {}).get("result", {}).get("resources", []) or []

                # Guardar conexión y registrar herramientas sintéticas
                self.active_sessions[name] = conn
                self._register_resources_as_tools(name, resources)

                if HOST_DEBUG:
                    err = conn.read_stderr_snapshot()
                    if err.strip():
                        print("\n[stderr del servidor]")
                        print(err)

                etiqueta = server.description or name
                print(f"\n✅ Conectado a {etiqueta} (resources mode: {len(resources)} recursos)")
                self.logger.log_interaction(name, "CONNECT", {
                    "status": "success",
                    "tools_count": 0,
                    "resources_count": len(resources),
                })
                return

            except Exception as res_err:
                raise RuntimeError(f"No se pudo listar tools ni resources.\n tools/list err: {tools_err}\n resources/list err: {res_err}")

        if HOST_DEBUG:
            print(f"  ✓ Herramientas obtenidas: {len(tools)}")
            err = conn.read_stderr_snapshot()
            if err.strip():
                print("\n[stderr del servidor]")
                print(err)

        # Guardar conexión
        self.active_sessions[name] = conn

        # Registrar herramientas normales
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

        self.logger.log_interaction(name, "CONNECT", {
            "status": "success",
            "tools_count": server_tools,
            "tools": [t.get("name") for t in tools],
        })

        etiqueta = server.description or name
        print(f"\n✅ Conectado a {etiqueta} ({server_tools} herramientas)")
    
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
        """Llama a una herramienta del servidor (HTTP/SDK/Manual según corresponda)"""
        try:
            if server_name not in self.active_sessions:
                raise Exception(f"No hay sesión activa para el servidor '{server_name}'")

            conn = self.active_sessions[server_name]

            # --- Herramientas sintéticas de resources ---
            if tool_name in ("resources_list", "resource_read"):
                try:
                    if tool_name == "resources_list":
                        resp = await asyncio.get_running_loop().run_in_executor(
                            None, lambda: conn.list_resources(timeout=35)
                        )
                        self.logger.log_interaction(server_name, "TOOL_RESPONSE", {"tool": tool_name, "result": str(resp)[:500]})
                        return resp
                    else:  # resource_read
                        uri = (arguments or {}).get("uri")
                        if not uri:
                            return {"error": "Falta argumento obligatorio: 'uri'"}
                        resp = await asyncio.get_running_loop().run_in_executor(
                            None, lambda: conn.read_resource(uri, timeout=35)
                        )
                        self.logger.log_interaction(server_name, "TOOL_RESPONSE", {"tool": tool_name, "result": str(resp)[:500]})
                        return resp
                except Exception as e:
                    error_msg = f"❌ Error en {tool_name}: {str(e)}"
                    print(error_msg)
                    self.logger.log_interaction(server_name, "TOOL_ERROR", error_msg)
                    return {"error": error_msg}
                
            async def _do_call():
                if isinstance(conn, HttpMCPConnection):
                    return await conn.call_tool(tool_name, arguments, timeout=30)
                if isinstance(conn, LibraryMCPConnection):
                    return await conn.call_tool(tool_name, arguments, timeout=30)
                if isinstance(conn, SdkMCPConnection):
                    return await conn.call_tool(tool_name, arguments)
                # Manual
                return await asyncio.get_running_loop().run_in_executor(
                    None, lambda: conn.call_tool(tool_name, arguments)
                )

            # Log a archivo
            self.logger.log_interaction(
                server_name, "TOOL_CALL",
                {"tool": tool_name, "arguments": arguments}
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
        tool_results = []

        for tool_call in tool_calls:
            tool_name = tool_call.name
            tool_input = tool_call.input

            # Resolver nombre saneado -> (server, tool real)
            if hasattr(self, "tool_name_map") and tool_name in self.tool_name_map:
                server_name = self.tool_name_map[tool_name]["server"]
                actual_tool_name = self.tool_name_map[tool_name]["name"]
            elif tool_name in self.available_tools:
                info = self.available_tools[tool_name]
                server_name = info["server"]
                actual_tool_name = info["name"]
            else:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "content": [{"type": "text", "text": f"Error: Herramienta '{tool_name}' no encontrada"}]
                })
                continue

            # Llamada al MCP
            mcp_resp = await self.call_mcp_tool(server_name, actual_tool_name, tool_input)

            # --- DES-EMPACADO CORRECTO ---
            content_blocks = None
            if isinstance(mcp_resp, dict):
                inner = mcp_resp.get("result")
                if isinstance(inner, dict) and "content" in inner:
                    # Ya vienen como content blocks MCP -> reutilízalos tal cual
                    content_blocks = inner["content"]

            if not content_blocks:
                # Fallback: convierte todo a texto y devuélvelo como bloque de texto
                text = json.dumps(mcp_resp, ensure_ascii=False, indent=2) if not isinstance(mcp_resp, str) else mcp_resp
                content_blocks = [{"type": "text", "text": text}]

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_call.id,
                "content": content_blocks
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
            MODEL = self.model
            MAX_TOKENS = self.max_tokens

            response = self.anthropic_client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system="Eres Prof. Oak, un mentor amable de VGC. Responde de forma breve, clara y útil. Usa herramientas cuando aporten.",
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
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
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
        if not self.active_sessions:
            return
            
        print("\n🔌 Desconectando servidores MCP...\n")
        
        # Cierra sesiones
        for name, conn in list(self.active_sessions.items()):
            try:
                if isinstance(conn, (HttpMCPConnection, SdkMCPConnection, LibraryMCPConnection)):
                    await conn.close()
                elif isinstance(conn, ManualMCPConnection):
                    conn.close()
                else:
                    # fallback: intenta await si existe
                    close_fn = getattr(conn, "close", None)
                    if asyncio.iscoroutinefunction(close_fn):
                        await close_fn()
                    elif callable(close_fn):
                        close_fn()

                print(f"✓ Desconectado de '{name}'\n")
            except Exception as e:
                print(f"⚠️  Error desconectando '{name}': {str(e)}")        
        self.active_sessions.clear()

async def init_with_retries(session, proto="2025-06-18"):
    tried = []
    async def _try(**kwargs):
        tried.append(kwargs)
        return await session.initialize(**kwargs)

    # 1) moderno
    try: return await _try(protocolVersion=proto, capabilities={}, clientInfo={"name":"host","version":"1.0"})
    except TypeError: pass
    # 2) snake_case
    try: return await _try(protocol_version=proto, capabilities={}, client_info={"name":"host","version":"1.0"})
    except TypeError: pass
    # 3) sin protocolo
    try: return await _try(capabilities={}, clientInfo={"name":"host","version":"1.0"})
    except TypeError: pass
    # 4) sin kwargs
    return await session.initialize()


async def main():
    """Función principal del chatbot"""
    load_dotenv()
    cfg = load_config(os.getenv("HOST_CONFIG", "src/host/config.json"))

    print("\n🚀 Poke VGC — MCP Host")
    print("=" * 60)
    
    # Solicitar API key de Anthropic
    api_key = (cfg.get("anthropic", {}) or {}).get("api_key") or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        api_key = input("🔑 Ingresa tu API key de Anthropic: ").strip()
        if not api_key:
            print("❌ API key requerida\n")
            return
    
    # Crear chatbot
    model = (cfg.get("anthropic", {}) or {}).get("model") or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    max_tokens = (cfg.get("anthropic", {}) or {}).get("max_tokens", 4000)
    chatbot = MCPChatbot(api_key, model=model, max_tokens=max_tokens)
    
    # Configuración del servidor MCP personalizado desde .env
    print("\n⚙️  Configurando servidores MCP...\n")
    
    for entry in cfg.get("mcp_servers", []):
        if not entry.get("enabled", False):
            continue

        stype = entry.get("type", "stdio")
        protocol = entry.get("protocol") or (PROTOCOL_DEFAULT)
        init_kind = entry.get("init", "modern")
        env_map = entry.get("env", {}) or {}
        framing = entry.get("framing", "lsp")

        if stype == "http":
            server = MCPServer(
                name=entry["name"],
                command="HTTP",
                args=[entry["base_url"]],
                description=entry.get("description", ""),
                protocol_version=protocol,
                type="http",
                init=init_kind,
                headers=entry.get("headers", {}),
                enabled=True,
                framing=framing
            )
        else:
            server = MCPServer(
                name=entry["name"],
                command=entry["command"],
                args=entry.get("args", []),
                cwd=entry.get("cwd"),
                description=entry.get("description", ""),
                protocol_version=protocol,
                type="stdio",
                init=init_kind,
                env=env_map,
                enabled=True,
                framing=framing,
                quirks=entry.get("quirks", {}),
                transport=entry.get("transport", "manual")
            )

        chatbot.add_mcp_server(server)

    # Registrar servidor MCP por HTTP si hay URL en .env
    remote_http_url = os.getenv("MCP_REMOTE_HTTP_URL", "").strip()
    if remote_http_url:
        remote_name = os.getenv("MCP_REMOTE_HTTP_NAME", "VGC HTTP Remote").strip()
        http_server = MCPServer(
            name=remote_name,
            command="HTTP",
            args=[remote_http_url],
            cwd=None,
            description="Servidor MCP remoto (HTTP)",
            type="http"
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