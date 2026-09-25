#!/usr/bin/env python3
"""The syslog agent: the long-running process that receives UniFi's events and indexes them.

Point UniFi's remote logging at this process's UDP port (see docs/agent-deployment.md for exactly where and
how - Settings -> Control Plane -> Integrations -> Activity Logging, or on older UniFi OS versions Settings ->
System -> Advanced Features -> Remote Logging). Every datagram becomes one document via `cef.build_document`;
documents are batched and bulk-indexed into the write alias. Nothing here talks to a UniFi controller - the
controller only ever sends to us, over plain UDP, so there is nothing to authenticate and no controller
address to configure here.

UDP is fire-and-forget by nature: if this process or Elasticsearch is down, UniFi does not know and does not
resend, so events from that window are genuinely gone - the same as unplugging a syslog server. There is
nothing this agent can do about that; it only tries not to make things worse by holding documents in memory
indefinitely during a short Elasticsearch outage (a bounded retry, then the batch is dropped and counted).

Run directly (`python3 -m unifi_cartographer.agent ...`) or as the container command in
k8s/ubiquiti-syslog-agent.yaml. Takes the same connection flags as `./install` (see config.py).
"""
from __future__ import annotations

import argparse
import json
import selectors
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unifi_cartographer.cef import build_document  # noqa: E402
from unifi_cartographer.config import ConfigError, add_connection_args, load_config  # noqa: E402
from unifi_cartographer.http import ConnectionFailure, HttpError, es_client  # noqa: E402
from unifi_cartographer.names import DEFAULT_PREFIX, Names  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def log(msg: str) -> None:
    print(f"{now_iso()} {msg}", flush=True)


class Stats:
    """Plain counters, safe to read from the health-check thread without locking (ints are atomic enough here)."""

    def __init__(self):
        self.started = now_iso()
        self.received = 0
        self.indexed = 0
        self.parse_errors = 0
        self.batches_failed = 0
        self.dropped = 0
        self.last_flush = None

    def as_dict(self):
        return {"started": self.started, "received": self.received, "indexed": self.indexed,
                "parse_errors": self.parse_errors, "batches_failed": self.batches_failed,
                "dropped": self.dropped, "last_flush": self.last_flush}


def _health_server(port: str, stats: Stats) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence the default per-request access log
            pass

        def do_GET(self):
            if self.path == "/healthz":
                body = b"ok"
                self.send_response(200)
            elif self.path == "/stats":
                body = json.dumps(stats.as_dict()).encode()
                self.send_response(200)
            else:
                body = b"not found"
                self.send_response(404)
            self.send_header("Content-Type", "text/plain" if self.path == "/healthz" else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Batcher:
    """Collects bulk ndjson lines and flushes them to Elasticsearch by count, by age, or on demand."""

    def __init__(self, client, index: str, max_docs: int, max_seconds: float, stats: Stats, max_retries: int = 3):
        self.client, self.index, self.max_docs, self.max_seconds = client, index, max_docs, max_seconds
        self.stats, self.max_retries = stats, max_retries
        self.lines: List[str] = []
        self.opened = time.monotonic()

    def add(self, doc: dict) -> None:
        # "create", not "index": this is a write-once event stream (no document is ever meant to be
        # overwritten), and unlike the single-document API, the bulk API does not downgrade an "index"
        # action with no explicit id to create-only semantics - it always demands the broader `write`/
        # `index` privilege. Using "create" lets the agent's own credential be scoped to create_doc alone.
        self.lines.append(json.dumps({"create": {"_index": self.index}}, separators=(",", ":")))
        self.lines.append(json.dumps(doc, separators=(",", ":")))
        if len(self.lines) // 2 >= self.max_docs:
            self.flush()

    def due(self) -> bool:
        return bool(self.lines) and (time.monotonic() - self.opened) >= self.max_seconds

    def flush(self) -> None:
        if not self.lines:
            return
        ndjson = ("\n".join(self.lines) + "\n").encode("utf-8")
        count = len(self.lines) // 2
        self.lines, self.opened = [], time.monotonic()
        for attempt in range(1, self.max_retries + 1):
            try:
                res = self.client.bulk(ndjson)
                failed = sum(1 for i in res.get("items", []) if i.get("create", {}).get("error"))
                self.stats.indexed += count - failed
                if failed:
                    self.stats.dropped += failed
                    log(f"  ! {failed}/{count} document(s) in this batch were rejected by Elasticsearch")
                self.stats.last_flush = now_iso()
                return
            except (HttpError, ConnectionFailure) as e:
                if attempt == self.max_retries:
                    self.stats.batches_failed += 1
                    self.stats.dropped += count
                    log(f"  ! giving up on a batch of {count} document(s) after {attempt} attempt(s): {e}")
                    return
                time.sleep(min(10.0, 2 ** attempt))


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(ap)
    ap.add_argument("--prefix", default=DEFAULT_PREFIX, metavar="TEXT", help=f"index name prefix (default: {DEFAULT_PREFIX})")
    ap.add_argument("--listen-host", default="0.0.0.0", metavar="HOST", help="UDP address to bind (default 0.0.0.0)")
    ap.add_argument("--listen-port", type=int, default=5514, metavar="PORT",
                    help="UDP port to bind (default 5514; an unprivileged port - see k8s/ for exposing 514 externally)")
    ap.add_argument("--health-port", type=int, default=8080, metavar="PORT", help="HTTP port for /healthz and /stats (default 8080)")
    ap.add_argument("--batch-size", type=int, default=200, metavar="N", help="flush after this many datagrams (default 200)")
    ap.add_argument("--batch-seconds", type=float, default=5.0, metavar="S", help="flush at least this often (default 5s)")
    ap.add_argument("--max-datagram", type=int, default=65535, metavar="BYTES", help="UDP receive buffer size (default 65535)")
    args = ap.parse_args(argv)

    try:
        names = Names(args.prefix)
        cfg = load_config(args)
    except (ConfigError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not cfg.elastic_url or not cfg.api_key:
        print("error: no Elasticsearch URL or API key (see --help)", file=sys.stderr)
        return 2

    es = es_client(cfg)
    stats = Stats()
    _health_server(args.health_port, stats)
    batcher = Batcher(es, names.write_alias, args.batch_size, args.batch_seconds, stats)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.listen_host, args.listen_port))
    sock.setblocking(False)
    sel = selectors.DefaultSelector()
    sel.register(sock, selectors.EVENT_READ)

    stop = {"now": False}

    def handle_signal(signum, frame):
        stop["now"] = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log(f"listening for UniFi syslog on udp {args.listen_host}:{args.listen_port}, "
        f"indexing into '{names.write_alias}' at {cfg.elastic_url}, health on :{args.health_port}")
    try:
        while not stop["now"]:
            for _ in sel.select(timeout=1.0):
                try:
                    raw, addr = sock.recvfrom(args.max_datagram)
                except (BlockingIOError, OSError):
                    continue
                stats.received += 1
                doc = build_document(raw, addr[0], addr[1], now_iso())
                if "parse_error" in doc.get("unifi", {}):
                    stats.parse_errors += 1
                batcher.add(doc)
            if batcher.due():
                batcher.flush()
    finally:
        log("shutting down: flushing what is left")
        batcher.flush()
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(run())
