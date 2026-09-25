"""Parse the log lines UniFi's remote-logging feature sends: a syslog envelope wrapping a CEF message.

UniFi Network (Settings -> Control Plane -> Integrations -> Activity Logging, or on older versions
Settings -> System -> Advanced Features -> Remote Logging) sends each selected category - Admin Activity,
Clients, Critical, Devices, Security Detections, Triggers, Updates, plus per-device logging for the Gateway,
Access Points and Switches, and a Debug verbosity level - as one UDP datagram per event. A real example,
straight from a UniFi console:

    CEF:0|Ubiquiti|UniFi Network|9.4.19|201|Threat Detected and Blocked|7|proto=TCP src=10.0.0.100 spt=52331
    dst=192.168.0.233 dpt=443 UNIFIcategory=Security UNIFIsubCategory=Intrusion Prevention
    UNIFIhost=Express 7 UNIFIdeviceMac=84:78:48:80:0d:86 UNIFIdeviceName=Express 7 UNIFIdeviceModel=UX7
    UNIFIdeviceIp=192.168.0.1 UNIFIdeviceVersion=4.3.9 UNIFIrisk=medium

That CEF message is sometimes wrapped in an ordinary syslog header (`<PRI>timestamp host CEF:0|...`) and
sometimes sent bare; UniFi OS has also been reported (community forum threads) to occasionally emit CEF with
extra or missing pipes. Everything here is deliberately lenient: a line that cannot be fully parsed still
becomes a document, with whatever was understood plus the original text, never a dropped or crashing event.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# --------------------------------------------------------------------------------------- syslog envelope
_PRI_RE = re.compile(r"^<(\d{1,3})>")
_RFC5424_VERSION_RE = re.compile(r"^(\d{1,2}) ")
_RFC3164_HEADER_RE = re.compile(r"^(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(\S+)\s+")


def parse_syslog_envelope(text: str) -> Dict[str, Any]:
    """Strip an optional syslog header and return what is left as `message`, plus whatever the header gave.

    Handles RFC 5424 (`<PRI>VERSION TIMESTAMP HOST APP PROCID MSGID [SD] MSG`), a best-effort RFC 3164
    (`<PRI>Mon DD HH:MM:SS HOST MSG`), or no header at all - UniFi devices are not consistent about this.
    """
    env: Dict[str, Any] = {"rfc": "none", "facility": None, "severity": None, "hostname": None,
                           "appname": None, "procid": None, "msgid": None, "message": text}
    m = _PRI_RE.match(text)
    if not m:
        return env
    pri = int(m.group(1))
    env["facility"], env["severity"] = pri // 8, pri % 8
    rest = text[m.end():]

    v = _RFC5424_VERSION_RE.match(rest)
    if v:
        parsed = _parse_rfc5424(rest[v.end():])
        if parsed is not None:
            env.update(parsed, rfc="5424")
            return env

    h = _RFC3164_HEADER_RE.match(rest)
    if h:
        env.update(rfc="3164", hostname=h.group(2), message=rest[h.end():])
    else:
        env.update(rfc="3164_no_header", message=rest)
    return env


def _parse_rfc5424(rest: str) -> Optional[Dict[str, Any]]:
    """The five space-separated header fields, then structured data (`-` or bracketed groups), then MSG."""
    fields = []
    for _ in range(5):
        sp = rest.find(" ")
        if sp == -1:
            return None
        fields.append(rest[:sp] if rest[:sp] != "-" else None)
        rest = rest[sp + 1:]
    timestamp, hostname, appname, procid, msgid = fields

    if rest.startswith("-"):
        rest = rest[1:]
    elif rest.startswith("["):
        depth, i, escaped, in_quotes = 0, 0, False, False
        for i, ch in enumerate(rest):
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quotes = not in_quotes
            elif ch == "[" and not in_quotes:
                depth += 1
            elif ch == "]" and not in_quotes:
                depth -= 1
                if depth == 0:
                    break
        else:
            return None  # unterminated structured data - salvage nothing, let the caller fall back
        rest = rest[i + 1:]
    else:
        return None
    return {"timestamp": timestamp, "hostname": hostname, "appname": appname, "procid": procid,
            "msgid": msgid, "message": rest[1:] if rest.startswith(" ") else rest}


# --------------------------------------------------------------------------------------- CEF
_HEADER_FIELDS = ("cef_version", "device_vendor", "device_product", "device_version", "signature_id", "name", "severity")
_UNESCAPE_HEADER = {r"\|": "|", r"\\": "\\"}
_UNESCAPE_EXT = {r"\=": "=", r"\\": "\\", r"\n": "\n", r"\r": "\r"}
_EXT_KEY_RE = re.compile(r"(?:^|\s)([A-Za-z][\w.]*)=")


def _unescape(value: str, table: Dict[str, str]) -> str:
    for esc, plain in table.items():
        value = value.replace(esc, plain)
    return value


def _split_unescaped(text: str, sep: str, maxsplit: int):
    """Like str.split, but a backslash-escaped separator does not split."""
    parts, buf, i = [], "", 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            buf += text[i:i + 2]
            i += 2
            continue
        if text[i] == sep and len(parts) < maxsplit:
            parts.append(buf)
            buf = ""
            i += 1
            continue
        buf += text[i]
        i += 1
    parts.append(buf)
    return parts


def parse_cef(message: str) -> Optional[Dict[str, Any]]:
    """None if no "CEF:" marker is found. Otherwise the header fields plus an `extension` dict.

    Tolerant of a malformed header (UniFi OS has been reported to occasionally miswrite one): whatever
    pipe-delimited fields are present are used; missing ones are None; nothing raises.
    """
    idx = message.find("CEF:")
    if idx == -1:
        return None
    body = message[idx + len("CEF:"):]
    parts = _split_unescaped(body, "|", 7)
    header = {name: (_unescape(parts[i], _UNESCAPE_HEADER) if i < len(parts) and parts[i] != "" else None)
             for i, name in enumerate(_HEADER_FIELDS)}
    ext_text = parts[7] if len(parts) > 7 else ""
    return {**header, "extension": _parse_extension(ext_text), "raw_cef": message[idx:]}


def _parse_extension(text: str) -> Dict[str, str]:
    keys = list(_EXT_KEY_RE.finditer(text))
    ext: Dict[str, str] = {}
    for i, m in enumerate(keys):
        end = keys[i + 1].start() if i + 1 < len(keys) else len(text)
        value = text[m.end():end].strip()
        if value:
            ext[m.group(1)] = _unescape(value, _UNESCAPE_EXT)
    return ext


def severity_label(value: Optional[str]) -> Optional[str]:
    """CEF severity is 0-10; the spec's own bands, used for a readable field alongside the number."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n <= 3:
        return "low"
    if n <= 6:
        return "medium"
    if n <= 8:
        return "high"
    return "very-high"


# --------------------------------------------------------------------------------------- extension field map
# CEF's own standard keys (a small, stable set) map onto ECS; everything else UniFi sends is its own
# "UNIFI<Name>" extensions (see the module docstring). Unmapped extension keys are kept, not dropped.
#
# Real captures (not the public-docs fixtures alone) showed two things worth a comment: a "Wired Client
# Connected" event uses UNIFIconnectedToDevice* while its "Disconnected" counterpart uses
# UNIFIlastConnectedToDevice* - genuinely different key names for the same concept, from the same category -
# so both map onto the same ("last_connected_device", ...) path below. Never assume one event name's
# extension keys generalise to another, even in the same category.
_STANDARD_EXT_ECS = {"proto": ("network", "transport"), "src": ("source", "ip"), "dst": ("destination", "ip"),
                    "shost": ("source", "hostname"), "dhost": ("destination", "hostname")}
_STANDARD_EXT_ECS_INT = {"spt": ("source", "port"), "dpt": ("destination", "port")}
_UNIFI_EXT_MAP = {
    "UNIFIcategory": "category", "UNIFIsubCategory": "subcategory", "UNIFIhost": "host",
    "UNIFIaccessMethod": "access_method", "UNIFIrisk": "risk", "UNIFIssid": "ssid", "UNIFIapName": "ap_name",
    "UNIFIclientMac": ("client", "mac"), "UNIFIclientName": ("client", "name"),
    "UNIFIclientAlias": ("client", "alias"), "UNIFIclientHostname": ("client", "hostname"),
    "UNIFIclientIp": ("client", "ip"),
    "UNIFIdeviceMac": ("device", "mac"), "UNIFIdeviceName": ("device", "name"),
    "UNIFIdeviceModel": ("device", "model"), "UNIFIdeviceIp": ("device", "ip"),
    "UNIFIdeviceVersion": ("device", "version"),
    "UNIFIlastConnectedToDeviceName": ("last_connected_device", "name"),
    "UNIFIlastConnectedToDeviceIp": ("last_connected_device", "ip"),
    "UNIFIlastConnectedToDeviceMac": ("last_connected_device", "mac"),
    "UNIFIlastConnectedToDeviceModel": ("last_connected_device", "model"),
    "UNIFIlastConnectedToDeviceVersion": ("last_connected_device", "version"),
    "UNIFIconnectedToDeviceName": ("last_connected_device", "name"),
    "UNIFIconnectedToDeviceIp": ("last_connected_device", "ip"),
    "UNIFIconnectedToDeviceMac": ("last_connected_device", "mac"),
    "UNIFIconnectedToDeviceModel": ("last_connected_device", "model"),
    "UNIFIconnectedToDeviceVersion": ("last_connected_device", "version"),
    "UNIFIlinkSpeed": ("last_connected_device", "link_speed"),
    "UNIFIduration": ("session", "duration"),
}
# Ports on either "connected device" key family; kept separate since these need int parsing, not a bare copy.
_UNIFI_EXT_INT_MAP = {
    "UNIFIlastConnectedToDevicePort": ("last_connected_device", "port"),
    "UNIFIconnectedToDevicePort": ("last_connected_device", "port"),
}
# UNIFInetwork* describes the network the EVENT happened on, not a UniFi-specific concept - lands at the
# top level (doc.network.*) alongside "proto"'s network.transport, the same as the standard CEF keys do.
_UNIFI_EXT_TOP_LEVEL_MAP = {
    "UNIFInetworkName": ("network", "name"), "UNIFInetworkSubnet": ("network", "subnet"),
    "UNIFInetworkVlan": ("network", "vlan"),
}
# UNIFIusageDown/Up carry a UniFi-formatted string ("43.99 KB") that is kept as-is *and* parsed to a byte
# count (see _parse_usage_bytes) so a dashboard can actually sum/chart it - a plain field rename is not enough.
_UNIFI_USAGE_KEYS = {"UNIFIusageDown": "usage_down", "UNIFIusageUp": "usage_up"}

_USAGE_RE = re.compile(r"^([\d.]+)\s*(B|KB|MB|GB|TB)$", re.IGNORECASE)
_USAGE_MULTIPLIERS = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}


def _parse_usage_bytes(value: str) -> Optional[float]:
    """UniFi's own display convention: "<number> <unit>", 1024-based. None if the shape is unrecognised -
    the raw string is always kept regardless, so nothing is lost even when this cannot parse it."""
    m = _USAGE_RE.match(value.strip())
    if not m:
        return None
    return float(m.group(1)) * _USAGE_MULTIPLIERS[m.group(2).upper()]


# A client MAC's OUI (first 3 bytes/6 hex chars), for the installer's vendor enrich lookup (see
# docs/agent-deployment.md and install's --skip-vendor-lookup) to resolve into unifi.client.vendor at ingest
# time. Locally-administered (randomised, privacy-mode) MAC addresses have no real IEEE-assigned vendor -
# phones/tablets will often show no vendor for this reason, which is correct, not a bug.
def _mac_oui(mac: str) -> Optional[str]:
    bare = mac.replace(":", "").replace("-", "").upper()
    return bare[:6] if len(bare) >= 6 else None


# The non-CEF per-device syslog UniFi hardware also sends (hostapd, kernel, mcad, udhcpc, ...) embeds its own
# "<mac>,<model>-<version>: <process>[<pid>]: " tag ahead of the actual message, inside what parse_syslog_envelope
# already stripped down to `message` - this is UniFi's own convention, not part of RFC 3164/5424, so it needs
# its own parser layered on top. Confirmed against real captures from both an AP (U7-Pro) and a switch
# (USW-Pro-Max-16-PoE); ~99% real-world coverage on a real fleet's traffic - a handful of double-tagged daemon
# lines (e.g. "mcad: mcad[981]:") are left unparsed rather than chased further.
_UNIFI_SYSLOG_TAG_RE = re.compile(r"^([0-9a-fA-F]+),(.+)-(\d+\.\d+\.\d+\+\d+): (\S+?)(?:\[(\d+)\])?: (.*)$", re.DOTALL)


def parse_unifi_syslog_tag(message: str) -> Optional[Dict[str, Any]]:
    """None if `message` does not start with UniFi's own device tag. Otherwise mac/model/version/appname,
    procid (if present), and the rest of the message unchanged."""
    m = _UNIFI_SYSLOG_TAG_RE.match(message.strip())
    if not m:
        return None
    return {"mac": m.group(1), "model": m.group(2), "version": m.group(3), "appname": m.group(4),
            "procid": m.group(5), "rest": m.group(6)}


def _set(doc: Dict[str, Any], path, value) -> None:
    if isinstance(path, str):
        path = (path,)
    node = doc
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def build_document(raw: bytes, source_ip: Optional[str], source_port: Optional[int], receive_time: str) -> Dict[str, Any]:
    """One Elasticsearch document from one UDP datagram. Never raises; a line it cannot parse still becomes
    a document with the original text and `unifi.parse_error` set, so nothing UniFi sends is silently lost."""
    text = raw.decode("utf-8", "replace").strip("\x00").strip()
    doc: Dict[str, Any] = {
        "@timestamp": receive_time,
        "event": {"ingested": receive_time, "dataset": "ubiquiti.unifi", "kind": "event"},
        "message": text,
        "log": {"source": {"address": source_ip, "port": source_port}},
    }
    if not text:
        _set(doc, ("unifi", "parse_error"), "empty_message")
        return doc

    env = parse_syslog_envelope(text)
    doc["log"]["syslog"] = {"rfc": env["rfc"]}
    if env["facility"] is not None:
        doc["log"]["syslog"]["facility"] = {"code": env["facility"]}
        doc["log"]["syslog"]["severity"] = {"code": env["severity"]}
    for key in ("hostname", "appname", "procid", "msgid"):
        if env.get(key):
            doc["log"]["syslog"][key] = env[key]
    if env.get("hostname"):
        doc.setdefault("observer", {})["hostname"] = env["hostname"]

    cef = parse_cef(env["message"])
    if cef is None:
        tag = parse_unifi_syslog_tag(env["message"])
        if tag is not None:
            _set(doc, ("unifi", "device", "mac"), tag["mac"])
            _set(doc, ("unifi", "device", "model"), tag["model"])
            _set(doc, ("unifi", "device", "version"), tag["version"])
            doc["log"]["syslog"]["appname"] = tag["appname"]
            if tag["procid"]:
                doc["log"]["syslog"]["procid"] = tag["procid"]
            _set(doc, ("unifi", "parse_error"), "not_cef_envelope_parsed")
        else:
            _set(doc, ("unifi", "parse_error"), "not_cef")
        return doc

    doc["observer"] = {**doc.get("observer", {}), "vendor": cef["device_vendor"], "product": cef["device_product"],
                       "version": cef["device_version"]}
    unifi: Dict[str, Any] = {"cef": {"signature_id": cef["signature_id"], "name": cef["name"]}}
    if cef["severity"] is not None:
        try:
            doc["event"]["severity"] = int(float(cef["severity"]))
        except ValueError:
            unifi["cef"]["severity_raw"] = cef["severity"]
        else:
            label = severity_label(cef["severity"])
            if label:
                doc["event"]["severity_label"] = label

    leftover = {}
    for key, value in cef["extension"].items():
        if key in _UNIFI_EXT_MAP:
            _set(unifi, _UNIFI_EXT_MAP[key] if isinstance(_UNIFI_EXT_MAP[key], tuple) else (_UNIFI_EXT_MAP[key],), value)
            if key == "UNIFIclientMac":
                oui = _mac_oui(value)
                if oui:
                    _set(unifi, ("client", "oui"), oui)
        elif key in _UNIFI_EXT_INT_MAP:
            try:
                _set(unifi, _UNIFI_EXT_INT_MAP[key], int(value))
            except ValueError:
                leftover[key] = value
        elif key in _UNIFI_EXT_TOP_LEVEL_MAP:
            _set(doc, _UNIFI_EXT_TOP_LEVEL_MAP[key], value)
        elif key in _UNIFI_USAGE_KEYS:
            field = _UNIFI_USAGE_KEYS[key]
            _set(unifi, ("session", field), value)
            usage_bytes = _parse_usage_bytes(value)
            if usage_bytes is not None:
                _set(unifi, ("session", f"{field}_bytes"), usage_bytes)
        elif key in _STANDARD_EXT_ECS:
            _set(doc, _STANDARD_EXT_ECS[key], value)
        elif key in _STANDARD_EXT_ECS_INT:
            try:
                _set(doc, _STANDARD_EXT_ECS_INT[key], int(value))
            except ValueError:
                leftover[key] = value
        else:
            leftover[key] = value
    if leftover:
        unifi["cef"]["extensions"] = leftover
    doc["unifi"] = unifi
    return doc
