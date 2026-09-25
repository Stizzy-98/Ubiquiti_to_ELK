# Architecture

Ubiquiti Cartographer has two independent halves: a one-time **installer** that sets up Elasticsearch and
Kibana, and an always-on **agent** that receives UniFi's events. Nothing here polls or authenticates to a
UniFi console - UniFi only ever pushes to the agent, over plain UDP.

```
                          push, UDP, fire-and-forget                bulk over HTTPS, API key, TLS verified
 ┌────────────────────┐   ┌─────────────────────────────────┐   ┌──────────────────────────────────────────┐
 │ UniFi Network      │   │ unifi_cartographer/agent.py     │   │ Elasticsearch 8.x / 9.x                  │
 │  Activity Logging /│──▶│  1 receive  UDP socket           │──▶│  ubiquiti-events(-v1)       │
 │  Remote Logging    │   │  2 parse    cef.py               │   │  ingest pipeline: -normalize-v1          │
 │  (CEF over syslog) │   │  3 batch    agent.Batcher        │   └──────────────────┬───────────────────────┘
 └────────────────────┘   │  4 index    http.Client.bulk()   │                      │
                          └─────────────────────────────────┘   ┌──────────────────▼───────────────────────┐
                                                                  │ Kibana: data view, charts, dashboard      │
        ./install (once) ──────── creates the pipeline, ─────────▶ (see kibana_objects.py)                  │
                                   index template and index      └────────────────────────────────────────────┘
```

## Components

| Component | File | Responsibility |
|---|---|---|
| Installer | `install` | The one-time setup: argument parsing, preflight checks, and creating the Elasticsearch/Kibana resources. |
| Settings | `unifi_cartographer/config.py` | Flags, environment variables and env files - the same shape Kismet_Cartographer uses. |
| HTTP client | `unifi_cartographer/http.py` | Standard library only: plain HTTP, HTTPS with your CA, `--tls-server-name`, retry with backoff, Kibana spaces. |
| Names | `unifi_cartographer/names.py` | Every resource name comes from one prefix; anything outside it is refused. |
| Parsing | `unifi_cartographer/cef.py` | The syslog envelope and CEF message parser. Pure functions, no I/O, heavily unit-tested. |
| Elasticsearch shapes | `unifi_cartographer/mappings.py` | The index mapping and the ingest pipeline body. |
| Kibana objects | `unifi_cartographer/kibana_objects.py` | The data view, charts, saved search and dashboard. |
| Agent | `unifi_cartographer/agent.py` | The long-running process: UDP socket, batching, bulk indexing, a tiny health endpoint. |

## Why these choices

* **Two halves, not one.** The installer runs once, from wherever is convenient, and exits. The agent has to
  run forever, somewhere UniFi can always reach, which is a completely different operational shape - trying to
  make one script do both would mean either running the installer's cluster-admin-ish credential continuously
  (bad) or the agent's narrow credential could not create its own index on first use (also bad). Splitting them
  is the same reasoning Kismet_Cartographer applies to `./install` vs `./ingest`.
* **UDP push, not a polling API.** UniFi does not expose a way to pull these particular event categories; it
  only sends them, unprompted, to a configured address. The agent is a receiver, not a client, and needs no
  UniFi credential at all - see [agent-deployment.md](agent-deployment.md).
* **Parsing in Python, the ingest pipeline as a safety net.** The same split Kismet_Cartographer uses: CEF's
  escaping rules and its "one key, then the next key, so a value can contain spaces" extension format are
  fiddly to get right in a `grok`/`dissect` pipeline processor and awkward to unit-test there. Doing it in
  Python means every parsing rule has a plain function and a test; the pipeline is left to do what pipelines
  are actually good at - `geoip` lookups, the MAC-vendor `enrich` lookup against the IEEE OUI registry
  `./install` loads, and a couple of conditional `set`/`append` processors - which are also unit- and
  self-tested (`./install` runs a real document through `_simulate` before it finishes).
* **`dynamic: false`, with one `flattened` catch-all.** UniFi's exact set of CEF extension keys is not
  something this project could get a full specification for (see [data-model.md](data-model.md)); more will
  turn up over time. A strict mapping would reject a whole batch over one new field; a fully dynamic one risks
  a type conflict (the same key appearing as a number in one message and text in another) rejecting documents
  too. `dynamic: false` plus `unifi.cef.extensions` as a single `flattened` field accepts anything without
  either failure mode, at the cost of that field not being aggregatable the way a properly mapped one would be.
* **A write alias in front of a versioned index**, exactly like Kismet_Cartographer: `ubiquiti-events`
  points at `ubiquiti-events-v1` today, so a future mapping change that cannot be done in place
  becomes a `-v2` and a reindex, never a rename the agent has to know about.
* **The agent never retries forever.** UDP already has no delivery guarantee; holding a batch in memory across
  a long Elasticsearch outage would just delay an eventual, larger loss and risk running the process out of
  memory. It retries a few times with backoff, then drops the batch and counts it - visible in `/stats` and in
  the dashboard's "Unparsed messages"-style visibility (see `docs/troubleshooting.md`).
* **Python standard library only, in both halves.** The agent in particular runs as a long-lived container;
  not needing `pip install` means the official `python:3.13-slim` image can run it unmodified, with the code
  supplied by a ConfigMap - see [agent-deployment.md](agent-deployment.md).

## Known limitations

* **No delivery guarantee.** See "UDP push" above - this is inherent to how UniFi sends these events, not a
  gap in this project.
* **The CEF extension field names UNIFI\* are not from an Ubiquiti specification.** They are what real,
  published UniFi output uses (see [data-model.md](data-model.md)); Ubiquiti could add, rename or restructure
  them in a future release without notice. A field this project does not recognise is still kept, just not
  broken out.
* **One agent replica.** UDP load-balancing across replicas would need something outside plain Kubernetes
  Services (they do not load-balance UDP the way they do TCP without extra configuration), and there is no
  ordering or session state to preserve across replicas anyway, so one is the simplest correct answer for a
  home/lab-scale volume of events.
* **The `UNIFI*` extension keys are not even stable within one category.** A real capture showed `"Wired
  Client Connected"` and `"Wired Client Disconnected"` - the same category, the same console - using
  genuinely different key names (`UNIFIconnectedToDevice*` vs `UNIFIlastConnectedToDevice*`) for the same
  concept. Treat any single captured example as one data point, not the whole vocabulary for its category -
  see [data-model.md](data-model.md).
* See [testing.md](testing.md) for exactly what has and has not been verified against a live UniFi console
  and a real Elastic Stack, and how.
