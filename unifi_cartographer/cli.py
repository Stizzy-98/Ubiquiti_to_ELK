"""Output helpers and error hints shared by the installer - the same look as Kismet_Cartographer's ./install."""
from __future__ import annotations


def say(msg: str = "") -> None:
    print(msg, flush=True)


def ok(msg: str) -> None:
    say(f"  [ ok ] {msg}")


def warn(msg: str) -> None:
    say(f"  [warn] {msg}")


class Stop(Exception):
    """A blocker: `code` is the exit code, `msg` says what is wrong, `fix` says what to do about it."""

    def __init__(self, code: int, msg: str, fix: str = ""):
        super().__init__(msg)
        self.code, self.msg, self.fix = code, msg, fix


def connection_hint(err: str, url: str = "") -> str:
    e = err.lower()
    if url.startswith("http://") and ("reset by peer" in e or "remote end closed" in e or "badstatusline" in e
                                      or "bad status line" in e or "eof occurred" in e):
        return ("The server hung up on a plain-HTTP request, which usually means it uses HTTPS. "
                "Use https:// in the URL (with --host: add --https) and pass the CA with --ca <file>.")
    if "certificate verify failed" in e and ("hostname" in e or "mismatch" in e or "not valid for" in e):
        return ("The certificate is trusted but is not valid for the name in your URL. Use the name the certificate was "
                "issued for in the URL, or keep your URL and add --tls-server-name <name> (Kibana: --kibana-tls-server-name). "
                "For `kubectl port-forward` on ECK the names are <cluster>-es-http.<namespace>.svc and "
                "<kibana>-kb-http.<namespace>.svc. See docs/installation.md.")
    if "certificate verify failed" in e or "self-signed" in e or "unable to get local issuer" in e:
        return ("The server's certificate is not signed by a CA this machine trusts. Pass the CA with --ca <file> "
                "(and --kibana-ca <file> if Kibana uses a different one). How to get it: docs/installation.md.")
    if "wrong version number" in e or "unknown protocol" in e:
        return "The server answered in plain HTTP. Use http:// in the URL, or point at the HTTPS port."
    if "refused" in e:
        return "Nothing is listening there. Check the host and port (is the port-forward / service running?)."
    if "name or service not known" in e or "nodename nor servname" in e or "getaddrinfo" in e:
        return "The host name does not resolve. Check the URL."
    if "no route to host" in e or "unreachable" in e:
        return "The host cannot be reached from this machine. Check the URL, VPN/firewall, or the port-forward."
    if "timed out" in e:
        return "The connection timed out. Check the URL, firewall, or that the port-forward is still running."
    return ""
