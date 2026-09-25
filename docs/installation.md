# Installation

The quick version is in the [README](../README.md). This covers the API key, the CA certificate, and every
`./install` option. It sets up Elasticsearch and Kibana only; deploying the agent that actually receives
events is a separate step, in [agent-deployment.md](agent-deployment.md).

## Requirements

* **Python 3.9+** wherever you run `./install`. Nothing to `pip install`.
* **Elasticsearch and Kibana 8.x or 9.x.** Developed and tested on 9.4.2. Elasticsearch Serverless is not
  supported (this project uses ordinary ingest pipelines and index templates).
* Somewhere for the agent to run continuously and reach both Elasticsearch and your UniFi console - see
  [agent-deployment.md](agent-deployment.md).

## 1. The API key

Run `./install --print-api-key-request`, paste the result into Kibana **Dev Tools -> Console**, run it, and
save the `encoded` value from the answer (shown once) to a file (`chmod 600`).

The key can do this and nothing more:

| Permission | Why |
|---|---|
| cluster `manage_index_templates`, `manage_ingest_pipelines` | create the ingest pipeline and index template (Elasticsearch cannot scope these to a name pattern) |
| cluster `manage_enrich` | create and run the MAC-vendor lookup's enrich policy (built from the real IEEE OUI registry). Skip this and the download with `--skip-vendor-lookup` if you would rather not grant it |
| cluster `monitor` | read the version and node count. Optional - the installer only loses a little detail without it |
| indices `ubiquiti-*`: `manage`, `create_index`, `read`, `write`, `view_index_metadata` | create, fill and read this project's index, and the vendor lookup's own `ubiquiti-oui-vendors` index (same prefix, no separate grant needed) |
| Kibana `space_all` on one space | create the data view, charts, saved search and dashboard |

**This is a different index pattern from Kismet_Cartographer's own key** (`kismet-cartographer-*`). A key
made for that project will not work here until its role is widened to also cover `ubiquiti-*` -
see [security.md](security.md#reusing-a-key-from-another-project) for the one-line change and why the
installer cannot make it for you.

**This key is only for `./install`.** The syslog agent needs a separate, much narrower one -
`./install --print-agent-key-request` - see [agent-deployment.md](agent-deployment.md).

**Whose key?** Any account's, the same as every tool in this family: the installer sends the key and lets
Elasticsearch identify the account (it prints `authenticated as '<user>'`). The account that creates the key
must itself hold these rights, since a key can never exceed its creator's.

## 2. The CA certificate

Skip this if your certificate is from a public CA, or your stack does not use HTTPS at all (see `--host`
below).

| Setup | Where the CA is |
|---|---|
| Standard install (deb/rpm/zip) | `/etc/elasticsearch/certs/http_ca.crt` on the Elasticsearch host |
| Docker | `docker cp <container>:/usr/share/elasticsearch/config/certs/http_ca.crt .` |
| Kubernetes, ECK | `kubectl -n <ns> get secret <es-name>-es-http-certs-public -o go-template='{{index .Data "ca.crt" \| base64decode}}' > es-ca.crt` (and the `-kb-http-certs-public` secret for Kibana, if it uses a different CA) |

## 3. Run the installer

```bash
./install --es-url URL --kibana-url URL --api-key-file FILE [--ca FILE] [--kibana-ca FILE]
```

| Option | What it does |
|---|---|
| `--es-url URL`, `--kibana-url URL` | the two addresses |
| `--host ADDRESS` | build both URLs from one address (`http://` unless you add `--https` or `--ca`); ports via `--es-port` / `--kibana-port` |
| `--api-key-file FILE` / `--api-key KEY` / `ELASTIC_API_KEY` | the key |
| `--ca FILE`, `--kibana-ca FILE` | CA certificate(s); Kibana's defaults to `--ca` |
| `--tls-server-name NAME`, `--kibana-tls-server-name NAME` | certificate name to verify when the URL uses a different name (`kubectl port-forward`) |
| `--space ID` | Kibana space (default: the default space) |
| `--prefix TEXT` | prefix of every name (default `ubiquiti-`) |
| `--replicas N` | replicas for the index (default: 1 with 2+ data nodes, else 0) |
| `--check` | test the connection and permissions only |
| `--dry-run` | show what would be created, change nothing |
| `--skip-kibana` | Elasticsearch only: pipeline, template, index |
| `--skip-vendor-lookup` | do not download the IEEE OUI registry or set up `unifi.client.vendor` - for offline/air-gapped installs, or to avoid the extra `manage_enrich` privilege |
| `--save-config [FILE]` | save the connection settings (never the key) to `FILE` (default `.env`, mode 600) so later runs need no flags |
| `--remove` (`--yes`) | delete everything this installer created |
| `--test` | run the offline unit tests first |
| `--insecure` | skip certificate verification (throw-away tests only) |
| `--username NAME` | optional: stop unless the key belongs to this account |

**What it does, in order** (safe to run again; that is also how you upgrade):

1. **Checks** - Python version, connection and certificate, that the key holds the permissions above, that
   Kibana is reachable and accepts the key. Nothing is changed until this passes.
2. **Vendor lookup** - downloads the real IEEE OUI registry and loads it into `<prefix>oui-vendors`, then
   creates (or re-runs) the enrich policy that fills in `unifi.client.vendor` at ingest time. A failed
   download only warns and continues without it - it never fails the whole install; run again later, or add
   `--skip-vendor-lookup` to opt out on purpose.
3. **Elasticsearch** - creates or updates the ingest pipeline, then self-tests it (a document is run through it
   with `_simulate` and checked for the fields it should have added), then creates or updates the index
   template and the index.
4. **Kibana** - creates or updates the data view, the charts, the saved search and the dashboard. Objects that
   already match what would be created are left alone.

Exit codes: `0` success, `1` a step failed, `2` a usage/configuration problem, `3` could not connect,
authenticate, or lacking permission.

## 4. Deploy the agent

Nothing shows up in the dashboard until the agent is running and UniFi is sending to it - see
[agent-deployment.md](agent-deployment.md).
