"""Definitions of the Kibana objects: data view, a dozen Lens charts, a saved search, and the dashboard.

The Lens panels are built from shapes already proven to work in this Elastic Stack (the same
`formBased`/`lnsMetric`/`lnsPie`/`lnsXY` JSON Kismet_Cartographer's own dashboard uses, plus `lnsDatatable`
for the client-status table), just re-parameterised for this project's fields, rather than hand-guessed from
scratch.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from .names import Names

BRAND = "Ubiquiti"


def stringify_json_attrs(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            v = stringify_json_attrs(v)
            out[k] = json.dumps(v, separators=(",", ":")) if k.endswith("JSON") and not isinstance(v, str) else v
        return out
    if isinstance(value, list):
        return [stringify_json_attrs(v) for v in value]
    return value


def data_view(names: Names) -> Dict[str, Any]:
    return {"id": names.data_view, "data_view": {"title": f"{names.prefix}*", "name": f"{BRAND}{names.label}",
                                                 "timeFieldName": "@timestamp", "allowNoIndex": True}}


# --------------------------------------------------------------------------------------- Lens building blocks
def _metric(title: str, query: str) -> Dict[str, Any]:
    return {"visualizationType": "lnsMetric", "state": {
        "datasourceStates": {"formBased": {"layers": {"layer1": {
            "columnOrder": ["metric"],
            "columns": {"metric": {"label": title, "customLabel": True, "dataType": "number", "operationType": "count",
                                   "isBucketed": False, "scale": "ratio", "sourceField": "___records___",
                                   "params": {"emptyAsNull": False}}},
            "incompleteColumns": {}, "sampling": 1}}}},
        "filters": [], "query": {"query": query, "language": "kuery"},
        "visualization": {"layerId": "layer1", "layerType": "data", "metricAccessor": "metric", "color": "#F1F4FA",
                          "showBar": False},
        "adHocDataViews": {}, "internalReferences": []}}


def _pie(title: str, field: str, query: str, size: int = 8) -> Dict[str, Any]:
    return {"visualizationType": "lnsPie", "state": {
        "datasourceStates": {"formBased": {"layers": {"layer1": {
            "columnOrder": ["bucket", "metric"],
            "columns": {
                "metric": {"label": "Events", "customLabel": True, "dataType": "number", "operationType": "count",
                          "isBucketed": False, "scale": "ratio", "sourceField": "___records___", "params": {"emptyAsNull": False}},
                "bucket": {"label": title, "customLabel": True, "dataType": "string", "operationType": "terms",
                          "scale": "ordinal", "sourceField": field, "isBucketed": True,
                          "params": {"size": size, "orderBy": {"type": "column", "columnId": "metric"}, "orderDirection": "desc",
                                    "otherBucket": True, "missingBucket": True, "parentFormat": {"id": "terms"},
                                    "include": [], "exclude": [], "includeIsRegex": False, "excludeIsRegex": False}},
            }, "incompleteColumns": {}, "sampling": 1}}}},
        "filters": [], "query": {"query": query, "language": "kuery"},
        "visualization": {"shape": "donut", "layers": [{"layerId": "layer1", "primaryGroups": ["bucket"], "metrics": ["metric"],
                                                        "numberDisplay": "percent", "categoryDisplay": "default",
                                                        "legendDisplay": "show", "legendPosition": "right",
                                                        "nestedLegend": False, "layerType": "data", "colorMapping": None}]},
        "adHocDataViews": {}, "internalReferences": []}}


def _bar_terms(title: str, field: str, query: str, size: int = 10) -> Dict[str, Any]:
    return {"visualizationType": "lnsXY", "state": {
        "datasourceStates": {"formBased": {"layers": {"layer1": {
            "columnOrder": ["bucket", "metric"],
            "columns": {
                "metric": {"label": "Events", "customLabel": True, "dataType": "number", "operationType": "count",
                          "isBucketed": False, "scale": "ratio", "sourceField": "___records___", "params": {"emptyAsNull": False}},
                "bucket": {"label": title, "customLabel": True, "dataType": "string", "operationType": "terms",
                          "scale": "ordinal", "sourceField": field, "isBucketed": True,
                          "params": {"size": size, "orderBy": {"type": "column", "columnId": "metric"}, "orderDirection": "desc",
                                    "otherBucket": False, "missingBucket": False, "parentFormat": {"id": "terms"},
                                    "include": [], "exclude": [], "includeIsRegex": False, "excludeIsRegex": False}},
            }, "incompleteColumns": {}, "sampling": 1}}}},
        "filters": [], "query": {"query": query, "language": "kuery"},
        "visualization": {"legend": {"isVisible": False, "position": "right"}, "valueLabels": "hide",
                          "fittingFunction": "None",
                          "axisTitlesVisibilitySettings": {"x": False, "yLeft": False, "yRight": True},
                          "tickLabelsVisibilitySettings": {"x": True, "yLeft": True, "yRight": True},
                          "gridlinesVisibilitySettings": {"x": False, "yLeft": True, "yRight": True},
                          "preferredSeriesType": "bar_horizontal",
                          "layers": [{"layerId": "layer1", "accessors": ["metric"], "position": "top",
                                     "seriesType": "bar_horizontal", "showGridlines": False, "layerType": "data",
                                     "xAccessor": "bucket", "colorMapping": None}]},
        "adHocDataViews": {}, "internalReferences": []}}


def _over_time(title: str, split_field: str, query: str) -> Dict[str, Any]:
    return {"visualizationType": "lnsXY", "state": {
        "datasourceStates": {"formBased": {"layers": {"layer1": {
            "columnOrder": ["date", "split", "metric"],
            "columns": {
                "metric": {"label": "Events", "customLabel": True, "dataType": "number", "operationType": "count",
                          "isBucketed": False, "scale": "ratio", "sourceField": "___records___", "params": {"emptyAsNull": False}},
                "date": {"label": "@timestamp", "dataType": "date", "operationType": "date_histogram", "sourceField": "@timestamp",
                        "isBucketed": True, "scale": "interval", "params": {"interval": "auto", "includeEmptyRows": True, "dropPartials": False}},
                "split": {"label": title, "customLabel": True, "dataType": "string", "operationType": "terms", "scale": "ordinal",
                         "sourceField": split_field, "isBucketed": True,
                         "params": {"size": 6, "orderBy": {"type": "column", "columnId": "metric"}, "orderDirection": "desc",
                                   "otherBucket": True, "missingBucket": False, "parentFormat": {"id": "terms"},
                                   "include": [], "exclude": [], "includeIsRegex": False, "excludeIsRegex": False}},
            }, "incompleteColumns": {}, "sampling": 1}}}},
        "filters": [], "query": {"query": query, "language": "kuery"},
        "visualization": {"legend": {"isVisible": True, "position": "right"}, "valueLabels": "hide",
                          "fittingFunction": "None",
                          "axisTitlesVisibilitySettings": {"x": False, "yLeft": False, "yRight": True},
                          "tickLabelsVisibilitySettings": {"x": True, "yLeft": True, "yRight": True},
                          "gridlinesVisibilitySettings": {"x": False, "yLeft": True, "yRight": True},
                          "preferredSeriesType": "bar_stacked",
                          "layers": [{"layerId": "layer1", "accessors": ["metric"], "position": "top",
                                     "seriesType": "bar_stacked", "showGridlines": False, "layerType": "data",
                                     "xAccessor": "date", "splitAccessor": "split", "colorMapping": None}]},
        "adHocDataViews": {}, "internalReferences": []}}


def _last_value(label: str, field: str, data_type: str = "string") -> Dict[str, Any]:
    return {"label": label, "customLabel": True, "dataType": data_type, "operationType": "last_value",
            "isBucketed": False, "scale": "ordinal" if data_type == "string" else "ratio",
            "sourceField": field, "params": {"sortField": "@timestamp", "showArrayValues": False}}


def _client_status_table(query: str) -> Dict[str, Any]:
    """Who's connected right now: last-value-per-client-MAC, so the newest event per device is the row shown.

    `unifi.client.mac` is the only field guaranteed present on every client-connect/disconnect event, old and
    new; alias/vendor/connected-to/link-speed/network only populate once a fresh event carries them - a client
    whose last event predates this project version (or an install upgraded from an older one) will show those
    columns blank until its next connect/disconnect, no reinstall needed."""
    columns = {
        "bucket": {"label": "Client MAC", "customLabel": True, "dataType": "string", "operationType": "terms",
                  "scale": "ordinal", "sourceField": "unifi.client.mac", "isBucketed": True,
                  "params": {"size": 50, "orderBy": {"type": "column", "columnId": "col_lastseen"}, "orderDirection": "desc",
                            "otherBucket": False, "missingBucket": False, "parentFormat": {"id": "terms"},
                            "include": [], "exclude": [], "includeIsRegex": False, "excludeIsRegex": False}},
        "col_alias": _last_value("Device Name", "unifi.client.alias"),
        "col_vendor": _last_value("Vendor", "unifi.client.vendor"),
        "col_status": _last_value("Last event", "unifi.cef.name"),
        "col_connected_to": _last_value("Connected to", "unifi.last_connected_device.name"),
        "col_link_speed": _last_value("Link speed", "unifi.last_connected_device.link_speed"),
        "col_network": _last_value("Network", "network.name"),
        "col_lastseen": {"label": "Last seen", "customLabel": True, "dataType": "date", "operationType": "max",
                         "isBucketed": False, "scale": "ratio", "sourceField": "@timestamp", "params": {}},
    }
    order = ["col_alias", "bucket", "col_vendor", "col_status", "col_connected_to", "col_link_speed",
             "col_network", "col_lastseen"]
    return {"visualizationType": "lnsDatatable", "state": {
        "datasourceStates": {"formBased": {"layers": {"layer1": {
            "columnOrder": order, "columns": columns, "incompleteColumns": {}, "sampling": 1}}}},
        "filters": [], "query": {"query": query, "language": "kuery"},
        "visualization": {"layerId": "layer1", "layerType": "data",
                          "columns": [{"columnId": c} for c in order]},
        "adHocDataViews": {}, "internalReferences": []}}


def _bar_terms_unique(title: str, bucket_field: str, metric_field: str, metric_label: str, query: str,
                      size: int = 10) -> Dict[str, Any]:
    """Bucket by device, metric = distinct client count (not raw event count) - "how many different clients
    has this switch/AP seen". Clicking a bar filters the rest of the dashboard to that one device - a built-in
    Kibana behaviour, nothing extra to wire up."""
    return {"visualizationType": "lnsXY", "state": {
        "datasourceStates": {"formBased": {"layers": {"layer1": {
            "columnOrder": ["bucket", "metric"],
            "columns": {
                "metric": {"label": metric_label, "customLabel": True, "dataType": "number", "operationType": "unique_count",
                          "isBucketed": False, "scale": "ratio", "sourceField": metric_field, "params": {"emptyAsNull": False}},
                "bucket": {"label": title, "customLabel": True, "dataType": "string", "operationType": "terms",
                          "scale": "ordinal", "sourceField": bucket_field, "isBucketed": True,
                          "params": {"size": size, "orderBy": {"type": "column", "columnId": "metric"}, "orderDirection": "desc",
                                    "otherBucket": False, "missingBucket": False, "parentFormat": {"id": "terms"},
                                    "include": [], "exclude": [], "includeIsRegex": False, "excludeIsRegex": False}},
            }, "incompleteColumns": {}, "sampling": 1}}}},
        "filters": [], "query": {"query": query, "language": "kuery"},
        "visualization": {"legend": {"isVisible": False, "position": "right"}, "valueLabels": "hide", "fittingFunction": "None",
                          "axisTitlesVisibilitySettings": {"x": False, "yLeft": False, "yRight": True},
                          "tickLabelsVisibilitySettings": {"x": True, "yLeft": True, "yRight": True},
                          "gridlinesVisibilitySettings": {"x": False, "yLeft": True, "yRight": True},
                          "preferredSeriesType": "bar_horizontal",
                          "layers": [{"layerId": "layer1", "accessors": ["metric"], "position": "top",
                                     "seriesType": "bar_horizontal", "showGridlines": False, "layerType": "data",
                                     "xAccessor": "bucket", "colorMapping": None}]},
        "adHocDataViews": {}, "internalReferences": []}}


_BYTES_FORMAT = {"format": {"id": "bytes", "params": {"decimals": 1}}}


def _bar_terms_usage(title: str, bucket_field: str, query: str, size: int = 10) -> Dict[str, Any]:
    """Total bytes down/up per client, grouped bars, byte-formatted. Sums across every session that client
    has had (a connect/disconnect pair carries that one session's total), not a point-in-time reading."""
    return {"visualizationType": "lnsXY", "state": {
        "datasourceStates": {"formBased": {"layers": {"layer1": {
            "columnOrder": ["bucket", "down", "up"],
            "columns": {
                "down": {"label": "Downloaded", "customLabel": True, "dataType": "number", "operationType": "sum",
                        "isBucketed": False, "scale": "ratio", "sourceField": "unifi.session.usage_down_bytes",
                        "params": {"emptyAsNull": True, **_BYTES_FORMAT}},
                "up": {"label": "Uploaded", "customLabel": True, "dataType": "number", "operationType": "sum",
                      "isBucketed": False, "scale": "ratio", "sourceField": "unifi.session.usage_up_bytes",
                      "params": {"emptyAsNull": True, **_BYTES_FORMAT}},
                "bucket": {"label": title, "customLabel": True, "dataType": "string", "operationType": "terms",
                          "scale": "ordinal", "sourceField": bucket_field, "isBucketed": True,
                          "params": {"size": size, "orderBy": {"type": "column", "columnId": "down"}, "orderDirection": "desc",
                                    "otherBucket": False, "missingBucket": False, "parentFormat": {"id": "terms"},
                                    "include": [], "exclude": [], "includeIsRegex": False, "excludeIsRegex": False}},
            }, "incompleteColumns": {}, "sampling": 1}}}},
        "filters": [], "query": {"query": query, "language": "kuery"},
        "visualization": {"legend": {"isVisible": True, "position": "right"}, "valueLabels": "hide", "fittingFunction": "None",
                          "axisTitlesVisibilitySettings": {"x": False, "yLeft": False, "yRight": True},
                          "tickLabelsVisibilitySettings": {"x": True, "yLeft": True, "yRight": True},
                          "gridlinesVisibilitySettings": {"x": False, "yLeft": True, "yRight": True},
                          "preferredSeriesType": "bar_horizontal",
                          "layers": [{"layerId": "layer1", "accessors": ["down", "up"], "position": "top",
                                     "seriesType": "bar_horizontal", "showGridlines": False, "layerType": "data",
                                     "xAccessor": "bucket", "colorMapping": None}]},
        "adHocDataViews": {}, "internalReferences": []}}


_LENS = {
    "total": lambda: ("Total events", _metric("Events", "")),
    "parse-errors": lambda: ("Unparsed messages", _metric("Unparsed", 'tags: "unifi_parse_error"')),
    "by-category": lambda: ("Events by category", _pie("Category", "unifi.category", "")),
    "by-severity": lambda: ("Events by severity", _pie("Severity", "event.severity_label", "")),
    "top-signatures": lambda: ("Top event names", _bar_terms("Name", "unifi.cef.name", "")),
    "top-devices": lambda: ("Top devices", _bar_terms("Device", "unifi.device.name", "")),
    "over-time": lambda: ("Events over time", _over_time("Category", "unifi.category", "")),
    "client-status": lambda: ("Client devices - most recent status",
                              _client_status_table('unifi.category: "Client Devices"')),
    "clients-per-device": lambda: ("Clients per device (click a bar to filter the dashboard to that device)",
                                   _bar_terms_unique("Device", "unifi.last_connected_device.name", "unifi.client.mac",
                                                     "Distinct clients", 'unifi.category: "Client Devices"')),
    "top-usage": lambda: ("Top clients by data usage", _bar_terms_usage("Client", "unifi.client.alias",
                                                                        'unifi.category: "Client Devices"')),
    "non-cef-by-process": lambda: ("Non-CEF traffic by process (what's actually generating it)",
                                   _bar_terms("Process", "log.syslog.appname", 'tags: "unifi_parse_error"')),
}


def lens_objects(names: Names) -> List[Dict[str, Any]]:
    objs = []
    for key, factory in _LENS.items():
        title, state = factory()
        objs.append({
            "id": names.lens(key), "type": "lens",
            "attributes": {"title": f"{BRAND}{names.label} - {title}", "visualizationType": state["visualizationType"],
                          "state": state["state"]},
            "references": [{"type": "index-pattern", "id": names.data_view, "name": "indexpattern-datasource-layer-layer1"}],
        })
    return objs


def search(names: Names) -> Dict[str, Any]:
    return {
        "id": names.search, "type": "search",
        "attributes": {
            "title": f"{BRAND}{names.label} - Recent events",
            "description": "The most recent UniFi events, newest first. `tags`/`message` lead the column list "
                           "since on a typical install most real volume is per-device debug syslog (hostapd, "
                           "kernel, ...), not CEF Activity Logging - unifi.category/cef.name are often empty "
                           "for those rows by design, not a failure; see unifi.parse_error.",
            "columns": ["@timestamp", "tags", "unifi.category", "unifi.cef.name", "event.severity_label",
                       "observer.hostname", "unifi.device.name", "message"],
            "sort": [["@timestamp", "desc"]], "grid": {}, "hideChart": False, "isTextBasedQuery": False,
            "timeRestore": False,
            "kibanaSavedObjectMeta": {"searchSourceJSON": {"query": {"query": "", "language": "kuery"}, "filter": [],
                                                          "indexRefName": "kibanaSavedObjectMeta.searchSourceJSON.index"}},
        },
        "references": [{"type": "index-pattern", "id": names.data_view, "name": "kibanaSavedObjectMeta.searchSourceJSON.index"}],
    }


def dashboard(names: Names, kibana_version: str) -> Dict[str, Any]:
    order = ["total", "parse-errors", "by-category", "by-severity", "top-signatures", "top-devices"]
    panels = []
    refs = []
    x = 0
    for i, key in enumerate(order[:4], 1):
        panels.append({"version": kibana_version, "type": "lens", "panelIndex": str(i),
                       "gridData": {"x": x, "y": 0, "w": 12, "h": 8, "i": str(i)},
                       "embeddableConfig": {"enhancements": {}}, "panelRefName": f"panel_{i}"})
        refs.append({"name": f"panel_{i}", "type": "lens", "id": names.lens(key)})
        x += 12
    for i, key in enumerate(order[4:6], 5):
        panels.append({"version": kibana_version, "type": "lens", "panelIndex": str(i),
                       "gridData": {"x": (i - 5) * 24, "y": 8, "w": 24, "h": 10, "i": str(i)},
                       "embeddableConfig": {"enhancements": {}}, "panelRefName": f"panel_{i}"})
        refs.append({"name": f"panel_{i}", "type": "lens", "id": names.lens(key)})
    panels.append({"version": kibana_version, "type": "lens", "panelIndex": "7",
                   "gridData": {"x": 0, "y": 18, "w": 48, "h": 14, "i": "7"},
                   "embeddableConfig": {"enhancements": {}}, "panelRefName": "panel_7"})
    refs.append({"name": "panel_7", "type": "lens", "id": names.lens("over-time")})
    panels.append({"version": kibana_version, "type": "lens", "panelIndex": "8",
                   "gridData": {"x": 0, "y": 32, "w": 48, "h": 10, "i": "8"},
                   "embeddableConfig": {"enhancements": {}}, "panelRefName": "panel_8"})
    refs.append({"name": "panel_8", "type": "lens", "id": names.lens("clients-per-device")})
    panels.append({"version": kibana_version, "type": "lens", "panelIndex": "9",
                   "gridData": {"x": 0, "y": 42, "w": 48, "h": 10, "i": "9"},
                   "embeddableConfig": {"enhancements": {}}, "panelRefName": "panel_9"})
    refs.append({"name": "panel_9", "type": "lens", "id": names.lens("top-usage")})
    panels.append({"version": kibana_version, "type": "lens", "panelIndex": "10",
                   "gridData": {"x": 0, "y": 52, "w": 48, "h": 14, "i": "10"},
                   "embeddableConfig": {"enhancements": {}}, "panelRefName": "panel_10"})
    refs.append({"name": "panel_10", "type": "lens", "id": names.lens("client-status")})
    panels.append({"version": kibana_version, "type": "lens", "panelIndex": "11",
                   "gridData": {"x": 0, "y": 66, "w": 48, "h": 10, "i": "11"},
                   "embeddableConfig": {"enhancements": {}}, "panelRefName": "panel_11"})
    refs.append({"name": "panel_11", "type": "lens", "id": names.lens("non-cef-by-process")})
    panels.append({"version": kibana_version, "type": "search", "panelIndex": "12",
                   "gridData": {"x": 0, "y": 76, "w": 48, "h": 22, "i": "12"},
                   "embeddableConfig": {"enhancements": {}, "sampleSize": 500, "rowsPerPage": 20}, "panelRefName": "panel_12"})
    refs.append({"name": "panel_12", "type": "search", "id": names.search})

    return {
        "id": names.dashboard, "type": "dashboard",
        "attributes": {
            "title": f"{BRAND}{names.label} - Overview",
            "description": "UniFi Network events received over syslog: totals, categories, severities, top event "
                           "names and devices, events over time, per-device client connections and bandwidth, "
                           "non-CEF traffic breakdown, and a live table of the most recent events.",
            "panelsJSON": panels,
            "optionsJSON": {"useMargins": True, "syncColors": False, "syncCursor": True, "syncTooltips": False,
                           "hidePanelTitles": False},
            "timeRestore": True, "timeFrom": "now-24h", "timeTo": "now",
            "kibanaSavedObjectMeta": {"searchSourceJSON": {"query": {"query": "", "language": "kuery"}, "filter": []}},
        },
        "references": refs,
    }
