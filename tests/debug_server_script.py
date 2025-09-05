#!/usr/bin/env python3
"""
Test de diagnóstico para MCP - Simula exactamente lo que hace tu host
"""
import asyncio
import json
import logging
import subprocess
import sys
import os
from datetime import datetime

# Configurar logging detallado
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

async def test_mcp_connection():
    """Test completo de conexión MCP paso a paso"""
    
    # Configuración del servidor (ajusta estas rutas)
    cmd = "C:/Users/Andy Ortega/Progras/Redes/MCP-PokeVGC-Teambuilder/.venv/Scripts/python.exe"
    args = ["-u", "-m", "server.main"]
    cwd = "C:/Users/Andy Ortega/Progras/Redes/MCP-PokeVGC-Teambuilder"
    
    print("=" * 60)
    print("🧪 TEST DE DIAGNÓSTICO MCP")
    print("=" * 60)
    print(f"Comando: {cmd}")
    print(f"Args: {' '.join(args)}")
    print(f"CWD: {cwd}")
    print()
    
    # Configurar entorno
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if cwd:
        env["PYTHONPATH"] = cwd
    
    process = None
    try:
        # Paso 1: Lanzar proceso
        print("1️⃣ Lanzando proceso servidor...")
        process = await asyncio.create_subprocess_exec(
            cmd, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env
        )
        print(f"   ✓ Proceso lanzado (PID: {process.pid})")
        
        # Dar tiempo al servidor para iniciar
        await asyncio.sleep(2)
        
        # Verificar que el proceso sigue vivo
        if process.returncode is not None:
            print(f"   ❌ Proceso terminó prematuramente (código: {process.returncode})")
            stderr = await process.stderr.read()
            print(f"   Error: {stderr.decode()}")
            return
        
        print("   ✓ Proceso activo")
        
        # Paso 2: Enviar initialize
        print("\n2️⃣ Enviando mensaje initialize...")
        init_msg = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "debug-test", "version": "1.0"}
            },
            "id": 1
        }
        
        init_json = json.dumps(init_msg) + "\n"
        print(f"   Enviando: {init_json.strip()}")
        
        process.stdin.write(init_json.encode())
        await process.stdin.drain()
        
        # Leer respuesta con timeout
        try:
            response_bytes = await asyncio.wait_for(
                process.stdout.readline(), 
                timeout=10
            )
            response_str = response_bytes.decode().strip()
            print(f"   ✓ Respuesta recibida: {response_str}")
            
            # Parsear respuesta
            try:
                response_json = json.loads(response_str)
                if response_json.get("id") == 1 and "result" in response_json:
                    print("   ✓ Initialize exitoso")
                else:
                    print(f"   ❌ Respuesta inesperada: {response_json}")
                    return
            except json.JSONDecodeError as e:
                print(f"   ❌ Error parseando JSON: {e}")
                return
                
        except asyncio.TimeoutError:
            print("   ❌ Timeout esperando respuesta de initialize")
            return
        
        # Paso 3: Enviar initialized notification
        print("\n3️⃣ Enviando notification initialized...")
        initialized_msg = {
            "jsonrpc": "2.0",
            "method": "initialized",
            "params": {}
        }
        
        initialized_json = json.dumps(initialized_msg) + "\n"
        print(f"   Enviando: {initialized_json.strip()}")
        
        process.stdin.write(initialized_json.encode())
        await process.stdin.drain()
        
        # Pausa breve (las notificaciones no tienen respuesta)
        await asyncio.sleep(0.5)
        print("   ✓ Notification enviada")
        
        # Paso 4: Enviar tools/list
        print("\n4️⃣ Enviando tools/list...")
        tools_msg = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "params": {},
            "id": 2
        }
        
        tools_json = json.dumps(tools_msg) + "\n"
        print(f"   Enviando: {tools_json.strip()}")
        
        process.stdin.write(tools_json.encode())
        await process.stdin.drain()
        
        # Leer respuesta
        try:
            tools_response = await asyncio.wait_for(
                process.stdout.readline(), 
                timeout=10
            )
            tools_str = tools_response.decode().strip()
            print(f"   ✓ Respuesta recibida: {tools_str[:200]}...")
            
            try:
                tools_json = json.loads(tools_str)
                if tools_json.get("id") == 2 and "result" in tools_json:
                    tools_count = len(tools_json["result"].get("tools", []))
                    print(f"   ✓ Tools/list exitoso - {tools_count} herramientas")
                else:
                    print(f"   ❌ Respuesta inesperada: {tools_json}")
                    return
            except json.JSONDecodeError as e:
                print(f"   ❌ Error parseando JSON: {e}")
                return
                
        except asyncio.TimeoutError:
            print("   ❌ Timeout esperando respuesta de tools/list")
            return
        
        # Paso 5: Test de herramienta
        print("\n5️⃣ Probando herramienta suggest_team...")
        suggest_msg = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "suggest_team",
                "arguments": {
                    "format": "vgc2022",
                    "playstyle": "trick_room",
                    "constraints": {
                        "strategy": {
                            "trick_room": True
                        }
                    }
                }
            },
            "id": 3
        }
        
        suggest_json = json.dumps(suggest_msg) + "\n"
        print(f"   Enviando: {suggest_json[:100]}...")
        
        process.stdin.write(suggest_json.encode())
        await process.stdin.drain()
        
        try:
            suggest_response = await asyncio.wait_for(
                process.stdout.readline(), 
                timeout=15
            )
            suggest_str = suggest_response.decode().strip()
            print(f"   ✓ Respuesta recibida: {suggest_str[:200]}...")
            
            try:
                suggest_json = json.loads(suggest_str)
                if suggest_json.get("id") == 3 and "result" in suggest_json:
                    print("   ✓ Herramienta ejecutada exitosamente")
                else:
                    print(f"   ❌ Error en herramienta: {suggest_json}")
            except json.JSONDecodeError as e:
                print(f"   ❌ Error parseando JSON: {e}")
                
        except asyncio.TimeoutError:
            print("   ❌ Timeout esperando respuesta de herramienta")
            return
        
        print("\n🎉 TODOS LOS TESTS PASARON - EL SERVIDOR MCP FUNCIONA CORRECTAMENTE")
        print("El problema está en el código del host, no en el servidor.")
        
    except Exception as e:
        print(f"❌ Error durante el test: {e}")
        logger.exception("Error completo:")
        
    finally:
        # Limpiar proceso
        if process and process.returncode is None:
            print(f"\n🧹 Cerrando proceso {process.pid}...")
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=5)
            except:
                process.kill()
            print("   ✓ Proceso cerrado")

async def test_stderr_output():
    """Test adicional para ver qué sale por stderr"""
    cmd = "C:/Users/Andy Ortega/Progras/Redes/MCP-PokeVGC-Teambuilder/.venv/Scripts/python.exe"
    args = ["-u", "-m", "server.main"]
    cwd = "C:/Users/Andy Ortega/Progras/Redes/MCP-PokeVGC-Teambuilder"
    
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if cwd:
        env["PYTHONPATH"] = cwd
    
    print("\n" + "=" * 60)
    print("🔍 CAPTURANDO STDERR DEL SERVIDOR")
    print("=" * 60)
    
    try:
        process = await asyncio.create_subprocess_exec(
            cmd, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env
        )
        
        # Enviar un mensaje simple
        init_msg = json.dumps({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test"}},
            "id": 1
        }) + "\n"
        
        process.stdin.write(init_msg.encode())
        await process.stdin.drain()
        
        # Leer stderr durante 3 segundos
        try:
            stderr_data = await asyncio.wait_for(process.stderr.read(1024), timeout=3)
            if stderr_data:
                print("STDERR del servidor:")
                print(stderr_data.decode())
            else:
                print("No hay output en stderr")
        except asyncio.TimeoutError:
            print("No hay output en stderr (timeout)")
            
        process.terminate()
        await process.wait()
        
    except Exception as e:
        print(f"Error capturando stderr: {e}")

if __name__ == "__main__":
    print("Ejecutando tests de diagnóstico MCP...")
    asyncio.run(test_mcp_connection())
    asyncio.run(test_stderr_output())