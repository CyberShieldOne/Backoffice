#!/usr/bin/env python3
"""Einstellungen/Historie: nie durch gleichzeitiges Schreiben verlieren; verlorene Einträge aus den
Ablageordnern zurückholen.

Aufruf: python -m unittest tests/test_historie.py   (aus dem Ordner rechnung/)
"""
from __future__ import annotations

import json
import multiprocessing as mp
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER.parent))
sys.path.insert(0, str(HIER))
import app  # noqa: E402
import rechnung_aus_ab as ra  # noqa: E402
import test_rechnung as gen  # noqa: E402

GROSS = {"ordner": "/x/Rechnungen", "letzte_nummer": "2026-0150",
         "historie": [{"nummer": f"2026-{i:04d}", "kunde": "OHB SE", "docx": f"/x/{i}.docx"} for i in range(150)]}


def _schreiber(datei: str, n: int) -> None:   # alte Instanz beim Beenden: lesen, ändern, schreiben
    with mock.patch.object(app, "STATUS_DATEI", Path(datei)), mock.patch.object(app, "STATUS_DIR", Path(datei).parent):
        for _ in range(n):
            st = app.lade_status()
            st.pop("laufend", None)
            app.speichere_status(st)


class Einstellungen(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.datei = self.t / "status" / "einstellungen.json"
        for p in (mock.patch.object(app, "STATUS_DATEI", self.datei),
                  mock.patch.object(app, "STATUS_DIR", self.datei.parent)):
            p.start()
            self.addCleanup(p.stop)

    def test_gleichzeitig_schreiben_verliert_nichts(self):
        """Update: neue Instanz liest, während die alte speichert – nie ein leerer Stand."""
        app.speichere_status(GROSS)
        proc = mp.get_context("fork").Process(target=_schreiber, args=(str(self.datei), 200))
        proc.start()
        leer = sum(1 for _ in range(200) if not app.lade_status().get("historie"))
        proc.join()
        self.assertEqual(leer, 0)
        self.assertEqual(len(app.lade_status()["historie"]), 150)

    def test_defekte_datei_sicherung_statt_leer(self):
        app.speichere_status(GROSS)
        app.speichere_status(dict(GROSS, pdf=True))     # Sicherung = voriger guter Stand
        self.datei.write_text('{"historie": [', encoding="utf-8")   # halb geschrieben
        st = app.lade_status()
        self.assertEqual(len(st["historie"]), 150)
        self.assertTrue(list(self.datei.parent.glob("einstellungen.defekt-*.json")), "defekte Datei aufgehoben")


class Wiederherstellen(unittest.TestCase):
    def test_historie_aus_ordner_mit_exakter_nummer_und_kunde(self):
        """Einstellungen verloren: Rechnungen im (im Formular eingetragenen) Ordner erscheinen wieder,
        Nummer und Kunde aus den Dokumenteigenschaften statt aus dem Dateinamen."""
        t = Path(tempfile.mkdtemp())
        ordner = t / "Kunden" / "Rechnungen 2026"
        ordner.mkdir(parents=True)
        pdf = t / "ab.pdf"
        gen.erzeuge_ab(pdf, random.Random(7))
        erg = ra.erstelle_rechnung(pdf, "RE 2026/77", ausgabe=ordner / ra.rechnungs_dateiname("RE 2026/77", "x"))
        kunde = erg.rechnung.auftrag.kunde
        with mock.patch.object(app, "STANDARD_ORDNER", t / "leer"):
            self.assertEqual(app.historie({}, t / "leer"), [])                       # ohne Hinweis unauffindbar
            h = app.historie({}, t / "leer", zusatz=[ordner])                        # Ordner im Formular
            self.assertEqual([(e["nummer"], e["kunde"]) for e in h], [("RE 2026/77", kunde)])
            st = {}
            app.merke_ordner(st, ordner)                                            # künftig gemerkt
            self.assertEqual(len(app.historie(st, t / "leer")), 1)


if __name__ == "__main__":
    unittest.main()
