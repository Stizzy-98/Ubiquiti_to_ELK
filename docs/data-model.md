# Data model

One index, `ubiquiti-events` (an alias; the concrete index is `-v1`, following `--prefix`), holds
one document per event received over syslog. The mapping is `dynamic: false`: an unrecognised field is kept in
`_source` but never creates a new mapped field, so it cannot explode the mapping or get a document rejected.

## Where the fields come from

UniFi's remote logging sends [CEF (Common Event Format)](https://www.microfocus.com/documentation/arcsight/arcsight-smartconnectors/pdfdoc/common-event-format-v25/common-event-format-v25.pdf)
messages, wrapped in an ordinary syslog envelope. A real example (from public documentation of UniFi's
remote-logging feature; this project has not captured one from a live console - see [testing.md](testing.md)):

```
CEF:0|Ubiquiti|UniFi Network|9.4.19|201|Threat Detected and Blocked|7|proto=TCP src=10.0.0.100 spt=52331
dst=192.168.0.233 dpt=443 UNIFIcategory=Security UNIFIsubCategory=Intrusion Prevention UNIFIhost=Express 7
UNIFIdeviceMac=84:78:48:80:0d:86 UNIFIdeviceName=Express 7 UNIFIdeviceModel=UX7 UNIFIdeviceIp=192.168.0.1
UNIFIdeviceVersion=4.3.9 UNIFIrisk=medium
```

CEF's own header is seven pipe-separated fields (`CEF:Version|Vendor|Product|Version|Signature ID|Name|
Severity`), then an `extension` of `key=value` pairs. A handful of extension keys are CEF's own standard
vocabulary (`proto`, `src`, `spt`, `dst`, `dpt`, ...); everything UniFi-specific is its own `UNIFI<Name>` key.
Both are mapped onto named fields here; anything else is kept under `unifi.cef.extensions` rather than
discarded, because this project has not been able to get a complete list of every key UniFi might send (see
[testing.md](testing.md) for the sources this was built from).

## Fields

| Field | Type | Meaning |
|---|---|---|
| `@timestamp` | date | When the agent received the datagram (not the device's own clock - see below). |
| `message` | text | The full original line, exactly as received, decoded as UTF-8. Always present, whatever else parsed. |
| `event.ingested` | date | Same as `@timestamp` - when the agent read the packet. |
| `event.created` | date | When the Elasticsearch ingest node processed it; the gap between this and `event.ingested` is queueing/network delay. |
| `event.dataset` | keyword | Always `ubiquiti.unifi`. |
| `event.severity` | integer | CEF severity, 0-10. |
| `event.severity_label` | keyword | `low` (0-3), `medium` (4-6), `high` (7-8) or `very-high` (9-10) - CEF's own bands. |
| `log.source.address`, `log.source.port` | ip, integer | The UDP sender's real address (preserved end to end by the Kubernetes Service's `externalTrafficPolicy: Local`). |
| `log.syslog.rfc` | keyword | `3164`, `5424`, `3164_no_header` (a 3164-style body with no timestamp/host it could confidently extract) or `none` (no syslog envelope at all - just a bare CEF message). |
| `log.syslog.hostname`, `.appname`, `.procid`, `.msgid` | keyword | Whatever the syslog envelope carried; UniFi is not consistent about all of them being present. |
| `log.syslog.facility.code`, `.severity.code` | short | Decoded from the syslog `<PRI>` value, when there is one. |
| `observer.hostname` | keyword | The device name from the syslog envelope, if present. |
| `observer.vendor`, `.product`, `.version` | keyword | CEF header: `Ubiquiti`, `UniFi Network`, and the console's software version. |
| `network.transport` | keyword | CEF `proto`. |
| `source.ip`, `.port`, `.hostname` | ip, integer, keyword | CEF `src`/`spt`/`shost`. |
| `destination.ip`, `.port`, `.hostname` | ip, integer, keyword | CEF `dst`/`dpt`/`dhost`. |
| `source.geo`, `destination.geo` | geo_point | Added by the ingest pipeline's `geoip` processor when the address is public; absent for LAN traffic (there is nothing to look up). |
| `unifi.category`, `.subcategory` | keyword | CEF `UNIFIcategory` / `UNIFIsubCategory`, e.g. `Security` / `Intrusion Prevention`. |
| `unifi.host` | keyword | CEF `UNIFIhost` - the console's own name for itself, which can differ from `observer.hostname`. |
| `unifi.access_method` | keyword | CEF `UNIFIaccessMethod` (seen on admin-activity events, e.g. `web`). |
| `unifi.risk` | keyword | CEF `UNIFIrisk` (seen on security-detection events, e.g. `medium`). |
| `unifi.ssid`, `.ap_name` | keyword | CEF `UNIFIssid` / `UNIFIapName` (seen on Wi-Fi client events). |
| `unifi.client.mac`, `.name`, `.alias`, `.hostname`, `.ip` | keyword, keyword, keyword, keyword, ip | CEF `UNIFIclientMac`/`Name`/`Alias`/`Hostname`/`Ip` - real captures show `alias`/`hostname`/`ip` on client connect/disconnect events, `name` on older/other event shapes; not always all present together. |
| `unifi.client.oui`, `.vendor` | keyword, keyword | The client MAC's first 6 hex chars, and the manufacturer name resolved from it via the IEEE OUI registry the installer loads (`--skip-vendor-lookup` to opt out - see [security.md](security.md)). A locally-administered (randomised) MAC has no real vendor to find; phones/tablets often show blank here on purpose, not a bug. |
| `unifi.device.mac`, `.name`, `.model`, `.ip`, `.version` | keyword, keyword, keyword, ip, keyword | CEF `UNIFIdevice*` on CEF events - the network device (gateway/AP/switch) the event is about, not necessarily the console. Also populated (mac/model/version only) from the non-CEF per-device syslog tag - see below. |
| `unifi.last_connected_device.name`, `.ip`, `.mac`, `.model`, `.version`, `.port`, `.link_speed` | keyword, ip, keyword, keyword, keyword, integer, keyword | Which switch/AP a client is/was connected through, and at what speed. A real capture showed **two different CEF key families for this same concept**: `UNIFIconnectedToDevice*` on a "Connected" event, `UNIFIlastConnectedToDevice*` on its "Disconnected" counterpart - both map here. `UNIFIlinkSpeed` -> `.link_speed`. |
| `unifi.session.duration`, `.usage_down`, `.usage_up` | keyword | CEF `UNIFIduration`/`UNIFIusageDown`/`UNIFIusageUp`, kept verbatim as UniFi formats them (e.g. `"20m 2s"`, `"43.99 KB"`). |
| `unifi.session.usage_down_bytes`, `.usage_up_bytes` | double | The same usage strings parsed to a byte count (1024-based, matching UniFi's own display convention) so they can actually be summed/charted - absent if the string did not match the expected `"<number> <unit>"` shape. |
| `network.name`, `.subnet`, `.vlan` | keyword | CEF `UNIFInetworkName`/`Subnet`/`Vlan` - which network/VLAN the event's client is on, alongside `network.transport` from the standard CEF `proto` key. |
| `unifi.cef.signature_id`, `.name` | keyword | CEF's own `Signature ID` and `Name` fields - the closest thing to an event type, e.g. `201` / `Threat Detected and Blocked`. |
| `unifi.cef.extensions` | flattened | Any extension key not in the list above, kept whole so nothing is lost. |
| `unifi.cef.severity_raw` | keyword | Only set if the CEF `Severity` field was not a plain number (should not normally happen). |
| `unifi.parse_error` | keyword | `not_cef_envelope_parsed` (no `CEF:` marker, but UniFi's own per-device syslog tag was recognised - see below), `not_cef` (neither), or `empty_message`. Set instead of the CEF fields above, never alongside them; `message` still has the original text either way. |
| `tags` | keyword | `unifi_parse_error` when `unifi.parse_error` is set to any of the above (added by the ingest pipeline, so it is filterable without knowing to look for the field). |

### The non-CEF per-device syslog UniFi hardware also sends

Not every UniFi datagram is a CEF Activity Logging event. Devices also stream their own low-level debug
syslog (hostapd, kernel, mcad, udhcpc, ...) when per-device logging is enabled - on a typical install this is
most of the *volume*, even though it carries none of the structured CEF fields above. A real example, straight
from a U7-Pro access point:

```
<13>Sep 24 19:54:14 U7-Pro 8cede1889798,U7-Pro-8.7.11+19419: hostapd[4429]: wifi1ap1: DPP-RX src=ca:b1:48:0e:a6:43 freq=5745 type=13
```

UniFi wraps this in its own device tag - `<mac>,<model>-<version>: <process>[<pid>]: ` - ahead of the actual
message, a convention that is not part of RFC 3164/5424 and so needed its own parser layered on top of the
standard syslog envelope handling. When recognised, it fills in `unifi.device.mac`/`.model`/`.version` and
`log.syslog.appname`/`.procid` even though there is no CEF to parse; `unifi.parse_error` is set to
`not_cef_envelope_parsed` rather than `not_cef` to distinguish "not CEF, but still understood" from "not
recognised at all". Confirmed against real captures from both an access point and a switch; a handful of
double-tagged daemon lines (e.g. `mcad: mcad[981]:`) are left as plain `not_cef` rather than chased further.

## Why `@timestamp` is the agent's clock, not the device's

The syslog envelope carries the device's own idea of the time, which this project does not trust as the
authoritative timestamp: many UniFi devices have no battery-backed clock and can be badly wrong after a reboot
before NTP catches up. `@timestamp` is always when the agent actually received the datagram. The device's
reported time is not currently kept as a separate field (`log.syslog` does not include a parsed timestamp) - if
you need it, `message` still has the original line to read it from.

## Malformed input

Nothing is dropped or crashes the agent:

* **No syslog envelope at all** - fine; `log.syslog.rfc` is `none` and parsing continues straight into CEF detection.
* **No `CEF:` marker anywhere in the message** - `unifi.parse_error: not_cef`; the raw text is still indexed as `message`.
* **A truncated or malformed CEF header** (UniFi OS has been reported, in its own community forum, to
  occasionally miswrite one) - whatever pipe-delimited fields are present are used; missing ones are simply
  absent, nothing raises.
* **Invalid UTF-8 bytes** - decoded with `errors="replace"`; the rest of the message still parses normally.

## Sources used to build this

Ubiquiti does not publish a machine-readable schema for these messages. This mapping was built from: the CEF
specification itself (the header/extension format is a public standard); Ubiquiti's own help-center article on
this feature (menu path and category names - see [agent-deployment.md](agent-deployment.md)); real example CEF
messages published in third-party documentation of UniFi's remote logging output (the original test fixtures in
`tests/test_unifi_cartographer.py`); and, since then, **real captures from a live UniFi console and access
point/switch** - `"Wired Client Connected"`/`"Disconnected"`, the non-CEF per-device syslog tag format above,
and the usage/network/link-speed extension fields. Those real captures are exactly what exposed that
`"Connected"` and `"Disconnected"` events use different extension key names for the same concept (see
`unifi.last_connected_device.*` above) - a mismatch the public-docs fixtures alone would never have shown, and
the reason to treat any single example, however official-looking, as one data point rather than the whole
vocabulary. See [testing.md](testing.md) for which fixtures are real captures vs. public documentation, and
how confident to be in a field this project has not yet seen a live example of.
