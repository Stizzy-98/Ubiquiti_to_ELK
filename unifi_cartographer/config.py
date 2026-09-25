"""Connection settings: flags, environment variables and env files - the same shape Kismet_Cartographer uses.

The names and precedence are identical on purpose: a key, a CA file or a `.env` you already made for one of
these Elastic Stack tools works for this one too, unchanged.

Precedence (highest first):
  1. command-line flags (--es-url, --kibana-url, --api-key, --api-key-file, --ca, ...)
  2. real environment variables (ELASTIC_*, KIBANA_*)
  3. --env-file / $UBIQUITI_ENV
  4. ./.env
  5. <this project>/.env
  6. ~/.config/ubiquiti/credentials.env

Nothing here ever prints a secret; Config.__repr__ redacts them.
"""
from __future__ import annotations

import argparse
import base64
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_USER_ENV = Path.home() / ".config" / "ubiquiti" / "credentials.env"
ENV_MARKER = "# Written by Ubiquiti (--save-config). Settings only: the API key itself is not stored here."


class ConfigError(Exception):
    """A problem with the settings or flags the user gave; the message says how to fix it."""


def parse_env_file(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if val and val[0] in "'\"":
            q = val[0]
            end = val.find(q, 1)
            val = val[1:end] if end != -1 else val[1:]
        else:
            val = val.split(" #", 1)[0].strip()  # strip trailing comment
        out[key.strip()] = val
    return out


def warn_if_loose(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(f"WARNING: {path} is readable by other users; run: chmod 600 {path}", file=sys.stderr)


def normalize_api_key(key: str) -> str:
    """Accept the "encoded" value Elasticsearch/Kibana show, or a bare "id:api_key" pair."""
    key = key.strip().strip("'\"")
    if key.lower().startswith("apikey "):
        key = key[7:].strip()
    if not key or any(c.isspace() for c in key):
        raise ConfigError("the API key is empty or contains spaces - pass the 'encoded' value (or id:secret) exactly as "
                          "Elasticsearch shows it")
    if ":" in key:  # id:secret -> base64(id:secret)
        key = base64.b64encode(key.encode()).decode()
    return key


def read_key_file(path: Path) -> str:
    """First non-comment line of the file. A line such as ELASTIC_API_KEY=abc... is accepted too."""
    if not path.is_file():
        raise ConfigError(f"--api-key-file {path} is missing")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head, sep, rest = line.partition("=")
        if sep and head.isupper() and head.replace("_", "").isalnum() and "KEY" in head and rest.strip():
            line = rest.strip()
        warn_if_loose(path)
        return line
    raise ConfigError(f"--api-key-file {path} is empty")


@dataclass
class Config:
    elastic_url: str = ""
    kibana_url: str = ""
    api_key: str = ""
    ca_cert: str = ""
    kibana_ca_cert: str = ""
    verify_certs: bool = True
    tls_server_name: str = ""         # Elasticsearch: certificate name to verify when the URL uses another host
    kibana_tls_server_name: str = ""  # the same for Kibana (kubectl port-forward: the two services differ)
    space: str = ""                   # Kibana space id ("" = default)
    sources: List[str] = field(default_factory=list)

    def __repr__(self) -> str:  # never leak secrets into logs/tracebacks
        return (f"Config(elastic_url={self.elastic_url!r}, kibana_url={self.kibana_url!r}, "
                f"auth={'api_key' if self.api_key else 'none'}, verify_certs={self.verify_certs}, "
                f"space={self.space!r}, sources={self.sources})")


def _truthy(v: str, default: bool) -> bool:
    return default if v == "" else v.strip().lower() not in ("0", "false", "no", "off")


def load_config(args: Optional[argparse.Namespace] = None, *, environ: Optional[Mapping[str, str]] = None,
                env_files: Optional[Sequence[Path]] = None) -> Config:
    """Merge env files, the environment and flags. `environ` / `env_files` exist so tests can isolate themselves."""
    environ = os.environ if environ is None else environ
    values: Dict[str, str] = {}
    used: List[str] = []
    if env_files is None:
        env_files = [DEFAULT_USER_ENV, REPO_ROOT / ".env", Path.cwd() / ".env"]
        explicit = (getattr(args, "env_file", None) if args is not None else None) or environ.get("UBIQUITI_ENV")
        if explicit:
            env_files = list(env_files) + [Path(explicit).expanduser()]
    for p in env_files:  # later entries override earlier ones
        if p.is_file():
            warn_if_loose(p)
            values.update(parse_env_file(p))
            used.append(str(p))
    for k, v in environ.items():  # the real environment wins over files
        if k.startswith(("ELASTIC_", "KIBANA_")):
            values[k] = v

    cfg = Config(
        elastic_url=values.get("ELASTIC_URL", "").rstrip("/"),
        kibana_url=values.get("KIBANA_URL", "").rstrip("/"),
        api_key=values.get("ELASTIC_API_KEY", ""),
        ca_cert=values.get("ELASTIC_CA_CERT", ""),
        kibana_ca_cert=values.get("KIBANA_CA_CERT", "") or values.get("ELASTIC_CA_CERT", ""),
        verify_certs=_truthy(values.get("ELASTIC_VERIFY_CERTS", ""), True),
        tls_server_name=values.get("ELASTIC_TLS_SERVER_NAME", ""),
        kibana_tls_server_name=values.get("KIBANA_TLS_SERVER_NAME", ""),
        space=values.get("KIBANA_SPACE", ""),
        sources=used,
    )
    key_file = values.get("ELASTIC_API_KEY_FILE", "")
    if key_file and not cfg.api_key:  # a saved setting points at the file that holds the key
        p = Path(key_file).expanduser()
        try:
            cfg.api_key = read_key_file(p)
        except ConfigError:
            print(f"WARNING: ELASTIC_API_KEY_FILE points at {p}, which is missing or empty", file=sys.stderr)
    if args is not None:
        apply_args(cfg, args)
    elif cfg.api_key:
        cfg.api_key = normalize_api_key(cfg.api_key)
    return cfg


def add_connection_args(ap: argparse.ArgumentParser) -> None:
    """The connection flags - the same names and meanings as Kismet_Cartographer's ./install."""
    g = ap.add_argument_group("connection (flags override environment variables and env files)")
    g.add_argument("--es-url", metavar="URL", help="Elasticsearch URL, e.g. https://localhost:9200")
    g.add_argument("--kibana-url", metavar="URL", help="Kibana URL, e.g. https://localhost:5601 (include any base path)")
    g.add_argument("--host", metavar="IP_OR_NAME",
                   help="shortcut for a stack that runs on one machine: builds the URLs from this address "
                        "(http:// unless you give --ca or --https). --es-url / --kibana-url win if also given")
    g.add_argument("--https", action="store_true", help="with --host: use https:// (implied by --ca)")
    g.add_argument("--es-port", type=int, default=9200, metavar="PORT", help="with --host: Elasticsearch port (default 9200)")
    g.add_argument("--kibana-port", type=int, default=5601, metavar="PORT", help="with --host: Kibana port (default 5601)")
    g.add_argument("--api-key", metavar="KEY",
                   help="API key (the 'encoded' value, or id:secret). Visible in `ps` and shell history - "
                        "prefer --api-key-file or the ELASTIC_API_KEY environment variable")
    g.add_argument("--api-key-file", metavar="FILE", help="read the API key from FILE (first line)")
    g.add_argument("--ca", metavar="FILE", help="PEM CA certificate that signed the Elasticsearch certificate "
                                                "(omit if it is signed by a public CA)")
    g.add_argument("--kibana-ca", metavar="FILE", help="PEM CA for Kibana (default: same as --ca)")
    g.add_argument("--space", metavar="ID", help="Kibana space to install into (default: the default space)")
    g.add_argument("--tls-server-name", metavar="NAME",
                   help="Elasticsearch: hostname to verify in the certificate when you connect through another name, "
                        "e.g. kubectl port-forward to localhost")
    g.add_argument("--kibana-tls-server-name", metavar="NAME", help="the same, for Kibana")
    g.add_argument("--insecure", action="store_true", help="do NOT verify TLS certificates (throw-away tests only)")
    g.add_argument("--env-file", metavar="FILE", help="KEY=value file with the same settings (mode 600)")


def apply_args(cfg: Config, args: argparse.Namespace) -> None:
    """Flags win over everything else. One key serves Elasticsearch and Kibana."""
    g = lambda n: getattr(args, n, None)  # noqa: E731
    if g("es_url"):
        cfg.elastic_url = g("es_url").rstrip("/")
    if g("kibana_url"):
        cfg.kibana_url = g("kibana_url").rstrip("/")
    key = ""
    if g("api_key_file"):
        key = read_key_file(Path(g("api_key_file")).expanduser())
    elif g("api_key"):
        key = g("api_key")
    if key:
        cfg.api_key = normalize_api_key(key)
    elif cfg.api_key:
        cfg.api_key = normalize_api_key(cfg.api_key)
    if g("ca"):
        cfg.ca_cert = g("ca")
        if not g("kibana_ca"):
            cfg.kibana_ca_cert = g("ca")
    if g("kibana_ca"):
        cfg.kibana_ca_cert = g("kibana_ca")
    if g("tls_server_name"):
        cfg.tls_server_name = g("tls_server_name")
    if g("kibana_tls_server_name"):
        cfg.kibana_tls_server_name = g("kibana_tls_server_name")
    if g("space"):
        cfg.space = g("space")
    if g("insecure"):
        cfg.verify_certs = False
    if g("host"):
        host = g("host").strip()
        if ":" in host and not host.startswith("["):  # bare IPv6 address
            host = f"[{host}]"
        scheme = "https" if (g("https") or g("ca")) else "http"
        if not g("es_url"):
            cfg.elastic_url = f"{scheme}://{host}:{g('es_port') or 9200}"
        if not g("kibana_url"):
            cfg.kibana_url = f"{scheme}://{host}:{g('kibana_port') or 5601}"
    for label, path in (("--ca", cfg.ca_cert), ("--kibana-ca", cfg.kibana_ca_cert)):
        if path and not Path(path).expanduser().is_file():
            raise ConfigError(f"{label} file not found: {path}")


def write_env_file(cfg: Config, path: Path, key_file: str = "") -> List[str]:
    """Save the non-secret connection settings (never the API key, only the path of the file that holds it).

    Returns the names written. Refuses to overwrite a file this tool did not write.
    """
    path = Path(path)
    if path.exists() and ENV_MARKER not in path.read_text(encoding="utf-8", errors="replace"):
        raise FileExistsError(f"{path} exists and was not written by this tool; pass another file to --save-config")

    def absolute(p: str) -> str:
        return str(Path(p).expanduser().resolve()) if p else ""

    items = [("ELASTIC_URL", cfg.elastic_url), ("KIBANA_URL", cfg.kibana_url),
             ("ELASTIC_CA_CERT", absolute(cfg.ca_cert)),
             ("KIBANA_CA_CERT", absolute(cfg.kibana_ca_cert) if cfg.kibana_ca_cert != cfg.ca_cert else ""),
             ("ELASTIC_TLS_SERVER_NAME", cfg.tls_server_name), ("KIBANA_TLS_SERVER_NAME", cfg.kibana_tls_server_name),
             ("KIBANA_SPACE", cfg.space), ("ELASTIC_API_KEY_FILE", absolute(key_file)),
             ("ELASTIC_VERIFY_CERTS", "" if cfg.verify_certs else "false")]
    written = [(k, v) for k, v in items if v]
    body = ENV_MARKER + "\n" + "".join(f"{k}={v}\n" for k, v in written)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(path, 0o600)
    return [k for k, _ in written]
