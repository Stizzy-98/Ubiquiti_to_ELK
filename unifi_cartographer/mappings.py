"""The Elasticsearch side: index settings/mappings and the ingest pipeline.

The agent (`agent.py`, via `cef.py`) does the real parsing before a document is ever sent - the same split
Kismet_Cartographer uses (heavy lifting in Python, the ingest pipeline a safety net and light enrichment):

  * `event.created` is stamped from the ingest node's own clock, alongside the agent's `event.ingested`, so a
    slow queue or a wrong device clock is visible as the gap between them.
  * `geoip` resolves `source.ip` / `destination.ip` when they are public addresses; LAN traffic will not
    match (there is no LAN entry in the database) and is left alone, not an error.
  * a document the agent could not parse (`unifi.parse_error` is set) is tagged `unifi_parse_error`, so it is
    easy to find and never silently mixed in with everything else.
  * `enrich` resolves `unifi.client.oui` (set by `cef.py` from a client MAC) against the IEEE OUI registry the
    installer loads, filling in `unifi.client.vendor` - see install's `--skip-vendor-lookup` and
    `pipeline_body(..., include_vendor_lookup=False)` for the no-network fallback.

Mapping is `dynamic: false`: fields this project does not know about are kept in `_source` but never turned
into new mapped fields, so an unexpected syslog message cannot explode the mapping or get a document rejected.
Anything left over from a CEF extension after the known fields are mapped goes into `unifi.cef.extensions`,
a `flattened` field built exactly for "arbitrary key/values, no mapping explosion, no type conflicts".
"""
from __future__ import annotations

from typing import Any, Dict

from .names import Names

_KW = {"type": "keyword"}
_IP = {"type": "ip", "ignore_malformed": True}   # CEF says src/dst are IPs, but never let a bad value reject a document
# The `geoip` processor writes a whole object here (continent_name, country_name, city_name, location, ...), not a
# bare point - ECS puts the actual coordinate at `geo.location`. Getting this wrong rejects every document with a
# public source/destination IP, which is exactly the case geoip enrichment exists for.
_GEO = {"properties": {
    "location": {"type": "geo_point"}, "continent_name": _KW, "country_name": _KW, "country_iso_code": _KW,
    "region_name": _KW, "region_iso_code": _KW, "city_name": _KW, "timezone": _KW,
}}


def mappings(tool: str) -> Dict[str, Any]:
    return {
        "dynamic": "false",
        "_meta": {"managed_by": tool, "family": "events", "docs": "docs/data-model.md"},
        "properties": {
            "@timestamp": {"type": "date"},
            "message": {"type": "text"},
            "event": {"properties": {
                "ingested": {"type": "date"}, "created": {"type": "date"}, "dataset": _KW, "kind": _KW,
                "severity": {"type": "integer", "ignore_malformed": True}, "severity_label": _KW,
            }},
            "log": {"properties": {
                "source": {"properties": {"address": _IP, "port": {"type": "integer", "ignore_malformed": True}}},
                "syslog": {"properties": {
                    "rfc": _KW, "hostname": _KW, "appname": _KW, "procid": _KW, "msgid": _KW,
                    "facility": {"properties": {"code": {"type": "short"}}},
                    "severity": {"properties": {"code": {"type": "short"}}},
                }},
            }},
            "observer": {"properties": {"hostname": _KW, "vendor": _KW, "product": _KW, "version": _KW}},
            "network": {"properties": {"transport": _KW, "name": _KW, "subnet": _KW, "vlan": _KW}},
            "source": {"properties": {"ip": _IP, "port": {"type": "integer", "ignore_malformed": True}, "hostname": _KW,
                                      "geo": _GEO}},
            "destination": {"properties": {"ip": _IP, "port": {"type": "integer", "ignore_malformed": True}, "hostname": _KW,
                                           "geo": _GEO}},
            "tags": _KW,
            "unifi": {"properties": {
                "parse_error": _KW, "category": _KW, "subcategory": _KW, "host": _KW,
                "access_method": _KW, "risk": _KW, "ssid": _KW, "ap_name": _KW,
                "client": {"properties": {"mac": _KW, "name": _KW, "alias": _KW, "hostname": _KW, "ip": _IP,
                                          "oui": _KW, "vendor": _KW}},
                "device": {"properties": {"mac": _KW, "name": _KW, "model": _KW, "ip": _IP, "version": _KW}},
                "last_connected_device": {"properties": {
                    "name": _KW, "ip": _IP, "mac": _KW, "model": _KW, "version": _KW, "link_speed": _KW,
                    "port": {"type": "integer", "ignore_malformed": True},
                }},
                "session": {"properties": {
                    "duration": _KW, "usage_down": _KW, "usage_up": _KW,
                    "usage_down_bytes": {"type": "double", "ignore_malformed": True},
                    "usage_up_bytes": {"type": "double", "ignore_malformed": True},
                }},
                "cef": {"properties": {
                    "signature_id": _KW, "name": _KW, "severity_raw": _KW,
                    "extensions": {"type": "flattened"},
                }},
            }},
        },
    }


def index_settings(replicas: int) -> Dict[str, Any]:
    return {"index": {"number_of_shards": 1, "number_of_replicas": replicas, "refresh_interval": "5s"}}


def pipeline_body(names: Names, include_vendor_lookup: bool = True) -> Dict[str, Any]:
    """`include_vendor_lookup=False` leaves out the enrich step entirely - used when the installer could not
    reach the IEEE OUI registry (or --skip-vendor-lookup was passed), since an enrich processor referencing a
    policy that was never created/executed would tag every single document with `_pipeline_failure`."""
    vendor_steps = [
        {"enrich": {"policy_name": names.enrich_policy, "field": "unifi.client.oui", "target_field": "_oui_lookup",
                   "max_matches": 1, "ignore_missing": True}},
        {"set": {"field": "unifi.client.vendor", "value": "{{{_oui_lookup.vendor}}}",
                "if": "ctx._oui_lookup != null"}},
        {"remove": {"field": "_oui_lookup", "ignore_missing": True}},
    ] if include_vendor_lookup else []
    return {
        "description": f"Safety net and enrichment for {names.write_alias} (see docs/data-model.md).",
        "_meta": {"managed_by": names.__class__.__module__.split(".")[0]},
        "processors": [
            {"set": {"field": "event.created", "value": "{{{_ingest.timestamp}}}"}},
            {"geoip": {"field": "source.ip", "target_field": "source.geo", "ignore_missing": True,
                      "on_failure": [{"remove": {"field": "source.geo", "ignore_missing": True}}]}},
            {"geoip": {"field": "destination.ip", "target_field": "destination.geo", "ignore_missing": True,
                      "on_failure": [{"remove": {"field": "destination.geo", "ignore_missing": True}}]}},
            *vendor_steps,
            {"append": {"field": "tags", "value": ["unifi_parse_error"],
                       "if": "ctx.unifi != null && ctx.unifi.parse_error != null"}},
        ],
        "on_failure": [
            {"set": {"field": "error.message", "value": "{{{_ingest.on_failure_message}}}"}},
            {"append": {"field": "tags", "value": ["_pipeline_failure"]}},
        ],
    }
