#!/usr/bin/env python3
"""Probe the Zotero MCP server (@oscardvs/zoteus) over stdio.

Runs initialize -> tools/list -> zotero_whoami, using the same command Qoder
uses. Secrets are read from the Qoder config or a launcher .cmd and are never
printed. Only the Python standard library is required.

Examples:
    python scripts/probe_zotero_mcp.py --from-qoder
    python scripts/probe_zotero_mcp.py --from-launcher "%USERPROFILE%\\.qoder-cn\\zotero-mcp\\zoteus.cmd"
    python scripts/probe_zotero_mcp.py --from-qoder --call zotero_search_items --args '{"q": "impact", "limit": 3}'
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time

DEFAULT_TIMEOUT = 180.0
QODER_SETTINGS = os.path.join(os.path.expanduser("~"), ".qoder-cn", "settings.json")
SET_LINE = re.compile(r'^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=(.*?)"?\s*$', re.IGNORECASE)


class StdioMcpClient:
    def __init__(self, argv, env=None):
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )
        self.messages = queue.Queue()
        self.stderr_lines = []
        self.next_id = 0
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        for raw in iter(self.proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                self.messages.put(json.loads(line))
            except json.JSONDecodeError:
                self.stderr_lines.append("[non-json stdout] " + line[:300])
        self.messages.put(None)

    def _read_stderr(self):
        for raw in iter(self.proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.stderr_lines.append(line)
                del self.stderr_lines[:-80]

    def _send(self, message):
        data = json.dumps(message, ensure_ascii=False).encode("utf-8")
        self.proc.stdin.write(data + b"\n")
        self.proc.stdin.flush()

    def notify(self, method, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def request(self, method, params=None, timeout=DEFAULT_TIMEOUT):
        self.next_id += 1
        request_id = self.next_id
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for %s (id=%d)" % (method, request_id))
            try:
                reply = self.messages.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError("timed out waiting for %s (id=%d)" % (method, request_id))
            if reply is None:
                raise RuntimeError("server closed stdout before replying to %s" % method)
            if reply.get("id") != request_id:
                continue
            if "error" in reply:
                raise RuntimeError("JSON-RPC error: " + json.dumps(reply["error"], ensure_ascii=False))
            return reply.get("result", {})

    def close(self):
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def load_qoder_entry():
    if not os.path.exists(QODER_SETTINGS):
        raise SystemExit("Qoder settings not found: %s" % QODER_SETTINGS)
    with open(QODER_SETTINGS, "r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    entry = (data.get("mcpServers") or {}).get("zotero")
    if not entry:
        raise SystemExit('No "zotero" entry under mcpServers in %s' % QODER_SETTINGS)
    return entry


def load_launcher_env(path):
    if not os.path.exists(path):
        raise SystemExit("Launcher not found: %s" % path)
    env = {}
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = SET_LINE.match(line)
            if match:
                env[match.group(1).upper()] = match.group(2)
    return env


def redact(text, env):
    for key, value in env.items():
        if key.endswith("API_KEY") and value and len(value) >= 6:
            text = text.replace(value, "<redacted>")
    return text


def summarize_result(result, full):
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if result.get("isError"):
        text = "[isError] " + text
    if not full and len(text) > 4000:
        text = text[:4000] + "\n... (truncated; use --full)"
    return text


def main():
    parser = argparse.ArgumentParser(description="Probe the Zotero MCP stdio server (zoteus)")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--from-qoder", action="store_true", help="Load env from mcpServers.zotero.env in Qoder settings")
    source.add_argument("--from-launcher", metavar="CMD", help="Load env from a zoteus.cmd launcher")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--call", default="zotero_whoami", help="Tool to call (default: zotero_whoami)")
    parser.add_argument("--no-call", action="store_true", help="Only initialize and list tools")
    parser.add_argument("--args", default="{}", help="JSON object passed to --call")
    parser.add_argument("--full", action="store_true", help="Do not truncate tool output")
    parser.add_argument("--verbose", action="store_true", help="Print redacted server stderr on failure")
    args = parser.parse_args()

    env = os.environ.copy()
    loaded = {}
    if args.from_qoder:
        entry = load_qoder_entry()
        loaded = dict(entry.get("env") or {})
        if not loaded:
            launchers = [str(arg) for arg in (entry.get("args") or []) if str(arg).lower().endswith(".cmd")]
            hint = " (entry uses launcher %s; use --from-launcher)" % launchers[0] if launchers else ""
            raise SystemExit("No env configured on mcpServers.zotero" + hint)
        print("ENV: loaded %s from Qoder settings" % ", ".join(sorted(loaded)))
    elif args.from_launcher:
        loaded = load_launcher_env(args.from_launcher)
        if not loaded:
            raise SystemExit("No 'set \"NAME=value\"' lines found in %s" % args.from_launcher)
        print("ENV: loaded %s from %s" % (", ".join(sorted(loaded)), args.from_launcher))
    else:
        print("ENV: using the current process environment")

    env.update(loaded)
    if not env.get("ZOTERO_API_KEY"):
        print("ENV: ZOTERO_API_KEY is not set; cloud writes and some tools will fail", file=sys.stderr)

    command = ["cmd", "/c", "npx", "-y", "@oscardvs/zoteus"] if os.name == "nt" else [
        "npx", "-y", "@oscardvs/zoteus"]
    print("CMD: " + " ".join(command))
    client = StdioMcpClient(command, env=env)
    ok = False
    try:
        init = client.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "zotero-mcp-probe", "version": "1.0"},
            },
            timeout=args.timeout,
        )
        client.notify("notifications/initialized")
        info = init.get("serverInfo", {})
        print("INIT: %s %s (protocol %s)" % (
            info.get("name", "?"), info.get("version", "?"), init.get("protocolVersion", "?")))

        listing = client.request("tools/list", {}, timeout=args.timeout)
        tools = listing.get("tools", [])
        print("TOOL COUNT: %d" % len(tools))
        print("TOOLS: " + ", ".join(tool.get("name", "?") for tool in tools))

        if not args.no_call:
            try:
                call_args = json.loads(args.args)
            except json.JSONDecodeError as exc:
                raise SystemExit("--args is not valid JSON: %s" % exc)
            if not isinstance(call_args, dict):
                raise SystemExit("--args must be a JSON object")
            result = client.request(
                "tools/call",
                {"name": args.call, "arguments": call_args},
                timeout=args.timeout,
            )
            print("CALL %s: %s" % (args.call, summarize_result(result, args.full)))
            ok = not result.get("isError", False)
        else:
            ok = len(tools) > 0
    except (RuntimeError, TimeoutError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        if args.verbose and client.stderr_lines:
            print("--- server stderr ---", file=sys.stderr)
            print(redact("\n".join(client.stderr_lines[-30:]), loaded), file=sys.stderr)
    finally:
        client.close()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
