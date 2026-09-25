# Testing

## Unit tests (no Elasticsearch, no Kibana, no UniFi console)

```bash
python3 -m unittest discover -s tests
```

46 tests run in under two seconds and need nothing but Python. They check:

* **CEF parsing** against real UniFi CEF messages, both from public documentation and from a live console
  capture (see below) - every standard and `UNIFI*` extension field maps to the field documented in
  [data-model.md](data-model.md), severity bands are correct, and nothing left unmapped is dropped.
* **Malformed input never raises or drops the message**: no `CEF:` marker, a truncated header (fewer than the
  full seven pipe-delimited fields - a bug UniFi's own community forum has reported), invalid UTF-8, an empty
  datagram, escaped pipes/equals signs inside a field.
* **The syslog envelope**: RFC 3164 and RFC 5424 headers (with and without structured data) are stripped
  correctly, and a bare CEF message with no envelope at all still parses.
* **UniFi's own non-CEF per-device syslog tag** (`<mac>,<model>-<version>: <process>[<pid>]: `, seen ahead of
  hostapd/kernel/mcad/udhcpc messages) is recognised and its fields extracted, including a real regression case
  (a trailing newline UniFi hardware actually sends) and real cases that are deliberately left unparsed (a
  double- or empty-tagged daemon line) rather than a guessed shape.
* **The agent's batching and retry logic** (`agent.Batcher`), against a fake Elasticsearch client: flushes at
  the configured size and age, retries a failed batch with backoff, and gives up and counts a dropped batch
  after too many failures - all without a real network call.
* **The index mapping covers every field** the parser can actually produce, checked against every fixture
  message including the non-CEF ones (so the mapping and the parser cannot silently drift apart).
* **Kibana object definitions** stay under the `--prefix` namespace, reference the right data view, and every
  dashboard panel has a matching reference.
* **Settings and the installer as a user would run it**: connection-flag precedence, that `--save-config` never
  writes the key, `--print-api-key-request` produces a request scoped to one index pattern and one space (with
  or without the vendor-lookup `manage_enrich` privilege, depending on `--skip-vendor-lookup`), and that bad
  flags or an unreachable stack exit with the documented code.

## What was checked against a real Elastic Stack

* **`./install` was run for real** (`--skip-vendor-lookup`, since the installer's own long-lived credential is
  not routinely granted `manage_enrich` - see below) against this project's own cluster: the ingest pipeline,
  index template and index were created, the pipeline's self-test passed, and the data view, all 12 Lens
  panels, the saved search and the dashboard were created in Kibana. Every dashboard panel reference was then
  independently verified to resolve to a real saved object, and the live index mapping was fetched and checked
  to actually contain every new field (`unifi.client.alias/hostname/ip/oui/vendor`,
  `unifi.last_connected_device.*`, `unifi.session.*`, `network.name/subnet/vlan`) - not just that the code
  claims to add them.
* **The MAC-vendor lookup's underlying mechanism (IEEE registry download, bulk load, enrich policy, enrich
  processor) was proven live** on this same cluster, using the same approach `ensure_oui_vendor_lookup` in
  `install` now automates, and cross-checked against a real device (a MAC's resolved vendor matched the
  device it actually belonged to). **`ensure_oui_vendor_lookup` itself has not yet been run end-to-end** through
  `./install` on a key holding `manage_enrich` - the installer's own long-lived credential was not granted that
  privilege for this round of testing. Re-run without `--skip-vendor-lookup` once a `manage_enrich`-holding key
  is available to close this gap.
* `./install --check` was also run against a key deliberately scoped to a different project
  (Kismet_Cartographer's, `kismet-cartographer-*`) and correctly reported the exact missing permissions - this
  is also how the connection, TLS and authentication code paths were exercised.
* **The agent was run end-to-end for real**: as an actual subprocess, binding a real UDP socket, receiving a
  real UDP datagram containing one of the CEF fixtures sent over the loopback interface, batching it, and
  successfully bulk-indexing the resulting document to a stand-in HTTP server acting as Elasticsearch (using
  this project's own `http.py` client, the same one two related projects have used against this real cluster).
  The `/healthz` and `/stats` endpoints and a clean shutdown-and-flush on `SIGTERM` were confirmed the same way.

## What was not checked - and why

* **`ensure_oui_vendor_lookup` end-to-end through `./install`** - see above; the mechanism is proven, the
  installer's own automation of it is not yet, for lack of a `manage_enrich`-holding key at test time.
* **The exact UniFi menu wording** in [agent-deployment.md](agent-deployment.md) is assembled from documentation
  of several UniFi OS/Network versions, which describe the feature slightly differently from each other; it has
  not been confirmed against any specific console.
* Elasticsearch/Kibana 8.x, Elastic Cloud, a non-default Kibana space, and Python versions other than what runs
  in this environment.
* **Wireless client connect/disconnect events** (`"WiFi Client Connected"`/`"Disconnected"`) - a real
  `"WiFi Client Connected"` capture was seen and does reuse the same `unifi.last_connected_device.*`/`client.*`
  fields as the wired events (confirmed against one real example: an AP, not a switch, in `.name`), but no
  `"WiFi Client Disconnected"` capture, and no capture of `unifi.ssid`/`unifi.ap_name` actually populating on a
  connect/disconnect event specifically, has been seen yet - worth checking against those UNIFI* keys' exact
  names once a real example turns up.
* A genuinely new CEF category this project has never seen (e.g. Admin Activity, Security Detections) would
  very likely use its own extension key names, the same way `"Wired Client Connected"` turned out to use
  different ones than `"Disconnected"` - see [data-model.md](data-model.md). `unifi.cef.extensions` and the
  "Unparsed messages" dashboard panel exist exactly for this: nothing is dropped, and anything unrecognised is
  visible rather than silently miscategorised.

## If you have a real UniFi console

The single highest-value thing you can do to firm this project up is turn on remote logging for one category,
point it at a throwaway UDP listener (`nc -ul 514`, or the agent itself with `--dry-run`-style logging - it
already prints nothing per-message today, so redirecting `/stats` before and after is the easiest check), and
compare a real captured message against the fixtures in `tests/test_unifi_cartographer.py`. A field this project
gets wrong is a one-line fix in `unifi_cartographer/cef.py`'s extension map, backed by a new test using your
real example - please open an issue or a pull request with the (sanitised - see [security.md](security.md) for
what these messages can contain) raw line if you find one.
