"""Every Elasticsearch/Kibana resource this project creates is named from one prefix.

`require_owned()` is the safety rail for the cluster-wide ingest-pipeline/index-template privileges the
installer needs (Elasticsearch cannot scope those to a name pattern the way it scopes index privileges):
every create, update or delete first checks the name starts with the prefix, and refuses otherwise.
"""
from __future__ import annotations

import re

DEFAULT_PREFIX = "ubiquiti-"
TOOL = "ubiquiti-cartographer"
CONCRETE_VERSION = "v1"
_VALID = re.compile(r"^[a-z0-9][a-z0-9._-]*-$")


class Names:
    def __init__(self, prefix: str = DEFAULT_PREFIX):
        prefix = prefix.strip().lower()
        if prefix and not prefix.endswith("-"):
            prefix += "-"
        if not _VALID.match(prefix):
            raise ValueError(f"--prefix {prefix!r} must be lowercase letters, digits, '.', '_' or '-' "
                             "(it becomes part of index and object names)")
        self.prefix = prefix
        self.write_alias = prefix + "events"
        self.read_alias = prefix + "all"
        self.index = f"{self.write_alias}-{CONCRETE_VERSION}"
        self.index_template = self.write_alias           # template name == alias name, one family
        self.pipeline = prefix + "normalize-v1"
        self.oui_vendors_index = prefix + "oui-vendors"
        self.enrich_policy = prefix + "oui-vendor-lookup"
        self.data_view = prefix + "dataview"
        self.dashboard = prefix + "overview-dashboard"
        self.search = prefix + "recent-events"
        self.lens_prefix = prefix + "lens-"
        # A distinct display suffix when a non-default prefix is used, so several installs in one Kibana
        # (e.g. two sites) get unique object titles - Kibana requires data view titles to be unique.
        self.label = "" if prefix == DEFAULT_PREFIX else f" ({prefix.rstrip('-')})"

    def lens(self, name: str) -> str:
        return f"{self.lens_prefix}{name}"

    def owns(self, name: str) -> bool:
        return name.startswith(self.prefix)

    def require_owned(self, name: str) -> str:
        if not self.owns(name):
            raise ValueError(f"refusing to touch '{name}': it is not under the '{self.prefix}' prefix")
        return name
