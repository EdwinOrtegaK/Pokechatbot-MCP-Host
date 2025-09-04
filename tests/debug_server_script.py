#!/usr/bin/env python3
"""
Script para diagnosticar problemas con el servidor MCP de Pokémon VGC
"""

import subprocess
import sys
import os
import time
import json
from pathlib import Path

def test_1_basic_paths():
    """Prueba 1: Verificar que todas las rutas básicas existen"""
    print("🔍 PRUEBA 1: Verificando rutas básicas...")
    
    server_path = "C:/Users/Andy Ortega/Progras/Redes/MCP-PokeVGC-Teambuilder"
    python_exe = f"{server_path}/.venv/Scripts/python.exe"
    main_file = os.path.join(server_path, "server", "main.py")
    
    checks = [
        (server_path, "Directorio del servidor"),
        (python_exe, "Ejecutable de Python"),
        (main_file, "Archivo server/main.py"),
    ]
    
    all_ok = True
    for path, description in checks:
        if os.path.exists(path):
            print(f"✅ {description}: {path}")
        else:
            print(f"❌ {description}: {path} (NO EXISTE)")
            all_ok = False
    
    return all_ok

def test_2_python_environment():
    """Prueba 2: Verificar el entorno de Python y dependencias"""
    print("\n🔍 PRUEBA 2: Verificando entorno de Python...")
    
    server_path = "C:/Users/Andy Ortega/Progras/Redes/MCP-PokeVGC-Teambuilder"
    python_exe = f"{server_path}/.venv/Scripts/python.exe"
    
    if not os.path.exists(python_exe):
        print("❌ Python no existe, saltando prueba")
        return False
    
    # Verificar que Python funciona
    try:
        result = subprocess.run(
            [python_exe, "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            print(f"✅ Python funciona: {result.stdout.strip()}")
        else:
            print(f"❌ Python falló: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ Error ejecutando Python: {e}")
        return False
    
    # Verificar dependencias MCP
    try:
        result = subprocess.run(
            [python_exe, "-c", "import mcp; print('MCP OK')"],
            cwd=server_path,
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            print("✅ Librería MCP disponible")
        else:
            print(f"❌ Librería MCP no disponible: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ Error verificando MCP: {e}")
        return False
    
    return True

def test_3_server_syntax():
    """Prueba 3: Verificar que el servidor no tiene errores de sintaxis"""
    print("\n🔍 PRUEBA 3: Verificando sintaxis del servidor...")
    
    server_path = "C:/Users/Andy Ortega/Progras/Redes/MCP-PokeVGC-Teambuilder"
    python_exe = f"{server_path}/.venv/Scripts/python.exe"
    
    try:
        result = subprocess.run(
            [python_exe, "-m", "py_compile", "server/main.py"],
            cwd=server_path,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            print("✅ Sintaxis del servidor correcta")
            return True
        else:
            print(f"❌ Error de sintaxis: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ Error verificando sintaxis: {e}")
        return False

def test_4_server_startup():
    """Prueba 4: Verificar que el servidor puede iniciarse"""
    print("\n🔍 PRUEBA 4: Probando inicio del servidor...")
    
    server_path = "C:/Users/Andy Ortega/Progras/Redes/MCP-PokeVGC-Teambuilder"
    python_exe = f"{server_path}/.venv/Scripts/python.exe"
    
    try:
        print("  Iniciando servidor (máximo 10 segundos)...")
        process = subprocess.Popen(
            [python_exe, "-u", "-m", "server.main"],
            cwd=server_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=0  # Sin buffer para ver output inmediato
        )
        
        # Esperar un poco para ver si se inicia
        time.sleep(5)
        
        if process.poll() is None:
            print("✅ El servidor se inició correctamente y sigue ejecutándose")
            
            # Intentar terminarlo graciosamente
            process.terminate()
            try:
                process.wait(timeout=5)
                print("✅ Servidor terminado correctamente")
            except subprocess.TimeoutExpired:
                process.kill()
                print("⚠️ Servidor forzado a terminar")
            
            return True
        else:
            # El proceso ya terminó
            stdout, stderr = process.communicate()
            print("❌ El servidor terminó inmediatamente")
            print(f"CÓDIGO DE SALIDA: {process.returncode}")
            if stdout:
                print(f"STDOUT:\n{stdout}")
            if stderr:
                print(f"STDERR:\n{stderr}")
            return False
            
    except Exception as e:
        print(f"❌ Error ejecutando servidor: {e}")
        return False

def test_5_server_output():
    """Prueba 5: Capturar y analizar output del servidor"""
    print("\n🔍 PRUEBA 5: Analizando output del servidor...")
    
    server_path = "C:/Users/Andy Ortega/Progras/Redes/MCP-PokeVGC-Teambuilder"
    python_exe = f"{server_path}/.venv/Scripts/python.exe"
    
    try:
        print("  Capturando output del servidor...")
        result = subprocess.run(
            [python_exe, "-u", "-m", "server.main"],
            cwd=server_path,
            capture_output=True,
            text=True,
            timeout=8  # Timeout corto para capturar output inicial
        )
        
        print(f"CÓDIGO DE RETORNO: {result.returncode}")
        
        if result.stdout:
            print("STDOUT del servidor:")
            print("-" * 40)
            print(result.stdout)
            print("-" * 40)
        
        if result.stderr:
            print("STDERR del servidor:")
            print("-" * 40)
            print(result.stderr)
            print("-" * 40)
        
        return True
        
    except subprocess.TimeoutExpired as e:
        print("⚠️ El servidor no terminó en 8 segundos (esto podría ser normal para servidores MCP)")
        if e.stdout:
            print("STDOUT capturado:")
            print("-" * 40)
            print(e.stdout.decode() if isinstance(e.stdout, bytes) else e.stdout)
            print("-" * 40)
        if e.stderr:
            print("STDERR capturado:")
            print("-" * 40)
            print(e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr)
            print("-" * 40)
        return True
    except Exception as e:
        print(f"❌ Error capturando output: {e}")
        return False

def test_6_manual_mcp_test():
    """Prueba 6: Sugerencias para prueba manual"""
    print("\n🔍 PRUEBA 6: Instrucciones para prueba manual...")
    
    server_path = "C:/Users/Andy Ortega/Progras/Redes/MCP-PokeVGC-Teambuilder"
    python_exe = f"{server_path}/.venv/Scripts/python.exe"
    
    print("Para probar manualmente el servidor, ejecuta estos comandos:")
    print()
    print("1. Abrir terminal y navegar al directorio:")
    print(f'   cd "{server_path}"')
    print()
    print("2. Ejecutar el servidor:")
    print(f'   "{python_exe}" -u -m server.main')
    print()
    print("3. El servidor debería:")
    print("   - NO mostrar errores")
    print("   - Quedarse ejecutándose (no terminar inmediatamente)")
    print("   - Posiblemente mostrar mensajes de inicialización")
    print()
    print("4. Para terminar el servidor manualmente, usa Ctrl+C")

def analyze_server_main():
    """Analizar el contenido de server/main.py"""
    print("\n🔍 ANÁLISIS: Contenido de server/main.py...")
    
    main_file = "C:/Users/Andy Ortega/Progras/Redes/MCP-PokeVGC-Teambuilder/server/main.py"
    
    if not os.path.exists(main_file):
        print("❌ server/main.py no existe")
        return
    
    try:
        with open(main_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        print(f"📄 Tamaño del archivo: {len(content)} caracteres")
        
        # Verificar elementos básicos de MCP
        checks = [
            ("import mcp", "Importa librería MCP"),
            ("from mcp", "Importa desde MCP"),
            ("@app.list_tools", "Decorador list_tools"),
            ("@app.call_tool", "Decorador call_tool"),
            ("async def", "Funciones asíncronas"),
            ("app.run()", "Llamada a app.run()"),
            ("if __name__", "Bloque main"),
        ]
        
        print("\nElementos encontrados:")
        for pattern, description in checks:
            if pattern in content:
                print(f"✅ {description}")
            else:
                print(f"❌ {description} - Patrón '{pattern}' no encontrado")
        
        # Mostrar las primeras líneas
        lines = content.split('\n')
        print(f"\nPrimeras 10 líneas del archivo:")
        print("-" * 40)
        for i, line in enumerate(lines[:10], 1):
            print(f"{i:2d}: {line}")
        print("-" * 40)
        
    except Exception as e:
        print(f"❌ Error leyendo server/main.py: {e}")

def main():
    """Ejecutar todas las pruebas de diagnóstico"""
    print("🚀 DIAGNÓSTICO COMPLETO DEL SERVIDOR MCP POKÉMON VGC")
    print("=" * 65)
    
    tests = [
        test_1_basic_paths,
        test_2_python_environment,
        test_3_server_syntax,
        test_4_server_startup,
        test_5_server_output,
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"❌ Error en prueba: {e}")
            results.append(False)
    
    # Análisis adicional
    analyze_server_main()
    test_6_manual_mcp_test()
    
    print("\n" + "=" * 65)
    print("📊 RESUMEN DE RESULTADOS:")
    passed = sum(results)
    total = len(results)
    print(f"   Pruebas pasadas: {passed}/{total}")
    
    if passed == total:
        print("✅ Todas las pruebas básicas pasaron")
        print("   El problema podría ser en el protocolo MCP o timing")
    else:
        print("❌ Hay problemas básicos que deben resolverse primero")
    
    print("\n🔧 PRÓXIMOS PASOS RECOMENDADOS:")
    print("1. Ejecuta la prueba manual (Prueba 6)")
    print("2. Si el servidor falla manualmente, revisa server/main.py")
    print("3. Si el servidor funciona manualmente, el problema es en el cliente MCP")

if __name__ == "__main__":
    main()