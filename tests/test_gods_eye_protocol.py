"""Protocole world_* — validation, bornes, parité JS/Python."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from src.gods_eye import protocol

REPO_ROOT = Path(__file__).resolve().parents[1]
REGIONS_JS = REPO_ROOT / "web" / "gods_eye_view" / "src" / "regions.js"
BRIDGE_JS = REPO_ROOT / "web" / "gods_eye_view" / "src" / "bridge.js"


class ActionValidationTests(unittest.TestCase):
    def test_action_inconnue_rejetee(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.build_request("world_delete_everything")

    def test_action_vide_rejetee(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.build_request("")

    def test_les_six_actions_sont_acceptees(self):
        for action in protocol.ACTIONS:
            args = {}
            if action == protocol.ACTION_SET_CAMERA:
                args = {"latitude": 48.85, "longitude": 2.35}
            elif action == protocol.ACTION_FOCUS_REGION:
                args = {"region": "paris"}
            elif action in (protocol.ACTION_ENABLE_LAYER, protocol.ACTION_DISABLE_LAYER):
                args = {"layer_id": "flights"}
            request = protocol.build_request(action, args)
            self.assertEqual(request["action"], action)
            self.assertRegex(request["id"], r"^[0-9a-f]{16}$")
            self.assertEqual(request["protocol"], protocol.PROTOCOL_VERSION)

    def test_enable_layer_whitelist(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.build_request(protocol.ACTION_ENABLE_LAYER, {"layer_id": "cctv"})
        request = protocol.build_request(protocol.ACTION_ENABLE_LAYER, {"layer_id": "FLIGHTS"})
        self.assertEqual(request["args"]["layer_id"], "flights")

    def test_disable_layer_whitelist(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.build_request(protocol.ACTION_DISABLE_LAYER, {"layer_id": ""})

    def test_set_view_seule_vue_globe(self):
        request = protocol.build_request(protocol.ACTION_SET_VIEW, {})
        self.assertEqual(request["args"], {"view": "globe"})
        with self.assertRaises(protocol.ProtocolError):
            protocol.build_request(protocol.ACTION_SET_VIEW, {"view": "terrain"})


class CameraValidationTests(unittest.TestCase):
    def test_bornes_latitude_longitude(self):
        for lat in (-90.1, 90.1, 120):
            with self.assertRaises(protocol.ProtocolError):
                protocol.build_request(protocol.ACTION_SET_CAMERA, {"latitude": lat, "longitude": 2.0})
        for lon in (-180.1, 180.1):
            with self.assertRaises(protocol.ProtocolError):
                protocol.build_request(protocol.ACTION_SET_CAMERA, {"latitude": 48, "longitude": lon})

    def test_latitude_non_numerique_rejetee(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.build_request(protocol.ACTION_SET_CAMERA, {"latitude": "abc", "longitude": 2})

    def test_altitude_bornee(self):
        request = protocol.build_request(
            protocol.ACTION_SET_CAMERA,
            {"latitude": 48, "longitude": 2, "altitude_m": 10**12},
        )
        self.assertEqual(request["args"]["altitude_m"], protocol.ALTITUDE_MAX_M)
        request = protocol.build_request(
            protocol.ACTION_SET_CAMERA,
            {"latitude": 48, "longitude": 2, "altitude_m": -5},
        )
        self.assertEqual(request["args"]["altitude_m"], protocol.ALTITUDE_MIN_M)

    def test_defauts_appliques(self):
        request = protocol.build_request(protocol.ACTION_SET_CAMERA, {"latitude": 48, "longitude": 2})
        args = request["args"]
        self.assertEqual(args["altitude_m"], protocol.DEFAULT_CAMERA["altitude_m"])
        self.assertEqual(args["pitch_deg"], protocol.DEFAULT_CAMERA["pitch_deg"])

    def test_duration_bornee(self):
        request = protocol.build_request(
            protocol.ACTION_SET_CAMERA, {"latitude": 48, "longitude": 2, "duration_s": 99}
        )
        self.assertEqual(request["args"]["duration_s"], protocol.DURATION_MAX_S)

    def test_nan_remplace_par_defaut(self):
        request = protocol.build_request(
            protocol.ACTION_SET_CAMERA, {"latitude": 48, "longitude": 2, "heading_deg": float("nan")}
        )
        self.assertEqual(request["args"]["heading_deg"], protocol.DEFAULT_CAMERA["heading_deg"])


class RegionTests(unittest.TestCase):
    def test_region_inconnue_message_exploitable(self):
        with self.assertRaises(protocol.ProtocolError) as ctx:
            protocol.build_request(protocol.ACTION_FOCUS_REGION, {"region": "atlantis"})
        self.assertIn("paris", str(ctx.exception))

    def test_region_insensible_casse(self):
        request = protocol.build_request(protocol.ACTION_FOCUS_REGION, {"region": "PARIS"})
        self.assertEqual(request["args"]["region"], "paris")

    def test_altitude_personnalisee_bornee(self):
        request = protocol.build_request(
            protocol.ACTION_FOCUS_REGION, {"region": "paris", "altitude_m": 10**9}
        )
        self.assertEqual(request["args"]["altitude_m"], protocol.ALTITUDE_MAX_M)


class ResponseParsingTests(unittest.TestCase):
    def test_reponse_valide_chaine(self):
        response = protocol.parse_response('{"id":"abc12345","ok":true,"action":"world_get_state"}')
        self.assertTrue(response["ok"])

    def test_reponse_valide_objet(self):
        response = protocol.parse_response({"id": "x", "ok": False, "error": "boom"})
        self.assertFalse(response["ok"])

    def test_json_invalide(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_response("{pas json")

    def test_ok_manquant(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_response('{"id":"abc12345"}')

    def test_ok_non_booleen(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_response('{"id":"abc12345","ok":"oui"}')

    def test_non_objet(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_response('[1,2,3]')

    def test_roundtrip_dumps_parse(self):
        request = protocol.build_request(protocol.ACTION_GET_STATE, {})
        payload = protocol.dumps_request(request)
        # JSON compact, ensure_ascii
        self.assertNotIn(" ", payload)
        parsed = json.loads(payload)
        self.assertEqual(parsed["action"], "world_get_state")


class JsParityTests(unittest.TestCase):
    """La table des régions et les bornes caméra doivent correspondre au module web."""

    @staticmethod
    def _js_regions() -> dict[str, tuple[float, float, float]]:
        text = REGIONS_JS.read_text(encoding="utf-8")
        regions: dict[str, tuple[float, float, float]] = {}
        pattern = re.compile(
            r"^\s{2}([a-z_]+):\s*\{\s*label:\s*'[^']+',\s*latitude:\s*(-?[\d.]+),\s*longitude:\s*(-?[\d.]+),\s*altitude_m:\s*_?([\d_]+)\.?0*\s*\}",
            re.MULTILINE,
        )
        for match in pattern.finditer(text):
            name, lat, lon, alt = match.groups()
            regions[name] = (float(lat), float(lon), float(alt.replace("_", "")))
        return regions

    def test_memes_regions_des_deux_cotes(self):
        js_regions = self._js_regions()
        self.assertTrue(js_regions, "regions.js : table introuvable — regex à mettre à jour")
        self.assertEqual(
            sorted(js_regions),
            sorted(protocol.FOCUS_REGIONS),
            "Les régions divergent entre protocol.py et regions.js",
        )

    def test_memes_coordonnees_des_deux_cotes(self):
        js_regions = self._js_regions()
        for name, (lat, lon, alt) in js_regions.items():
            py_region = protocol.FOCUS_REGIONS[name]
            self.assertAlmostEqual(py_region["latitude"], lat, places=4, msg=name)
            self.assertAlmostEqual(py_region["longitude"], lon, places=4, msg=name)
            self.assertAlmostEqual(py_region["altitude_m"], alt, places=0, msg=name)

    def test_bornes_camera_identiques(self):
        text = BRIDGE_JS.read_text(encoding="utf-8")
        for needle, value in (
            ("altitude_m: [1_000, 40_000_000]", None),
            ("duration_s: [0, 10]", None),
        ):
            self.assertIn(needle, text, "bridge.js ne porte plus les bornes attendues")
        self.assertIn("40_000_000", text)


if __name__ == "__main__":
    unittest.main()
