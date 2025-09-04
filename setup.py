#!/usr/bin/env python3
import os
import sys
import subprocess
import json
from pathlib import Path

def install_dependencies():
    """Instala las dependencias necesarias"""
    print(" Instalando dependencias...")
    
    # Instalar dependencias de Python
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    
    # Verificar que Node.js esté instalado para servidores MCP oficiales
    try:
        subprocess.check_call(["node", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(" Node.js encontrado")
        
        # Instalar servidores MCP oficiales
        print(" Instalando servidores MCP oficiales...")
        subprocess.check_call(["npx", "-y", "@modelcontextprotocol/server-filesystem", "--help"], 
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(" Servidor filesystem MCP disponible")
        
        subprocess.check_call(["npx", "-y", "@modelcontextprotocol/server-git", "--help"], 
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(" Servidor git MCP disponible")
        
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("  Node.js no encontrado. Los servidores MCP oficiales no estarán disponibles.")
        print("   Para instalar Node.js: https://nodejs.org/")

def create_config_files():
    """Crea archivos de configuración si no existen"""
    print("  Configurando archivos...")
    
    # Crear .env si no existe
    if not os.path.exists('.env'):
        if os.path.exists('.env.example'):
            with open('.env.example', 'r') as example:
                content = example.read()
        else:
            # Crear contenido básico si no existe .env.example
            content = """# Configuración de Anthropic
ANTHROPIC_API_KEY=tu-api-key-aqui

# Configuración de logging
LOG_LEVEL=INFO
LOG_FILE=mcp_interactions.log

# Configuración de servidores MCP personalizados
CUSTOM_MCP_SERVER_PATH=./services/poke_sprites_remote/server/app.py
CUSTOM_MCP_SERVER_ARGS=

# Configuración opcional
MAX_CONVERSATION_HISTORY=50
MAX_TOKENS=4000"""
        
        with open('.env', 'w') as env_file:
            env_file.write(content)
        
        print(" Archivo .env creado - ¡Recuerda configurar tu API key!")
    
    # Crear directorio de logs
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    print("Directorio de logs creado")
    
    # Verificar que el directorio de servidores MCP exista
    if Path("services").exists():
        print(" Directorio de servicios encontrado")
    else:
        print("  No se encontró directorio 'services' - asegúrate de tener tus servidores MCP")

def get_anthropic_api_key():
    """Solicita y configura la API key de Anthropic"""
    print("\n CONFIGURACIÓN DE ANTHROPIC API")
    print("=" * 40)
    print("1. Ve a https://console.anthropic.com/")
    print("2. Crea una cuenta si no tienes una")
    print("3. Ve a la sección de API Keys")
    print("4. Crea una nueva API key")
    print("5. Anthropic te da $5 USD gratis para empezar")
    print()
    
    api_key = input("Ingresa tu API key de Anthropic (o presiona Enter para configurar después): ").strip()
    
    if api_key:
        # Actualizar .env con la API key
        env_content = ""
        if os.path.exists('.env'):
            with open('.env', 'r') as f:
                for line in f:
                    if line.startswith('ANTHROPIC_API_KEY='):
                        env_content += f"ANTHROPIC_API_KEY={api_key}\n"
                    else:
                        env_content += line
        else:
            env_content = f"ANTHROPIC_API_KEY={api_key}\n"
        
        with open('.env', 'w') as f:
            f.write(env_content)
        
        print(" API key configurada en .env")
    else:
        print("  Recuerda configurar tu API key en el archivo .env antes de usar el chatbot")

def create_example_mcp_server():
    """Crea un servidor MCP de ejemplo si no existe uno personalizado"""
    print("\n Verificando servidores MCP...")
    
    # Verificar si ya existe un servidor personalizado
    if Path("services/poke_sprites_remote/server/app.py").exists():
        print(" Servidor MCP personalizado encontrado")
        return
    
    # Si no existe, crear uno de ejemplo
    print(" Creando servidor MCP de ejemplo...")
    
    example_server_code = '''#!/usr/bin/env python3
"""
Servidor MCP de ejemplo - Calculadora
Este es un ejemplo de servidor MCP personalizado
"""

import asyncio
import json
import sys
from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions, Server
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource
import mcp.types as types
from pydantic import AnyUrl
import mcp.server.stdio

# Crear servidor
server = Server("example-calculator")

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Lista las herramientas disponibles"""
    return [
        types.Tool(
            name="calculate",
            description="Realiza operaciones matemáticas básicas",
            inputSchema={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Expresión matemática a evaluar (ej: 2 + 2, 10 * 5)"
                    }
                },
                "required": ["expression"]
            }
        ),
        types.Tool(
            name="factorial",
            description="Calcula el factorial de un número",
            inputSchema={
                "type": "object",
                "properties": {
                    "number": {
                        "type": "integer",
                        "description": "Número para calcular factorial",
                        "minimum": 0,
                        "maximum": 20
                    }
                },
                "required": ["number"]
            }
        )
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """Maneja las llamadas a herramientas"""
    
    if name == "calculate":
        try:
            expression = arguments.get("expression", "")
            # Evaluar expresión matemática de forma segura
            allowed_chars = set('0123456789+-*/.() ')
            if not all(c in allowed_chars for c in expression):
                raise ValueError("Expresión contiene caracteres no permitidos")
            
            result = eval(expression)
            return [types.TextContent(type="text", text=f"Resultado: {result}")]
        
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {str(e)}")]
    
    elif name == "factorial":
        try:
            number = arguments.get("number", 0)
            if number < 0:
                raise ValueError("El factorial no está definido para números negativos")
            if number > 20:
                raise ValueError("Número demasiado grande")
            
            # Calcular factorial
            factorial = 1
            for i in range(1, number + 1):
                factorial *= i
            
            return [types.TextContent(type="text", text=f"Factorial de {number}: {factorial}")]
        
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {str(e)}")]
    
    else:
        raise ValueError(f"Herramienta desconocida: {name}")

async def main():
    # Ejecutar servidor MCP
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="example-calculator",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
'''
    
    # Crear directorio de ejemplo si no existe
    example_dir = Path("examples") / "mcp_servers"
    example_dir.mkdir(parents=True, exist_ok=True)
    
    example_path = example_dir / "example_calculator_server.py"
    with open(example_path, 'w') as f:
        f.write(example_server_code)
    
    # Hacer ejecutable
    os.chmod(example_path, 0o755)
    print(f" Servidor MCP de ejemplo creado: {example_path}")

def verify_project_structure():
    """Verifica la estructura del proyecto"""
    print("\n  Verificando estructura del proyecto...")
    
    required_dirs = [
        "src/host",
        "src/utils", 
        "services"
    ]
    
    required_files = [
        "src/host/cli.py",
        "requirements.txt"
    ]
    
    for dir_path in required_dirs:
        if Path(dir_path).exists():
            print(f" Directorio encontrado: {dir_path}")
        else:
            print(f"  Directorio no encontrado: {dir_path}")
    
    for file_path in required_files:
        if Path(file_path).exists():
            print(f" Archivo encontrado: {file_path}")
        else:
            print(f" Archivo faltante: {file_path}")

def show_next_steps():
    """Muestra los próximos pasos"""
    print("\n PRÓXIMOS PASOS:")
    print("=" * 30)
    print("1. Asegúrate de que src/host/cli.py tenga el código del chatbot")
    print("2. Configura tu API key en .env:")
    print("   ANTHROPIC_API_KEY=tu-api-key-real")
    print("3. Si tienes un servidor MCP personalizado, asegúrate de que esté en services/")
    print("4. Ejecuta el chatbot:")
    print("   cd src/host")
    print("   python cli.py")
    print("\n Archivos importantes:")
    print("   - .env: Configuración de API keys")
    print("   - src/host/config.json: Configuración de servidores MCP")
    print("   - logs/: Logs de interacciones MCP")

def main():
    """Función principal de configuración"""
    print(" CONFIGURADOR MCP CHATBOT")
    print("Universidad del Valle de Guatemala - CC3067 Redes")
    print("=" * 60)
    
    try:
        # Verificar Python version
        if sys.version_info < (3, 8):
            print("Python 3.8+ requerido")
            sys.exit(1)
        
        print(f"Python {sys.version_info.major}.{sys.version_info.minor} detectado")
        
        # Verificar estructura del proyecto
        verify_project_structure()
        
        # Instalar dependencias
        install_dependencies()
        
        # Crear archivos de configuración
        create_config_files()
        
        # Configurar API key
        get_anthropic_api_key()
        
        # Crear servidor de ejemplo si es necesario
        create_example_mcp_server()
        
        print("\n¡CONFIGURACIÓN COMPLETA!")
        show_next_steps()
        
    except KeyboardInterrupt:
        print("\nConfiguración interrumpida por el usuario")
        sys.exit(1)
    except Exception as e:
        print(f"Error durante la configuración: {str(e)}")
        print("Por favor revisa los errores y ejecuta el script nuevamente")
        sys.exit(1)

if __name__ == "__main__":
    main()