import sys, json

def send(msg: dict):
    data = json.dumps(msg).encode("utf-8")
    sys.stdout.write(f"Content-Length: {len(data)}\r\n\r\n")
    sys.stdout.flush()
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()

def read():
    headers = {}
    while True:
        line = sys.stdin.readline()
        if not line:
            return None
        line = line.strip()
        if line == "":
            break
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length", "0"))
    if n <= 0:
        return None
    body = sys.stdin.read(n)
    return json.loads(body)

# initialize
send({"jsonrpc":"2.0","id":"1","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"ping","version":"0.1"}}})
resp = read()
print("INIT RESP:", resp, file=sys.stderr)

# initialized (notification)
send({"jsonrpc":"2.0","method":"initialized"})

# tools/list
send({"jsonrpc":"2.0","id":"2","method":"tools/list"})
resp2 = read()
print("TOOLS RESP:", resp2, file=sys.stderr)
