# Deploying the agent

`./install` only sets up Elasticsearch and Kibana. This is the other half: the always-on process that actually
receives UniFi's events and the UniFi-side configuration that sends them to it.

## Why a fixed address

UniFi does not offer a way to *pull* these events - no REST endpoint, no export file. It only *pushes* them,
one UDP datagram per event, to an address and port you configure once in the UniFi Network application. That
address has to be something UniFi can always reach, which is why the Kubernetes manifest gives the agent a
static LAN IP (matching the pattern this cluster already uses for the pfSense syslog agent in
`../eck_stack_values.yaml`) rather than an ordinary, address-changes-on-restart Pod.

## On Kubernetes (the included manifest)

`k8s/ubiquiti-syslog-agent.yaml` is a Deployment (the agent, `python3 -m unifi_cartographer.agent`, running in
a stock `python:3.13-slim` image - the agent is pure standard library, so no image needs to be built) and a
LoadBalancer Service that gives it a fixed address via MetalLB, **192.168.55.134** by default.

**If you manage your ECK stack as one values file** the way `../eck_stack_values.yaml` does, the same Deployment
and Service can simply be appended to it instead of applying `k8s/ubiquiti-syslog-agent.yaml` as a separate
file - the two are identical content, just placed differently. Everything below (the ConfigMap, the Secret, the
UniFi-side configuration) is the same either way.

**Before applying it:**

1. **Confirm that IP is free.** It is simply the next address after the `.131`/`.132`/`.133` already used in
   `../eck_stack_values.yaml`; this project has no way to see your actual MetalLB pool or DHCP reservations.
   Change the `metallb.universe.tf/loadBalancerIPs` annotation in the Service if `.134` is taken.
2. **Create the ConfigMap holding the agent's code** (so the Deployment needs no image build or registry):
   ```bash
   kubectl create configmap ubiquiti-agent-code -n elastic \
       --from-file=unifi_cartographer/
   ```
   Re-run this (it will need `--dry-run=client -o yaml | kubectl apply -f -`, or delete-and-recreate) whenever
   you pull a change to the `unifi_cartographer/` package, then roll the Deployment
   (`kubectl rollout restart deployment/ubiquiti-syslog-agent -n elastic`) to pick it up.
3. **Create the Secret holding its Elasticsearch credential** - never put this in a file that gets committed.
   Use a separate, much narrower key than the one `./install` itself uses: the agent only ever *creates* new
   documents (see [security.md](security.md)), so it needs no cluster privileges and no Kibana access at all.
   ```bash
   ./install --print-agent-key-request        # paste into Kibana Dev Tools, run it, copy the "encoded" value
   kubectl create secret generic ubiquiti-es-credentials -n elastic \
       --from-literal=api-key='<the encoded value from that request>' \
       --from-file=ca.crt=./ca.crt
   ```
4. **Apply it:**
   ```bash
   kubectl apply -f k8s/ubiquiti-syslog-agent.yaml
   kubectl -n elastic get pods -l app=ubiquiti-syslog-agent
   kubectl -n elastic logs -l app=ubiquiti-syslog-agent -f
   ```
   The log line `listening for UniFi syslog on udp 0.0.0.0:5514, indexing into 'ubiquiti-events' at
   https://...` means it is up. `kubectl -n elastic port-forward deploy/ubiquiti-syslog-agent 8080:8080` then
   `curl localhost:8080/stats` shows live counters (`received`, `indexed`, `parse_errors`, `dropped`).

## Point UniFi at it

In the UniFi Network application:

* **UniFi OS 9.x and newer:** Settings -> Control Plane -> Integrations -> Activity Logging (sometimes shown as
  a "SIEM Server" toggle). Enable it, choose the categories you want (Admin Activity, Clients, Critical,
  Devices, Security Detections, Triggers, Updates), and set the server address to the agent's IP
  (`192.168.55.134` if you used the default) and port `514`, protocol UDP.
* **Older UniFi Network versions:** Settings -> System -> Advanced Features -> Remote Logging. The category
  names differ slightly (Admin Activity, Client Events, Critical Events, Device Status, Security Detections,
  System Updates, Network Triggers, VPN Activity) but the mechanism is the same: server address, port (default
  514, can be changed), UDP only.
* **Per-device logging** (Gateway, Access Points, Switches, and a Debug verbosity level) is a separate setting,
  usually per device or device profile rather than the one Control Plane toggle above.

The exact menu wording has moved between UniFi releases and was not something this project could confirm
against a live console (see [testing.md](testing.md)); if your version's screen looks different, the underlying
setting is always "remote syslog server address and port" - point it at the agent and pick whichever categories
you want. Everything the agent receives is kept - even a category or a message shape not described here.

## Confirm it is working

* `curl http://<agent-ip>:8080/stats` (from a machine that can reach it) or the port-forward above:
  `received` should climb whenever UniFi sends anything, and `indexed` should track it closely.
* **Kibana -> Discover**, data view "Ubiquiti Cartographer", sorted by time descending: events should appear
  within a few seconds.
* If `received` climbs but `indexed` does not, or the dashboard's "Unparsed messages" panel is high, see
  [troubleshooting.md](troubleshooting.md).

## Running the agent somewhere other than Kubernetes

It is one process with no dependencies:

```bash
python3 -m unifi_cartographer.agent --es-url https://elasticsearch.example.com:9200 --ca ./ca.crt \
    --api-key-file ~/uc.key --listen-host 0.0.0.0 --listen-port 514
```

Binding UDP port 514 directly needs root (or, on Linux, `CAP_NET_BIND_SERVICE`); as an unprivileged user, pick
a port above 1024 and forward 514 to it however your platform does that. Run it under whatever keeps a process
alive on your system (systemd, a container restart policy, ...) - it does not daemonize or retry its own
crashes. `python3 -m unifi_cartographer.agent --help` lists every option, including `--batch-size` and
`--batch-seconds` (how often it flushes to Elasticsearch) and `--health-port` (`/healthz`, `/stats`).

## Elastic Cloud

Cloud only hosts Elasticsearch and Kibana, not arbitrary containers, so the agent still needs a machine of your
own to run on (a small VM is enough) - point it at your deployment's endpoints instead of a self-managed URL:

```bash
python3 -m unifi_cartographer.agent --es-url https://<deployment-id>.es.<region>.gcp.elastic-cloud.com:443 \
    --kibana-url https://<deployment-id>.kb.<region>.gcp.elastic-cloud.com:443 --api-key-file ~/uc.key
```

No `--ca` is needed (Cloud's certificate is publicly trusted). `./install` should work the same way. **This
project has not been tested against a real Elastic Cloud deployment** - see [testing.md](testing.md).
