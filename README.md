# Ubiquiti Cartographer

Turn the events your [UniFi Network](https://www.ui.com/) console can send over syslog - Admin Activity, Client
Events, Critical Events, Devices, Security Detections, Triggers, Updates, and per-device logging for the
Gateway, Access Points and Switches, including Debug level - into a Kibana dashboard.

* One command, `./install`, sets up Elasticsearch and Kibana: the ingest pipeline, the index, a MAC-vendor
  lookup built from the real IEEE OUI registry, and the dashboard.
* A separate, always-on **agent** receives the events. UniFi does not offer a pull API for these logs; it pushes
  them as one UDP datagram per event, so the agent listens on a fixed address on your network - see
  [docs/agent-deployment.md](docs/agent-deployment.md).
* Same connection flags as [Kismet_Cartographer](../Kismet_Cartographer)'s `./install`: an API key, a CA
  certificate if your stack uses a private one, and the two URLs. A key you already made for Kismet_Cartographer
  works here too, once its role also covers `ubiquiti-*` - see [docs/security.md](docs/security.md).
* Python 3.9+ standard library only, in both the installer and the agent. No pip packages, no external
  ingest tool.

> **What this reads.** UniFi's events, once you point its remote logging at the agent. No UniFi API key,
> account or cloud access is used or needed - see [docs/agent-deployment.md](docs/agent-deployment.md).

## How it fits together

```
UniFi Network  --UDP syslog (CEF)-->  ubiquiti-syslog-agent  --bulk over HTTPS-->  Elasticsearch  -->  Kibana
  (pushes)          port 514              (this repo)          ingest pipeline      dashboard
```

## Install

You need Elasticsearch and Kibana **8.x or 9.x** reachable from wherever you run `./install`, and somewhere to
run the agent continuously. `./install` only ever talks to their HTTP APIs, so where they run does not matter:

* **Kubernetes / ECK** - developed and tested against this. `k8s/ubiquiti-syslog-agent.yaml` deploys the
  agent; if you manage your ECK stack as one values file the way `../eck_stack_values.yaml` in this repo's
  parent folder does, the same Deployment/Service can be appended to it instead - see [docs/agent-deployment.md](docs/agent-deployment.md).
* **Standard ELK** (package, Docker, a VM) - `./install` works unchanged; run the agent as an ordinary process
  (systemd, a container) on any machine that can reach both UniFi and Elasticsearch.
* **Elastic Cloud** - `./install --es-url <your deployment's endpoint> --kibana-url <...> --api-key-file ...`
  should work (Cloud uses the same APIs and a publicly trusted certificate, so no `--ca` is needed); **this has
  not been tested** against a real Cloud deployment. The agent still needs somewhere of your own to run, since
  Cloud does not host arbitrary containers for you.

### 1. Create an API key

Its role needs to cover `ubiquiti-*`, which is not the same pattern Kismet_Cartographer's own key
uses (`kismet-cartographer-*`) - see [docs/security.md](docs/security.md) for why and how to widen an existing
key's role instead of creating a second one, if you would rather. To make a new one: in Kibana, **Dev Tools ->
Console**, paste the output of

```bash
./install --print-api-key-request
```

run it, and copy the `encoded` value into a file (`chmod 600`).

### 2. Get the CA certificate

Same as any Elastic Stack tool - skip this if your certificate is from a public CA or you do not use HTTPS.
See [docs/installation.md](docs/installation.md) for where to find it (standard install, Docker, Kubernetes/ECK).

### 3. Run the installer

```bash
./install --es-url https://elasticsearch.example.com:9200 --kibana-url https://kibana.example.com:5601 \
           --api-key-file ~/uc.key --ca ./ca.crt --save-config
```

No HTTPS: `./install --host 192.168.1.20 --api-key-file ~/uc.key`. Add `--dry-run` first if you like. This
creates the ingest pipeline, the index, and the Kibana dashboard - and nothing else needs to exist yet.

### 4. Deploy the agent

The agent is the part that actually receives events; the dashboard stays empty until it is running and UniFi
is pointed at it. Full steps, including the exact UniFi menu and the `kubectl` commands, are in
[docs/agent-deployment.md](docs/agent-deployment.md):

```bash
kubectl create configmap ubiquiti-agent-code -n elastic --from-file=unifi_cartographer/
kubectl create secret generic ubiquiti-es-credentials -n elastic \
    --from-literal=api-key='<the same key, or a write-only one>' --from-file=ca.crt=./ca.crt
kubectl apply -f k8s/ubiquiti-syslog-agent.yaml
```

### 5. Point UniFi at it, and open the dashboard

In the UniFi Network application, turn on remote logging (Settings -> Control Plane -> Integrations ->
Activity Logging, or on older UniFi OS versions Settings -> System -> Advanced Features -> Remote Logging) and
enter the agent's address and port 514/UDP. `./install` printed the dashboard's link at the end; find it again
under **Dashboards -> "Ubiquiti Cartographer - Overview"**.

## What you get

* **Overview dashboard** - total events, an "unparsed messages" count (so a format change or a bug shows up
  immediately), events by category and by severity, the busiest event names and devices, events over time,
  which clients are connected to which switch/AP right now (with vendor, link speed and network), top clients
  by data usage, a breakdown of what's generating non-CEF traffic, and a live table of the most recent events.
* Every event keeps its original text (`message`) and, when it parses as CEF, its device, client, category and
  severity - plus anything left over from the message that this project does not have a named field for, so
  nothing UniFi sends is ever silently dropped. A client's manufacturer (`unifi.client.vendor`) is resolved
  from its MAC address against the real IEEE OUI registry `./install` loads - `--skip-vendor-lookup` to opt out.
* **Even non-CEF traffic gets structured**: UniFi hardware also streams its own per-device debug syslog
  (hostapd, kernel, ...) when device-level logging is on - not CEF, but still recognised and broken into
  fields (which device, which process) rather than left as an opaque line of text.

## Documentation

| | |
|---|---|
| [docs/installation.md](docs/installation.md) | the API key, CA certificates, all installer options |
| [docs/agent-deployment.md](docs/agent-deployment.md) | the UniFi side, the Kubernetes manifest, running the agent anywhere else |
| [docs/architecture.md](docs/architecture.md) | how the pieces fit together, and why |
| [docs/data-model.md](docs/data-model.md) | the CEF format, every field, the sources for it |
| [docs/security.md](docs/security.md) | permissions, credentials, what leaves your network |
| [docs/testing.md](docs/testing.md) | the unit tests and how this was actually checked |
| [docs/troubleshooting.md](docs/troubleshooting.md) | nothing arriving, parse errors, connection errors |

## Repository layout

```
install                          the one-time Elasticsearch/Kibana setup
unifi_cartographer/               the library and the agent (standard library only)
  agent.py                        the long-running syslog receiver
  cef.py                          parsing: syslog envelope + CEF (pure functions, heavily unit-tested)
k8s/ubiquiti-syslog-agent.yaml    Deployment + static-IP Service for the agent
docs/  tests/
```

## Tests

```bash
python3 -m unittest discover -s tests
```

Runs offline in about a second. See [docs/testing.md](docs/testing.md) for what is - and is not - covered:
in short, the parsing is tested against real UniFi CEF messages (both published examples and live-console
captures), the agent was tested end-to-end against a stand-in Elasticsearch, and `./install` itself has been
run against a real Elastic Stack.

## Known limitations

* UniFi's remote logging is UDP: if the agent or Elasticsearch is briefly unreachable, events from that window
  are gone, the same as unplugging any syslog server. Nothing here can change that.
* Some CEF extension keys are named and mapped from real published examples, not a specification Ubiquiti
  documents publicly; a field this project does not recognise is still kept (`unifi.cef.extensions`), just not
  broken out into its own named field until it has been seen. See [docs/data-model.md](docs/data-model.md).
* One agent replica: UDP has no natural way to share load or preserve ordering across several.
* Developed and tested on Elasticsearch/Kibana 9.4.2. Other 8.x/9.x versions should work but are untested.
* The MAC-vendor lookup needs cluster privilege `manage_enrich` and one outbound HTTPS request to
  `standards-oui.ieee.org` at install time (see [docs/security.md](docs/security.md)) - use
  `--skip-vendor-lookup` for an air-gapped install or to avoid the extra privilege.

Ubiquiti and UniFi are trademarks of Ubiquiti Inc.; this project is independent and not affiliated with either
Ubiquiti or Elastic.
