"""A tiny stdio MCP server for testing `rclass scan`. Behaviour is set by a JSON file passed as argv[1]:

{"tools": [{"name": "...", "annotations": {...}}], "page_size": 2, "noise": true,
 "server_request": true, "crash": false, "hang": false, "name": "fake", "version": "1.0"}
"""
import json
import sys
import time

cfg = json.load(open(sys.argv[1]))
tools = cfg.get("tools", [])
page = cfg.get("page_size") or len(tools) or 1

if cfg.get("crash"):
    sys.stderr.write("fatal: missing configuration\n")
    sys.exit(3)
if cfg.get("noise"):
    print("starting fake server (this line is not JSON)", flush=True)


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    method, mid = msg.get("method"), msg.get("id")
    if cfg.get("hang") and method == "tools/list":
        time.sleep(60)
    if method == "initialize":
        if cfg.get("server_request"):
            send({"jsonrpc": "2.0", "id": "srv-1", "method": "roots/list", "params": {}})
            send({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": "hi"}})
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
            "serverInfo": {"name": cfg.get("name", "fake"), "version": cfg.get("version", "1.0")}}})
    elif method == "tools/list":
        start = int((msg.get("params") or {}).get("cursor") or 0)
        chunk = tools[start:start + page]
        result = {"tools": [{"inputSchema": {"type": "object"}, **t} for t in chunk]}
        if start + page < len(tools):
            result["nextCursor"] = str(start + page)
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    elif mid is not None and method:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown"}})
