"""Unit tests (standard library only): run with `python3 -m unittest discover -s tests -v`.

No Elasticsearch, no Kibana, no UniFi controller and no internet needed. The CEF fixtures are a mix: three
real example messages published by UniFi users/vendors documenting UniFi's remote-logging feature (see
docs/data-model.md for the sources), plus real captures from a live UniFi console and access point/switch
(the `RealCaptureTests` class) - both are genuine UniFi output, never a message invented from the spec alone.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unifi_cartographer import cef, kibana_objects as ko, mappings  # noqa: E402
from unifi_cartographer.agent import Batcher, Stats  # noqa: E402
from unifi_cartographer.config import (ConfigError, add_connection_args, load_config, normalize_api_key,  # noqa: E402
                                       write_env_file)
from unifi_cartographer.http import Client, kibana_space_path  # noqa: E402
from unifi_cartographer.names import DEFAULT_PREFIX, Names  # noqa: E402

WIFI_CLIENT = ("CEF:0|Ubiquiti|UniFi Network|9.3.33|400|WiFi Client Connected|2| "
              "UNIFIcategory=Monitoring UNIFIsubCategory=WiFi UNIFIhost=Office UDM Pro "
              "UNIFIclientMac=aa:bb:cc:dd:ee:ff UNIFIclientName=iPhone UNIFIssid=Corporate-WiFi UNIFIapName=Lobby-AP")
THREAT_BLOCKED = ("CEF:0|Ubiquiti|UniFi Network|9.4.19|201|Threat Detected and Blocked|7|proto=TCP src=10.0.0.100 "
                  "spt=52331 dst=192.168.0.233 dpt=443 UNIFIcategory=Security UNIFIsubCategory=Intrusion Prevention "
                  "UNIFIhost=Express 7 UNIFIdeviceMac=84:78:48:80:0d:86 UNIFIdeviceName=Express 7 "
                  "UNIFIdeviceModel=UX7 UNIFIdeviceIp=192.168.0.1 UNIFIdeviceVersion=4.3.9 UNIFIrisk=medium")
ADMIN_ACCESS = ("CEF:0|Ubiquiti|UniFi Network|9.3.33|544|Admin Accessed UniFi Network|1|"
               "UNIFIcategory=System UNIFIsubCategory=Admin UNIFIhost=Office UDM Pro UNIFIaccessMethod=web")

# Real captures from a live UniFi console/AP/switch (not from public documentation - see docs/testing.md).
# The two client-connect events below are the ones that exposed a real gap: "Connected" and "Disconnected"
# use different UNIFI* extension key names for the same "which switch/port" concept.
WIRED_CLIENT_CONNECTED = ("CEF:0|Ubiquiti|UniFi Network|10.6.106|403|Wired Client Connected|1|"
                          "UNIFIcategory=Client Devices UNIFIsite=Default UNIFIhost=UCK G2 Plus "
                          "UNIFIconnectedToDeviceName=USW Pro Max 16 PoE UNIFIconnectedToDevicePort=4 "
                          "UNIFIconnectedToDeviceIp=192.168.50.4 UNIFIconnectedToDeviceMac=74:fa:29:1d:a5:f2 "
                          "UNIFIconnectedToDeviceModel=USW-Pro-Max-16-PoE UNIFIconnectedToDeviceVersion=7.5.15 "
                          "UNIFIclientAlias=IDRAC-R630 d8:14 UNIFIclientHostname=IDRAC-R630 UNIFIclientIp=192.168.55.36 "
                          "UNIFIclientMac=58:8a:5a:f3:d8:14 UNIFIlinkSpeed=GbE UNIFInetworkName=TALOS "
                          "UNIFInetworkSubnet=192.168.1.0/24 UNIFInetworkVlan=55 "
                          "UNIFIutcTime=2026-09-24T22:14:35.876Z "
                          "msg=IDRAC-R630 d8:14 connected to TALOS on USW Pro Max 16 PoE Port 4. Link Speed: GbE.")
WIRED_CLIENT_DISCONNECTED = ("CEF:0|Ubiquiti|UniFi Network|10.6.106|404|Wired Client Disconnected|2|"
                             "UNIFIcategory=Client Devices UNIFIsite=Default UNIFIhost=UCK G2 Plus "
                             "UNIFIlastConnectedToDeviceName=USW Pro Max 16 PoE UNIFIlastConnectedToDevicePort=4 "
                             "UNIFIlastConnectedToDeviceIp=192.168.50.4 UNIFIlastConnectedToDeviceMac=74:fa:29:1d:a5:f2 "
                             "UNIFIlastConnectedToDeviceModel=USW-Pro-Max-16-PoE UNIFIlastConnectedToDeviceVersion=7.5.15 "
                             "UNIFIclientAlias=IDRAC-R630 d8:14 UNIFIclientHostname=IDRAC-R630 UNIFIclientIp=192.168.55.36 "
                             "UNIFIclientMac=58:8a:5a:f3:d8:14 UNIFIduration=20m 2s UNIFIusageDown=43.99 KB "
                             "UNIFIusageUp=398.00 B UNIFInetworkName=TALOS UNIFInetworkSubnet=192.168.1.0/24 "
                             "UNIFInetworkVlan=55 UNIFIutcTime=2026-09-24T21:40:04.593Z "
                             "msg=IDRAC-R630 d8:14 disconnected from TALOS on USW Pro Max 16 PoE Port 4.")
# A real access point's own debug syslog - not CEF at all, most of a typical install's real traffic volume.
NON_CEF_HOSTAPD = ("<13>Sep 24 19:54:14 U7-Pro 8cede1889798,U7-Pro-8.7.11+19419: hostapd[4429]: wifi1ap1: "
                   "DPP-RX src=ca:b1:48:0e:a6:43 freq=5745 type=13")
# A real switch's own syslog, WITH the trailing newline UniFi hardware actually sends - this exact trailing
# newline broke an early version of the envelope-tag regex (Java's whole-string .matches() semantics, when
# this logic was first written for a different runtime); Python's re.match does not have that failure mode,
# but the fixture is kept with the newline anyway as a regression guard.
NON_CEF_SWITCH = ("<27>Sep 24 16:22:16 USW-Pro-Max-16-PoE 74fa291da5f2,USW-Pro-Max-16-PoE-7.5.15+17146: "
                  "udhcpc[977]: lease of 192.168.50.4 obtained, lease time 7200\n")


class CefParsingTests(unittest.TestCase):
    def test_wifi_client_connected(self):
        parsed = cef.parse_cef(WIFI_CLIENT)
        self.assertEqual((parsed["device_vendor"], parsed["device_product"], parsed["device_version"]),
                         ("Ubiquiti", "UniFi Network", "9.3.33"))
        self.assertEqual((parsed["signature_id"], parsed["name"], parsed["severity"]), ("400", "WiFi Client Connected", "2"))
        self.assertEqual(parsed["extension"]["UNIFIclientMac"], "aa:bb:cc:dd:ee:ff")
        self.assertEqual(parsed["extension"]["UNIFIapName"], "Lobby-AP")

    def test_threat_detected_maps_standard_and_unifi_fields(self):
        doc = cef.build_document(THREAT_BLOCKED.encode(), "192.168.55.10", 514, "2026-01-01T00:00:00.000Z")
        self.assertEqual(doc["source"]["ip"], "10.0.0.100")
        self.assertEqual(doc["source"]["port"], 52331)
        self.assertEqual(doc["destination"]["ip"], "192.168.0.233")
        self.assertEqual(doc["destination"]["port"], 443)
        self.assertEqual(doc["network"]["transport"], "TCP")
        self.assertEqual(doc["unifi"]["category"], "Security")
        self.assertEqual(doc["unifi"]["subcategory"], "Intrusion Prevention")
        self.assertEqual(doc["unifi"]["device"], {"mac": "84:78:48:80:0d:86", "name": "Express 7", "model": "UX7",
                                                  "ip": "192.168.0.1", "version": "4.3.9"})
        self.assertEqual(doc["unifi"]["risk"], "medium")
        self.assertEqual(doc["event"]["severity"], 7)
        self.assertEqual(doc["event"]["severity_label"], "high")
        self.assertNotIn("parse_error", doc["unifi"])
        self.assertNotIn("extensions", doc["unifi"]["cef"])  # every field in this message is mapped, nothing left over

    def test_admin_access(self):
        doc = cef.build_document(ADMIN_ACCESS.encode(), "192.168.55.10", 514, "2026-01-01T00:00:00.000Z")
        self.assertEqual(doc["unifi"]["category"], "System")
        self.assertEqual(doc["unifi"]["access_method"], "web")
        self.assertEqual(doc["event"]["severity"], 1)
        self.assertEqual(doc["event"]["severity_label"], "low")

    def test_wifi_client_leftover_extension_is_kept_not_dropped(self):
        doc = cef.build_document(WIFI_CLIENT.encode(), "192.168.55.10", 514, "2026-01-01T00:00:00.000Z")
        self.assertEqual(doc["unifi"]["client"], {"mac": "aa:bb:cc:dd:ee:ff", "name": "iPhone", "oui": "AABBCC"})
        self.assertEqual(doc["unifi"]["ssid"], "Corporate-WiFi")
        self.assertEqual(doc["unifi"]["ap_name"], "Lobby-AP")

    def test_severity_labels(self):
        self.assertEqual(cef.severity_label("0"), "low")
        self.assertEqual(cef.severity_label("3"), "low")
        self.assertEqual(cef.severity_label("4"), "medium")
        self.assertEqual(cef.severity_label("6"), "medium")
        self.assertEqual(cef.severity_label("7"), "high")
        self.assertEqual(cef.severity_label("9"), "very-high")
        self.assertEqual(cef.severity_label("10"), "very-high")
        self.assertIsNone(cef.severity_label("not-a-number"))
        self.assertIsNone(cef.severity_label(None))

    def test_escaped_pipe_and_equals_in_header_and_extension(self):
        msg = r"CEF:0|Ubiquiti|UniFi Network|9.3.33|400|Rule \| matched|3|UNIFIhost=Office UDM Pro UNIFIrisk=a\=b"
        parsed = cef.parse_cef(msg)
        self.assertEqual(parsed["name"], "Rule | matched")
        self.assertEqual(parsed["extension"]["UNIFIrisk"], "a=b")

    def test_truncated_header_is_salvaged_not_rejected(self):
        # Fewer than 7 pipes: a documented real-world UniFi OS bug. Whatever is present is used, nothing raises.
        msg = "CEF:0|Ubiquiti|UniFi Network|9.3.33|400"
        parsed = cef.parse_cef(msg)
        self.assertEqual(parsed["signature_id"], "400")
        self.assertIsNone(parsed["name"])
        self.assertEqual(parsed["extension"], {})

    def test_no_cef_marker_is_not_an_error(self):
        self.assertIsNone(cef.parse_cef("this is not a CEF message at all"))
        doc = cef.build_document(b"link down on eth0", "192.168.55.11", 514, "2026-01-01T00:00:00.000Z")
        self.assertEqual(doc["unifi"]["parse_error"], "not_cef")
        self.assertEqual(doc["message"], "link down on eth0")

    def test_empty_datagram(self):
        doc = cef.build_document(b"   ", "192.168.55.11", 514, "2026-01-01T00:00:00.000Z")
        self.assertEqual(doc["unifi"]["parse_error"], "empty_message")

    def test_invalid_utf8_does_not_raise(self):
        doc = cef.build_document(b"CEF:0|Ubiquiti|UniFi Network|1|1|Bad byte \xff here|1|UNIFIcategory=System",
                                 "192.168.55.11", 514, "2026-01-01T00:00:00.000Z")
        self.assertEqual(doc["unifi"]["category"], "System")


class RealCaptureTests(unittest.TestCase):
    """Real captures from a live UniFi console (see the fixtures' own comments), not from public documentation."""

    def test_connected_and_disconnected_use_different_keys_for_the_same_field(self):
        # The real bug this test guards: "Connected" uses UNIFIconnectedToDevice*, "Disconnected" uses
        # UNIFIlastConnectedToDevice* - genuinely different extension key names, same category, same console.
        connected = cef.build_document(WIRED_CLIENT_CONNECTED.encode(), "192.168.50.7", 514, "2026-01-01T00:00:00.000Z")
        disconnected = cef.build_document(WIRED_CLIENT_DISCONNECTED.encode(), "192.168.50.7", 514, "2026-01-01T00:00:00.000Z")
        expected = {"name": "USW Pro Max 16 PoE", "ip": "192.168.50.4", "mac": "74:fa:29:1d:a5:f2",
                   "model": "USW-Pro-Max-16-PoE", "version": "7.5.15", "port": 4}
        self.assertEqual({k: v for k, v in connected["unifi"]["last_connected_device"].items() if k != "link_speed"}, expected)
        self.assertEqual(disconnected["unifi"]["last_connected_device"], expected)
        self.assertEqual(connected["unifi"]["last_connected_device"]["link_speed"], "GbE")

    def test_client_alias_hostname_ip_and_oui(self):
        doc = cef.build_document(WIRED_CLIENT_CONNECTED.encode(), "192.168.50.7", 514, "2026-01-01T00:00:00.000Z")
        client = doc["unifi"]["client"]
        self.assertEqual(client["alias"], "IDRAC-R630 d8:14")
        self.assertEqual(client["hostname"], "IDRAC-R630")
        self.assertEqual(client["ip"], "192.168.55.36")
        self.assertEqual(client["mac"], "58:8a:5a:f3:d8:14")
        self.assertEqual(client["oui"], "588A5A")  # real: Dell Inc. - the vendor lookup itself is Elasticsearch's job

    def test_network_fields_are_top_level_not_under_unifi(self):
        doc = cef.build_document(WIRED_CLIENT_CONNECTED.encode(), "192.168.50.7", 514, "2026-01-01T00:00:00.000Z")
        self.assertEqual(doc["network"], {"name": "TALOS", "subnet": "192.168.1.0/24", "vlan": "55"})
        self.assertNotIn("network", doc["unifi"])

    def test_usage_strings_are_parsed_to_bytes_and_kept_verbatim(self):
        doc = cef.build_document(WIRED_CLIENT_DISCONNECTED.encode(), "192.168.50.7", 514, "2026-01-01T00:00:00.000Z")
        session = doc["unifi"]["session"]
        self.assertEqual(session["usage_down"], "43.99 KB")
        self.assertEqual(session["usage_down_bytes"], 43.99 * 1024)
        self.assertEqual(session["usage_up"], "398.00 B")
        self.assertEqual(session["usage_up_bytes"], 398.0)
        self.assertEqual(session["duration"], "20m 2s")

    def test_non_cef_hostapd_line_is_envelope_parsed(self):
        doc = cef.build_document(NON_CEF_HOSTAPD.encode(), "192.168.50.7", 514, "2026-01-01T00:00:00.000Z")
        self.assertEqual(doc["unifi"]["parse_error"], "not_cef_envelope_parsed")
        self.assertEqual(doc["unifi"]["device"], {"mac": "8cede1889798", "model": "U7-Pro", "version": "8.7.11+19419"})
        self.assertEqual(doc["log"]["syslog"]["appname"], "hostapd")
        self.assertEqual(doc["log"]["syslog"]["procid"], "4429")

    def test_non_cef_switch_line_with_trailing_newline_is_still_envelope_parsed(self):
        # Regression: a trailing "\n" on the raw datagram must not stop the device-tag regex from matching.
        doc = cef.build_document(NON_CEF_SWITCH.encode(), "192.168.50.4", 514, "2026-01-01T00:00:00.000Z")
        self.assertEqual(doc["unifi"]["parse_error"], "not_cef_envelope_parsed")
        self.assertEqual(doc["unifi"]["device"],
                         {"mac": "74fa291da5f2", "model": "USW-Pro-Max-16-PoE", "version": "7.5.15+17146"})
        self.assertEqual(doc["log"]["syslog"]["appname"], "udhcpc")
        self.assertEqual(doc["log"]["syslog"]["procid"], "977")

    def test_empty_tagged_daemon_line_is_left_plain_not_cef(self):
        # A handful of real lines have an EMPTY tag before the real one (e.g. "...17146: : utermd[984]:") -
        # the tag regex requires at least one appname character, so this genuinely does not match (unlike a
        # "mcad: mcad[981]:" double-tag, where the first "mcad" IS a valid one-word appname and matches fine -
        # this fixture is deliberately the empty-tag case, not that one).
        msg = ("<14>Sep 24 15:49:31 USW-Pro-Max-16-PoE 74fa291da5f2,USW-Pro-Max-16-PoE-7.5.15+17146: "
              ": utermd[984]: utermd.house_keeper(): Session cleanup")
        doc = cef.build_document(msg.encode(), "192.168.50.4", 514, "2026-01-01T00:00:00.000Z")
        self.assertEqual(doc["unifi"]["parse_error"], "not_cef")
        self.assertNotIn("device", doc["unifi"])


class SyslogEnvelopeTests(unittest.TestCase):
    def test_rfc3164_style_header_is_stripped(self):
        text = "<134>Jan  5 10:00:00 UDM-Pro " + WIFI_CLIENT
        env = cef.parse_syslog_envelope(text)
        self.assertEqual((env["facility"], env["severity"], env["rfc"], env["hostname"]), (16, 6, "3164", "UDM-Pro"))
        self.assertTrue(env["message"].startswith("CEF:0|Ubiquiti"))

    def test_rfc5424_style_header_is_stripped(self):
        text = f"<134>1 2026-01-05T10:00:00Z UDM-Pro unifi-core 123 - - {WIFI_CLIENT}"
        env = cef.parse_syslog_envelope(text)
        self.assertEqual((env["rfc"], env["hostname"], env["appname"], env["procid"]), ("5424", "UDM-Pro", "unifi-core", "123"))
        self.assertTrue(env["message"].startswith("CEF:0|Ubiquiti"))

    def test_rfc5424_with_structured_data(self):
        text = f'<134>1 2026-01-05T10:00:00Z UDM-Pro unifi-core - - [ex@1 iut="1"] {WIFI_CLIENT}'
        env = cef.parse_syslog_envelope(text)
        self.assertEqual(env["rfc"], "5424")
        self.assertTrue(env["message"].startswith("CEF:0|Ubiquiti"))

    def test_no_pri_header_at_all(self):
        env = cef.parse_syslog_envelope(WIFI_CLIENT)
        self.assertEqual(env["rfc"], "none")
        self.assertIsNone(env["facility"])
        self.assertEqual(env["message"], WIFI_CLIENT)

    def test_full_datagram_with_syslog_header_end_to_end(self):
        raw = f"<134>Jan  5 10:00:00 UDM-Pro {WIFI_CLIENT}".encode()
        doc = cef.build_document(raw, "192.168.55.10", 514, "2026-01-01T00:00:00.000Z")
        self.assertEqual(doc["observer"]["hostname"], "UDM-Pro")
        self.assertEqual(doc["log"]["syslog"]["facility"]["code"], 16)
        self.assertEqual(doc["unifi"]["ssid"], "Corporate-WiFi")


class AgentBatcherTests(unittest.TestCase):
    class _FakeClient:
        def __init__(self, fail_times=0):
            self.calls, self.fail_times = [], fail_times

        def bulk(self, ndjson):
            self.calls.append(ndjson)
            if self.fail_times > 0:
                self.fail_times -= 1
                from unifi_cartographer.http import ConnectionFailure
                raise ConnectionFailure("simulated")
            n = ndjson.count(b'"create"')
            return {"items": [{"create": {}} for _ in range(n)]}

    def test_action_line_uses_create_and_names_the_target_index(self):
        # Regression test, two things at once:
        # 1. The bulk endpoint used here (POST /_bulk) is not index-scoped, so every action line must carry
        #    its own _index or Elasticsearch rejects the whole batch with "index is missing".
        # 2. It must be the "create" action, not "index": this is a write-once stream, and unlike the
        #    single-document API, bulk's "index" action demands the broader write privilege even with no
        #    explicit id, defeating a credential scoped to create_doc alone.
        client = self._FakeClient()
        b = Batcher(client, "ubiquiti-events", max_docs=1, max_seconds=999, stats=Stats())
        b.add({"a": 1})
        action_line = client.calls[0].decode().splitlines()[0]
        self.assertEqual(json.loads(action_line), {"create": {"_index": "ubiquiti-events"}})

    def test_flushes_at_batch_size(self):
        client = self._FakeClient()
        b = Batcher(client, "idx", max_docs=2, max_seconds=999, stats=Stats())
        b.add({"a": 1})
        self.assertEqual(len(client.calls), 0)
        b.add({"a": 2})
        self.assertEqual(len(client.calls), 1)

    def test_due_after_time_elapses(self):
        b = Batcher(self._FakeClient(), "idx", max_docs=999, max_seconds=0, stats=Stats())
        b.add({"a": 1})
        self.assertTrue(b.due())

    def test_retries_then_succeeds(self):
        client = self._FakeClient(fail_times=1)
        stats = Stats()
        b = Batcher(client, "idx", max_docs=1, max_seconds=999, stats=stats, max_retries=3)
        import time as _t
        orig_sleep = _t.sleep
        _t.sleep = lambda *_: None
        try:
            b.add({"a": 1})
        finally:
            _t.sleep = orig_sleep
        self.assertEqual(stats.indexed, 1)
        self.assertEqual(stats.batches_failed, 0)

    def test_gives_up_and_counts_dropped(self):
        stats = Stats()
        b = Batcher(self._FakeClient(fail_times=99), "idx", max_docs=1, max_seconds=999, stats=stats, max_retries=2)
        import time as _t
        orig_sleep = _t.sleep
        _t.sleep = lambda *_: None
        try:
            b.add({"a": 1})
        finally:
            _t.sleep = orig_sleep
        self.assertEqual((stats.indexed, stats.dropped, stats.batches_failed), (0, 1, 1))


class NamesTests(unittest.TestCase):
    def test_default(self):
        n = Names()
        self.assertEqual((n.write_alias, n.index, n.pipeline), ("ubiquiti-events",
                                                                "ubiquiti-events-v1", "ubiquiti-normalize-v1"))
        self.assertEqual(n.label, "")

    def test_custom_prefix_and_guard(self):
        n = Names("acme")
        self.assertEqual(n.prefix, "acme-")
        self.assertEqual(n.label, " (acme)")
        self.assertEqual((n.oui_vendors_index, n.enrich_policy), ("acme-oui-vendors", "acme-oui-vendor-lookup"))
        with self.assertRaises(ValueError):
            n.require_owned("ubiquiti-events")
        with self.assertRaises(ValueError):
            Names("Bad Prefix!")


class MappingsTests(unittest.TestCase):
    def test_mapping_covers_every_field_a_real_document_can_have(self):
        m = mappings.mappings("ubiquiti-cartographer")
        self.assertEqual(m["dynamic"], "false")
        for raw in (WIFI_CLIENT, THREAT_BLOCKED, ADMIN_ACCESS, WIRED_CLIENT_CONNECTED, WIRED_CLIENT_DISCONNECTED):
            doc = cef.build_document(raw.encode(), "192.168.55.10", 514, "2026-01-01T00:00:00.000Z")

            def check(props, d, path=""):
                for k, v in d.items():
                    self.assertIn(k, props, f"field '{path}{k}' from a real message is not in the mapping")
                    if isinstance(v, dict) and "properties" in props[k]:
                        check(props[k]["properties"], v, f"{path}{k}.")
            check(m["properties"], doc)
        # the non-CEF envelope-tag path (unifi.device.*, log.syslog.appname/procid) needs the same coverage
        for raw in (NON_CEF_HOSTAPD, NON_CEF_SWITCH):
            doc = cef.build_document(raw.encode(), "192.168.55.10", 514, "2026-01-01T00:00:00.000Z")

            def check(props, d, path=""):
                for k, v in d.items():
                    self.assertIn(k, props, f"field '{path}{k}' from a real message is not in the mapping")
                    if isinstance(v, dict) and "properties" in props[k]:
                        check(props[k]["properties"], v, f"{path}{k}.")
            check(m["properties"], doc)

    def test_pipeline_body_is_valid_processors_list(self):
        body = mappings.pipeline_body(Names())
        self.assertTrue(body["processors"])
        self.assertTrue(body["on_failure"])

    def test_pipeline_body_vendor_lookup_can_be_left_out(self):
        # An enrich processor referencing a policy that was never created (download failed, or
        # --skip-vendor-lookup) would tag every document with _pipeline_failure - this must be a clean opt-out.
        names = Names()
        with_lookup = mappings.pipeline_body(names, include_vendor_lookup=True)
        without_lookup = mappings.pipeline_body(names, include_vendor_lookup=False)
        self.assertTrue(any(p.get("enrich", {}).get("policy_name") == names.enrich_policy for p in with_lookup["processors"]))
        self.assertFalse(any("enrich" in p for p in without_lookup["processors"]))

    def test_geo_fields_match_what_the_geoip_processor_actually_writes(self):
        # Regression test: the geoip processor writes a whole object (location, continent_name, ...) to its
        # target field, not a bare geo_point - mapping the target itself as geo_point rejects every document
        # with a public source/destination IP, silently defeating the one enrichment the pipeline does.
        m = mappings.mappings("ubiquiti-cartographer")
        for side in ("source", "destination"):
            geo = m["properties"][side]["properties"]["geo"]
            self.assertNotEqual(geo.get("type"), "geo_point", f"{side}.geo itself must not be geo_point")
            self.assertEqual(geo["properties"]["location"]["type"], "geo_point")


class KibanaObjectsTests(unittest.TestCase):
    def test_objects_reference_the_data_view_and_stay_in_namespace(self):
        n = Names("acme")
        dv = ko.data_view(n)
        for obj in ko.lens_objects(n) + [ko.search(n)]:
            n.require_owned(obj["id"])
            self.assertIn(dv["id"], {r["id"] for r in obj["references"]})
        n.require_owned(ko.dashboard(n, "9.4.2")["id"])

    def test_dashboard_references_every_panel(self):
        n = Names()
        d = ko.dashboard(n, "9.4.2")
        panel_ids = {p["panelRefName"] for p in d["attributes"]["panelsJSON"]}
        ref_names = {r["name"] for r in d["references"]}
        self.assertEqual(panel_ids, ref_names)

    def test_json_attrs_are_stringified(self):
        attrs = ko.stringify_json_attrs(ko.dashboard(Names(), "9.4.2")["attributes"])
        self.assertIsInstance(attrs["panelsJSON"], str)
        self.assertEqual(json.loads(attrs["panelsJSON"])[0]["type"], "lens")


# --------------------------------------------------------------------------------------- config / connection
def parse(*argv):
    ap = argparse.ArgumentParser()
    add_connection_args(ap)
    return ap.parse_args(list(argv))


def cfg_from(*argv, environ=None, env_files=()):
    return load_config(parse(*argv), environ=environ or {}, env_files=list(env_files))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def file(self, name, text):
        p = self.dir / name
        p.write_text(text)
        p.chmod(0o600)
        return p

    def test_api_key_forms(self):
        encoded = base64.b64encode(b"id123:secret456").decode()
        self.assertEqual(normalize_api_key(encoded), encoded)
        self.assertEqual(normalize_api_key("id123:secret456"), encoded)
        with self.assertRaises(ConfigError):
            normalize_api_key("two words")

    def test_host_shortcut(self):
        self.assertEqual(cfg_from("--host", "10.1.2.3").elastic_url, "http://10.1.2.3:9200")
        ca = self.file("ca.crt", "x")
        self.assertEqual(cfg_from("--host", "h", "--ca", str(ca)).elastic_url, "https://h:9200")

    def test_save_config_never_stores_the_key(self):
        keyfile = self.file("k.txt", "supersecret==\n")
        c = cfg_from("--host", "h", "--api-key-file", str(keyfile))
        out = self.dir / "saved.env"
        write_env_file(c, out, str(keyfile))
        text = out.read_text()
        self.assertNotIn("supersecret", text)
        self.assertIn("ELASTIC_API_KEY_FILE=", text)
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)

    def test_kibana_space_path(self):
        self.assertEqual(kibana_space_path(""), "")
        self.assertEqual(kibana_space_path("default"), "")
        self.assertEqual(kibana_space_path("team-a"), "/s/team-a")


class InstallerScriptTests(unittest.TestCase):
    def run_install(self, *argv):
        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items() if not k.startswith(("ELASTIC_", "KIBANA_", "UBIQUITI_"))}
            env["HOME"] = home
            return subprocess.run([sys.executable, str(ROOT / "install"), *argv], capture_output=True, text=True,
                                  cwd=home, env=env, timeout=60)

    def test_print_api_key_request_is_scoped(self):
        r = self.run_install("--print-api-key-request", "--space", "team-a", "--prefix", "acme")
        self.assertEqual(r.returncode, 0)
        body = json.loads(r.stdout.split("POST /_security/api_key\n", 1)[1])
        role = next(iter(body["role_descriptors"].values()))
        self.assertEqual(role["indices"][0]["names"], ["acme-*"])
        self.assertEqual(role["applications"][0]["resources"], ["space:team-a"])
        self.assertIn("manage_ingest_pipelines", role["cluster"])
        self.assertIn("manage_index_templates", role["cluster"])
        self.assertIn("manage_enrich", role["cluster"])

    def test_print_api_key_request_skip_vendor_lookup_drops_manage_enrich(self):
        r = self.run_install("--print-api-key-request", "--skip-vendor-lookup")
        self.assertEqual(r.returncode, 0)
        body = json.loads(r.stdout.split("POST /_security/api_key\n", 1)[1])
        role = next(iter(body["role_descriptors"].values()))
        self.assertNotIn("manage_enrich", role["cluster"])
        self.assertIn("manage_ingest_pipelines", role["cluster"])  # everything else stays

    def test_print_agent_key_request_is_narrower_than_the_installer_key(self):
        r = self.run_install("--print-agent-key-request", "--prefix", "acme")
        self.assertEqual(r.returncode, 0)
        body = json.loads(r.stdout.split("POST /_security/api_key\n", 1)[1])
        role = next(iter(body["role_descriptors"].values()))
        self.assertEqual(role["indices"][0]["names"], ["acme-events*"])
        self.assertEqual(set(role["indices"][0]["privileges"]), {"create_doc", "read"})
        self.assertNotIn("cluster", role)              # no cluster privileges at all, unlike the installer's own key
        self.assertNotIn("applications", role)          # and no Kibana access either

    def test_missing_settings_exit_2(self):
        r = self.run_install("--check")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no Elasticsearch URL", r.stdout)

    def test_bad_prefix_exit_2(self):
        r = self.run_install("--prefix", "Bad Prefix!", "--check")
        self.assertEqual(r.returncode, 2)

    def test_unreachable_stack_exit_3(self):
        r = self.run_install("--check", "--host", "127.0.0.1", "--es-port", "1", "--api-key", "abc")
        self.assertEqual(r.returncode, 3)
        self.assertIn("cannot reach Elasticsearch", r.stdout)


if __name__ == "__main__":
    unittest.main()
