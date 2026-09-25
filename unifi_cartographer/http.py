"""Minimal HTTP client for Elasticsearch and Kibana (standard library only).

* Works over plain HTTP, HTTPS with your own CA file, or HTTPS with a publicly trusted certificate.
* Authenticates with an Elasticsearch API key (`Authorization: ApiKey ...`), which both Elasticsearch and
  Kibana accept.
* Retries connection errors and HTTP 429/502/503/504 with exponential backoff.
"""
from __future__ import annotations

import http.client
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

RETRY_STATUS = {429, 502, 503, 504}


class HttpError(Exception):
    """The server answered with an error status."""

    def __init__(self, status: int, body: Any, method: str = "", path: str = ""):
        self.status, self.body, self.method, self.path = status, body, method, path
        reason = body
        if isinstance(body, dict):
            err = body.get("error", body.get("message", body))
            reason = err.get("reason") if isinstance(err, dict) else err
            if isinstance(err, dict) and err.get("root_cause"):
                reason = f"{reason} [{err['root_cause'][0].get('reason')}]"
            if isinstance(err, str) and body.get("message"):      # Kibana: {"error": "Bad Request", "message": "..."}
                reason = f"{err}: {body['message']}"
        super().__init__(f"{method} {path} -> HTTP {status}: {str(reason)[:400]}")


class ConnectionFailure(Exception):
    """Could not reach the server at all (DNS, refused, timeout, TLS verification)."""


def make_ssl_context(scheme: str, ca_cert: str = "", verify: bool = True) -> Optional[ssl.SSLContext]:
    """None for plain HTTP. For HTTPS: verify against `ca_cert` if given, else the system trust store."""
    if scheme != "https":
        return None
    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if ca_cert:
        ctx = ssl.create_default_context(cafile=ca_cert)
        # Python 3.13+ rejects CA certificates without an Authority Key Identifier (many self-signed and
        # Kubernetes/ECK-generated CAs). The chain and the host name are still fully verified.
        strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
        ctx.verify_flags &= ~strict
        return ctx
    return ssl.create_default_context()


def _tls_problem(exc: BaseException) -> bool:
    text = str(getattr(exc, "reason", exc)).lower()
    return "certificate" in text or "hostname" in text or "ssl" in text


class _NamedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that checks the certificate against `server_name` instead of the URL's host.

    Needed for `kubectl port-forward` (the URL says localhost, the certificate says
    <cluster>-es-http.<namespace>.svc) without turning verification off.
    """
    server_name = ""

    def connect(self):
        http.client.HTTPConnection.connect(self)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.server_name or self.host)


class _NamedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, ctx: ssl.SSLContext, server_name: str):
        super().__init__(context=ctx)
        self._ctx, self._name = ctx, server_name

    def https_open(self, req):
        name = self._name

        def make(host, **kw):
            conn = _NamedHTTPSConnection(host, **kw)
            conn.server_name = name
            return conn
        return self.do_open(make, req, context=self._ctx)


def kibana_space_path(space: str) -> str:
    """"" or "default" -> "" ; "team-a" -> "/s/team-a"."""
    space = (space or "").strip("/ ")
    return "" if space in ("", "default") else "/s/" + urllib.parse.quote(space, safe="")


class Client:
    def __init__(self, base_url: str, *, api_key: str = "", ca_cert: str = "", verify: bool = True,
                 timeout: float = 120.0, max_retries: int = 5, extra_headers: Optional[Dict[str, str]] = None,
                 server_name: str = ""):
        if not base_url:
            raise ValueError("no URL configured")
        self.base_url = base_url.rstrip("/")
        self.timeout, self.max_retries = timeout, max_retries
        scheme = urllib.parse.urlparse(self.base_url).scheme
        self.ctx = make_ssl_context(scheme, ca_cert, verify)
        self.opener = (urllib.request.build_opener(_NamedHTTPSHandler(self.ctx, server_name))
                       if server_name and verify and scheme == "https" else None)
        self.headers = {"Accept": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"ApiKey {api_key}"
        self.headers.update(extra_headers or {})

    def request(self, method: str, path: str, body: Any = None, params: Optional[Dict[str, Any]] = None,
                ok: Tuple[int, ...] = (200, 201), raw_body: Optional[bytes] = None,
                content_type: str = "application/json", expect_json: bool = True) -> Any:
        url = self.base_url + (path if path.startswith("/") else "/" + path)
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(
                {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in params.items()})
        data = raw_body
        headers = dict(self.headers)
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        if data is not None:
            headers["Content-Type"] = content_type
        attempt = 0
        while True:
            attempt += 1
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                opened = (self.opener.open(req, timeout=self.timeout) if self.opener
                          else urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx))
                with opened as resp:
                    payload, status = resp.read(), resp.status
            except urllib.error.HTTPError as e:
                payload, status = e.read(), e.code
                if status in RETRY_STATUS and attempt <= self.max_retries:
                    time.sleep(min(30.0, 0.5 * 2 ** attempt))
                    continue
            except (urllib.error.URLError, ConnectionError, TimeoutError, ssl.SSLError, OSError) as e:
                # A certificate problem will not fix itself: fail at once instead of retrying.
                if attempt <= self.max_retries and not _tls_problem(e):
                    time.sleep(min(30.0, 0.5 * 2 ** attempt))
                    continue
                raise ConnectionFailure(f"{method} {url}: {getattr(e, 'reason', e)}") from e
            parsed: Any = payload
            if payload and expect_json:
                try:
                    parsed = json.loads(payload)
                except ValueError:
                    parsed = payload.decode("utf-8", "replace")
            elif not payload:
                parsed = {}
            if status in ok:
                return parsed
            raise HttpError(status, parsed, method, path)

    def get(self, path, **kw): return self.request("GET", path, **kw)
    def put(self, path, body=None, **kw): return self.request("PUT", path, body, **kw)
    def post(self, path, body=None, **kw): return self.request("POST", path, body, **kw)
    def delete(self, path, **kw): return self.request("DELETE", path, **kw)

    def exists(self, path: str) -> bool:
        try:
            self.request("HEAD", path, ok=(200,), expect_json=False)
            return True
        except HttpError as e:
            if e.status == 404:
                return False
            raise

    def bulk(self, ndjson: bytes, refresh: bool = False) -> Dict[str, Any]:
        return self.request(
            "POST", "/_bulk", raw_body=ndjson, content_type="application/x-ndjson",
            params={"refresh": refresh, "filter_path": "errors,items.*.status,items.*.error,items.*._id"})


def es_client(cfg, timeout: float = 120.0) -> Client:
    return Client(cfg.elastic_url, api_key=cfg.api_key, ca_cert=cfg.ca_cert, verify=cfg.verify_certs,
                  timeout=timeout, server_name=cfg.tls_server_name)


def kibana_client(cfg, timeout: float = 120.0) -> Client:
    """Every /api/... call is space-aware because the space path is part of the base URL."""
    return Client(cfg.kibana_url + kibana_space_path(cfg.space), api_key=cfg.api_key, ca_cert=cfg.kibana_ca_cert,
                  verify=cfg.verify_certs, timeout=timeout, extra_headers={"kbn-xsrf": "ubiquiti-cartographer"},
                  server_name=cfg.kibana_tls_server_name)
