# Security

## The API key

`./install --print-api-key-request` prints the request that creates a key with exactly these rights and no
others (see [installation.md](installation.md) for the full table):

* indices `ubiquiti-*`: `manage`, `create_index`, `read`, `write`, `view_index_metadata`
* cluster `manage_index_templates`, `manage_ingest_pipelines`, `manage_enrich` (all cluster-wide - Elasticsearch
  has no way to scope these to a name pattern) and `monitor` (optional)
* Kibana `space_all` on one space

`manage_enrich` is only for the MAC-vendor lookup (see below); pass `--skip-vendor-lookup` if you would rather
not grant it, and drop it from the role.

It cannot read or change other indices, users, roles, or other Kibana spaces. Because the two template/pipeline
rights are cluster-wide, `install` adds its own guard rail: every create, update or delete first checks the
target name starts with `--prefix` (`ubiquiti-` by default) and refuses otherwise, and it refuses
to write into an index or overwrite a data view that was not created by this project. That guard rail is
enforced in this project's own code, not by Elasticsearch - treat the key itself as able to manage templates and
pipelines cluster-wide.

**Give the key an expiry** (the request sets 90 days) and invalidate it when done:

```
DELETE /_security/api_key
{ "ids": ["<the id shown when you created it>"] }
```

### The agent's own credential

The agent (`unifi_cartographer/agent.py`, deployed via `k8s/ubiquiti-syslog-agent.yaml`) only ever *creates*
new documents (it bulk-indexes with the `create` action, never `index` or `update`, since every event is
new and none is ever meant to be overwritten) - it needs nothing from the list above except `create_doc`
and `read` on `ubiquiti-events*`, no cluster privileges and no Kibana access at all. Run
`./install --print-agent-key-request` for the Dev Tools request that creates exactly that, a separate key
from the one `./install` itself uses - see [agent-deployment.md](agent-deployment.md).

### Reusing a key from another project

A key made for Kismet_Cartographer's `./install` is scoped to `kismet-cartographer-*` and will not work here as
is - Elasticsearch checks the index pattern on every request, not just that a key exists. Two ways to reuse it
rather than keeping two keys:

1. **Widen its role** to also list `ubiquiti-*` in the `indices` block (Kibana -> Stack Management
   -> Roles, or `PUT /_security/role/<name>` with both patterns in the `indices` array). This is a change to
   your cluster's security configuration - review it before applying, the same as any role change.
2. **Create a second key** from the same account with `./install --print-api-key-request`, and keep both. Simpler,
   and keeps the two projects' access separate, at the cost of one more key to track.

Neither can be done by `./install` itself: creating or editing a *role* needs `manage_security`, which the
scoped key this project asks for deliberately does not have (a key that could edit roles could grant itself
more access later, defeating the point of scoping it in the first place).

## What leaves your network

* **UniFi -> the agent:** whatever categories you enabled, over UDP, to your network. UniFi is not told anything
  about this project or asked for credentials; it just sends to an address you configured on its own settings
  page.
* **The agent -> Elasticsearch:** the parsed documents, over HTTPS with the API key, to the address you
  configured. No data leaves to anywhere else.
* **`./install` -> Elasticsearch/Kibana:** the pipeline, index template, and dashboard definitions. No UniFi
  data passes through the installer.
* **`./install` -> `standards-oui.ieee.org` (unless `--skip-vendor-lookup`):** a plain HTTPS GET of IEEE's public
  OUI registry (`oui.csv`, ~4MB, no query parameters, no data about your network or devices sent). This is the
  one outbound request to a third party anywhere in this project, and it is one-directional - nothing about
  your install, your key, or your UniFi data is included in or derivable from that request. Use
  `--skip-vendor-lookup` for an air-gapped install, or if you would rather not make this request at all;
  `unifi.client.vendor` just stays empty.

## Handling secrets

* Nothing secret is stored in this repository. Give the key with `--api-key-file`, the `ELASTIC_API_KEY`
  environment variable, or an env file (`.env.example`; keep it mode 600 and out of version control).
* **`--api-key` on the command line is visible to other users on the machine (`ps`) and stays in your shell
  history.** Prefer a file.
* `./install --save-config` writes the connection settings to a mode-600 `.env`, **without the key** - only the
  path of the file that holds it - and refuses to overwrite a file it did not write.
* The Kubernetes manifest never contains a key: the agent reads it from a Secret you create yourself (see
  [agent-deployment.md](agent-deployment.md)), which is why the commands for that are documentation, not files
  in this repository.
* `.gitignore` excludes `.env`, `*.env` (except `.env.example`), `*.key`, `*.pem`, `*.crt`. Check `git status`
  before committing anything you did not write.

## TLS

Connections are verified against the CA you give (or the system's trusted CAs). Verification is off only with
`--insecure` (or `ELASTIC_VERIFY_CERTS=false`), and the installer warns loudly. Plain `http://` (stacks without
TLS) sends the API key and every document in clear text; use it only on a network you trust.

## Sensitivity of the data

Security-detection and admin-activity events describe your own network's traffic and administration: source
and destination addresses, which admin accessed the console and how, which clients associated to which access
point. Treat the Kibana space and the API key with the same care you would give firewall logs, because that is
substantially what they are.
