"""Übersetzungen: de.json und en.json deckungsgleich, Fehlermeldungen und Meldungstexte vorhanden."""
import json
import os
import re
import unittest

BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "water_softener_refill_sensor")


def _load(lang):
    with open(os.path.join(BASE, "translations", f"{lang}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _keys(node, prefix=""):
    for key, value in node.items():
        if isinstance(value, dict):
            yield from _keys(value, f"{prefix}{key}.")
        else:
            yield f"{prefix}{key}"


def _source():
    text = ""
    for name in os.listdir(BASE):
        if name.endswith(".py"):
            with open(os.path.join(BASE, name), encoding="utf-8") as fh:
                text += fh.read()
    return text


class Translations(unittest.TestCase):
    def test_same_keys(self):
        self.assertEqual(sorted(_keys(_load("de"))), sorted(_keys(_load("en"))))

    def test_same_placeholders(self):
        de, en = _load("de"), _load("en")
        flat_en = dict(zip(_keys(en), _values(en)))
        for key, value in zip(_keys(de), _values(de)):
            self.assertEqual(set(re.findall(r"{(\w+)}", value)), set(re.findall(r"{(\w+)}", flat_en[key])), key)

    def test_every_service_error_has_a_text(self):
        used = set(re.findall(r'service_error\(\s*"(\w+)"', _source()))
        self.assertTrue(used)
        for lang in ("de", "en"):
            self.assertEqual(used, set(_load(lang)["exceptions"]), lang)

    def test_notification_texts(self):
        used = set(re.findall(r'"(unknown_unit|low_salt_one|low_salt|regeneration_overdue)"', _source()))
        self.assertEqual(used, {"unknown_unit", "low_salt", "low_salt_one", "regeneration_overdue"})
        for lang in ("de", "en"):
            issues = _load(lang)["issues"]
            self.assertEqual(set(issues), used, lang)
            for issue in issues.values():
                self.assertEqual(set(issue), {"title", "description"})

    def test_clock_mode_options_match_logic(self):
        with open(os.path.join(BASE, "logic.py"), encoding="utf-8") as fh:
            modes = set(re.findall(r'^CLOCK_\w+ = "(\w+)"', fh.read(), re.M))
        self.assertEqual(modes, {"standard", "dst", "manual"})
        for lang in ("de", "en"):
            data = _load(lang)
            self.assertEqual(set(data["selector"]["clock_mode"]["options"]), modes, lang)
            for section, step in (("config", "user"), ("options", "init")):
                self.assertIn("clock_mode", data[section]["step"][step]["data"])
                self.assertIn("clock_mode", data[section]["step"][step]["data_description"])

    def test_config_errors_are_reachable(self):
        source = _source()
        for lang in ("de", "en"):
            data = _load(lang)
            for section in ("config", "options"):
                for key in data[section].get("error", {}):
                    self.assertIn(f'"{key}"', source, f"{lang}: {section}.error.{key}")
            # Fehler, die der Optionsfluss nicht erzeugen kann, stehen dort nicht
            self.assertNotIn("stock_too_large", data["options"]["error"])


def _values(node):
    for value in node.values():
        if isinstance(value, dict):
            yield from _values(value)
        else:
            yield value


if __name__ == "__main__":
    unittest.main()
