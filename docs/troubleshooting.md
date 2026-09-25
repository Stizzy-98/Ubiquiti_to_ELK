# Troubleshooting

Start with `./install ... --check` for anything Elasticsearch/Kibana-side, and `curl http://<agent>:8080/stats`
for anything about the agent itself. Exit codes: `1` a step failed, `2` a usage/configuration problem, `3` could
not connect, authenticate, or lacking permission.

## Installing

**`the API key is missing permissions: index ubiquiti-*: read/write`**
The key's role does not cover this project's index pattern - very likely you pointed it at a key made for a
different project (Kismet_Cartographer's own key is scoped to `kismet-cartographer-*`, not this one). See
[security.md](security.md#reusing-a-key-from-another-project) for widening a role or making a second key.

**`Kibana already has a data view named ...` / `index '...' already exists and was not created by this installer`**
Something else already uses that name. Use a different `--prefix`, or remove/rename the existing one.

**Certificate, connection, or `--host` vs `--es-url` problems**
Identical to Kismet_Cartographer and Kismet_ADSB_Baseball_Cards - see those projects' troubleshooting guides,
or `./install --help`; the flags, error messages and fixes are the same across all three.

## The agent

**`received` stays at 0 in `/stats`**
Nothing is reaching the agent. Check: UniFi is actually configured to send to the agent's address and port 514
(see [agent-deployment.md](agent-deployment.md)); the Kubernetes Service's external IP is the one UniFi is
pointed at (`kubectl -n elastic get svc ubiquiti-syslog`); nothing between UniFi and the agent is blocking UDP
514 (a firewall, a different VLAN with no route); and, if you changed the agent's `--listen-port`, that the
Service's `targetPort` still matches it.

**`received` climbs but `indexed` does not, or stays far behind**
Elasticsearch is not accepting the batches. Check the agent's logs
(`kubectl -n elastic logs -l app=ubiquiti-syslog-agent`) for a line like `giving up on a batch of N document(s)`,
which names the underlying error - most often the same connection/certificate/permission problems as `./install`
itself, but for whichever credential the agent's own Secret holds (see [agent-deployment.md](agent-deployment.md)).

**The Deployment's Pod will not start / `CrashLoopBackOff`**
`kubectl -n elastic describe pod -l app=ubiquiti-syslog-agent` and `kubectl -n elastic logs ...`. Common causes:
the `ubiquiti-agent-code` ConfigMap or the `ubiquiti-es-credentials` Secret does not
exist yet (both are created by you, not by the YAML - see [agent-deployment.md](agent-deployment.md)), or the
ConfigMap does not have every file from `unifi_cartographer/` in it (re-run the `kubectl create configmap`
command after pulling a code update).

**The `192.168.55.134` address never gets a LoadBalancer IP**
`kubectl -n elastic get svc ubiquiti-syslog` - if `EXTERNAL-IP` stays `<pending>`, that address is not actually
free in your MetalLB pool, or MetalLB itself is not running. Pick a different, genuinely free address and update
both the Service's annotation and where UniFi sends to.

## The dashboard

**Nothing shows in "Ubiquiti Cartographer - Overview"**
The dashboard has a 24-hour time range by default (`timeRestore`); widen it. If still empty, confirm the agent
is receiving and indexing (`/stats`) and that `./install` actually completed (its last line prints the
dashboard's link, meaning the Kibana objects were created).

**A lot of events show "Unparsed messages" / `unifi.parse_error: not_cef`**
UniFi sent something that either is not CEF at all, or the `CEF:` marker was not found. This can be a genuinely
non-CEF log line (some older per-device logging is not CEF - see [data-model.md](data-model.md)), or a sign
UniFi's output format has changed. The full original text is still in `message` on that document - open one in
Discover and compare it with the examples in `tests/test_unifi_cartographer.py`; if it is a real CEF message
this project's parser is missing, see [testing.md](testing.md) for how to report it.

**A field you expected (e.g. a specific `UNIFI*` extension) is missing but the event is there**
Check `unifi.cef.extensions` on that document (Discover's JSON view) - anything this project does not have a
named field for yet is still kept there, just not broken out. See [data-model.md](data-model.md).

**Severity/category values look inconsistent between events**
Expected to a degree: UniFi's own category and severity taxonomy is not something this project could fully
verify (see [testing.md](testing.md)) - the fields hold exactly what the device sent (`UNIFIcategory`, CEF
`Severity`), not a normalised scheme this project imposes.

## Still stuck

`./install ... --dry-run` (or `--check`) prints the addresses, account and versions it found - compare with what
you expect. For the agent, its startup log line states the exact address, port and Elasticsearch URL it is
using; a wrong one there is usually the whole problem.
