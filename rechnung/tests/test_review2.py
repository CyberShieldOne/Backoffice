#!/usr/bin/env python3
"""Regressionstests zum zweiten xhigh-Review (gesamter PR). Ein Test je Fund (Nummer = Fund).

Aufruf: python -m unittest tests/test_review2.py   (aus dem Ordner rechnung/)
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from decimal import Decimal
from pathlib import Path
from unittest import mock

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER.parent))
sys.path.insert(0, str(HIER))
import app  # noqa: E402
import rechnung_aus_ab as ra  # noqa: E402
import test_ariba  # noqa: E402
import test_rechnung as gen  # noqa: E402

from reportlab.pdfgen import canvas  # noqa: E402

HEUTE = dt.date(2026, 10, 1)
SKRIPT = HIER.parent / "rechnung_aus_ab.py"


def ab(t: Path, name="ab.pdf", seed=1, **erzwinge) -> Path:
    p = t / name
    gen.erzeuge_ab(p, random.Random(seed), erzwinge or None)
    return p


def sap(t: Path, name="sap.pdf", seed=7) -> Path:
    p = t / name
    test_ariba.erzeuge_ariba(p, random.Random(seed))
    return p


class Kern(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())

    def test_01_leerzeichen_als_tausendertrenner(self):
        for text, soll in {"EUR 4 500,00": "4500.00", "EUR 12 345,67": "12345.67",
                           "1 234 567,89 €": "1234567.89", "4 500,00": "4500.00",
                           "4 500,-": "4500.00", "EUR 950,00": "950.00"}.items():
            with self.subTest(text=text):
                self.assertEqual(ra.parse_betrag(text), Decimal(soll))

    def test_02_beleg_neben_ausgabe_wird_nie_geloescht(self):
        quelle = sap(self.t, "RE-X.pdf")
        inhalt = quelle.read_bytes()
        for pdf in (False, True):
            with self.subTest(pdf=pdf):
                erg = ra.erstelle_rechnung(quelle, ausgabe=self.t / "RE-X.docx", pdf=pdf, ueberschreiben=True)
                self.assertEqual(quelle.read_bytes(), inhalt, "Beleg verändert/gelöscht")
                if pdf:
                    self.assertIsNone(erg.pdf)
                    self.assertTrue(any("Beleg selbst" in h.text for h in erg.hinweise))

    def test_04_klammern_in_kundendaten(self):
        p = ab(self.t)
        a = ra.lese_beleg(p)
        a.kunde, a.projekt = "ACME [K-1042] GmbH", "Pentest [Phase 2]"
        erg = ra.erstelle_rechnung(p, "2026-0001", datum=HEUTE, ausgabe=self.t / "r.docx", auftrag=a)
        text = gen.dokument_text(erg.docx)
        self.assertIn("ACME [K-1042] GmbH", text)
        self.assertIn("Pentest [Phase 2]", text)
        self.assertNotIn("", text)

    def test_05_bestellzeile(self):
        faelle = {
            "Ihre Bestellung 45104423 per Email vom: 29.10.2025": ("45104423", dt.date(2025, 10, 29)),
            "Ihre Bestellung per Email vom: 29.10.2025": ("", dt.date(2025, 10, 29)),
            "Ihre Bestellung Nr. 4510442": ("4510442", None),
            "Ihre Bestellung Nr.: PO-2026/77 vom 01.02.2026": ("PO-2026/77", dt.date(2026, 2, 1)),
        }
        for zeile, soll in faelle.items():
            with self.subTest(zeile=zeile):
                self.assertEqual(ra.lies_bestellung(zeile, []), soll)
        h: list = []
        self.assertEqual(ra.lies_bestellung("Ihre Bestellung 4711 vom 31.09.2026", h), ("4711", None))
        self.assertTrue(h, "unlesbares Bestelldatum ohne Hinweis")

    def test_06_cli_meldet_sap_fehler_ohne_traceback(self):
        kaputt = self.t / "sap_kaputt.pdf"
        c = canvas.Canvas(str(kaputt))
        c.drawString(100, 750, "Standardrechnung")
        c.drawString(100, 730, "Rechnungsnummer (Lieferantenreferenznr.)")
        c.save()
        lauf = subprocess.run([sys.executable, str(SKRIPT), str(kaputt), "-o", str(self.t / "x.docx")],
                              capture_output=True, text=True)
        self.assertEqual(lauf.returncode, 2, lauf.stderr)
        self.assertIn("FEHLER (Beleg): SAP-Rechnung", lauf.stderr)
        self.assertNotIn("Traceback", lauf.stderr)

    def test_07_unmoegliches_datum(self):
        with self.assertRaises(ra.AbFehler):
            ra.parse_datum("31.09.2026")

    def test_08_kern_ueberschreibt_keine_fremde_rechnung(self):
        p = ab(self.t)
        a = ra.lese_beleg(p)
        ziel = self.t / "r.docx"
        ra.erstelle_rechnung(p, "RE 2026/77", datum=HEUTE, ausgabe=ziel, auftrag=a)
        alt = ziel.read_bytes()
        with self.assertRaises(ra.DateiExistiert) as k:
            ra.erstelle_rechnung(p, "RE_2026_77", datum=HEUTE, ausgabe=ziel, auftrag=a, ueberschreiben=True)
        self.assertTrue(k.exception.fremd)
        with self.assertRaises(ra.DateiExistiert) as k:
            ra.erstelle_rechnung(p, "RE 2026/77", datum=HEUTE, ausgabe=ziel, auftrag=a)
        self.assertFalse(k.exception.fremd)
        self.assertEqual(ziel.read_bytes(), alt)
        ra.erstelle_rechnung(p, "RE 2026/77", datum=HEUTE, ausgabe=ziel, auftrag=a, ueberschreiben=True)
        # Kommandozeile: ohne --ueberschreiben Abbruch mit Hinweis
        lauf = subprocess.run([sys.executable, str(SKRIPT), str(p), "--rechnungsnr", "RE 2026/77",
                               "-o", str(ziel)], capture_output=True, text=True)
        self.assertEqual(lauf.returncode, 3, lauf.stderr)
        self.assertIn("--ueberschreiben", lauf.stderr)

    def test_10_sap_mit_explizitem_uebergabedatum(self):
        erg = ra.erstelle_rechnung(sap(self.t), ausgabe=self.t / "r.docx", uebergabe=dt.date(2026, 10, 15))
        self.assertIn("Roadmap wurden am 15.10.2026 übergeben", gen.dokument_text(erg.docx))

    def test_11_schreiben_ist_atomar(self):
        p = ab(self.t)
        ziel = self.t / "r.docx"
        ra.erstelle_rechnung(p, "2026-0001", datum=HEUTE, ausgabe=ziel)
        alt = ziel.read_bytes()
        with mock.patch.object(zipfile.ZipFile, "writestr", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                ra.erstelle_rechnung(p, "2026-0001", datum=HEUTE, ausgabe=ziel, ueberschreiben=True)
        self.assertEqual(ziel.read_bytes(), alt, "vorhandene Rechnung beschädigt")
        self.assertEqual(sorted(x.name for x in self.t.iterdir() if x.name.startswith(".~")), [], "Temp-Datei übrig")

    def test_12_rechnungsnummer_wird_geprueft(self):
        p = ab(self.t)
        for nr in ("RE\\2026", "RE\\x1", "2026<1>"):
            with self.subTest(nr=nr), self.assertRaises(ra.AbFehler):
                ra.erstelle_rechnung(p, nr, datum=HEUTE, ausgabe=self.t / "r.docx")
        erg = ra.erstelle_rechnung(p, "RE 2026/77 #1+", datum=HEUTE, ausgabe=self.t / "ok.docx")
        self.assertEqual(ra.rechnungs_identitaet(erg.docx)[0], "RE 2026/77 #1+")

    def test_13_keine_doppelten_absatz_ids(self):
        p = None
        for seed in range(1, 60):  # AB mit mehreren Positionen suchen
            kand = ab(self.t, f"m{seed}.pdf", seed=seed)
            if len(ra.lese_beleg(kand).positionen) >= 2:
                p = kand
                break
        self.assertIsNotNone(p)
        erg = ra.erstelle_rechnung(p, "2026-0001", datum=HEUTE, ausgabe=self.t / "r.docx")
        with zipfile.ZipFile(erg.docx) as z:
            doc = z.read("word/document.xml").decode()
        ids = re.findall(r'w14:paraId="([0-9A-Fa-f]+)"', doc)
        self.assertEqual(len(ids), len(set(ids)), "doppelte w14:paraId")

    def test_15_beleg_wird_einmal_geoeffnet(self):
        import pdfplumber
        for p in (ab(self.t), sap(self.t)):
            with self.subTest(beleg=p.name), mock.patch.object(pdfplumber, "open", wraps=pdfplumber.open) as auf:
                ra.lese_beleg(p)
                self.assertEqual(auf.call_count, 1)


class Nummern(unittest.TestCase):
    def test_09_korrektur_aus_vorjahr_setzt_nicht_zurueck(self):
        h = app.hoehere_nummer
        self.assertEqual(h("2027-0005", "2026-0142"), "2027-0005")
        self.assertEqual(h("2026-0142", "2027-0001"), "2027-0001")
        self.assertEqual(h("RE-2026-0009", "RE-2026-0010"), "RE-2026-0010")
        self.assertEqual(h("2026-0142", "RE-2026-0001"), "RE-2026-0001")  # neues Schema


class Server(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(app, "STATUS_DIR", self.t / "status"),
                        mock.patch.object(app, "STATUS_DATEI", self.t / "status" / "e.json")]
        for p in self.patches:
            p.start()
        self.faden = threading.Thread(target=app.main, args=(["--kein-browser"],), daemon=True)
        self.faden.start()
        for _ in range(50):
            url = app.lade_status().get("laufend")
            if url:
                break
            time.sleep(0.1)
        self.basis = url.rsplit("/", 2)[0]
        self.token = url.rstrip("/").rsplit("/", 1)[1]
        self.ordner = self.t / "rechnungen"
        self.ordner.mkdir()

    def tearDown(self):
        try:
            self.post("/api/beenden", {})
        except OSError:
            pass
        self.faden.join(5)
        for p in self.patches:
            p.stop()

    def post(self, pfad, daten, kopf=None):
        roh = daten if isinstance(daten, bytes) else json.dumps(daten).encode()
        req = urllib.request.Request(self.basis + pfad, data=roh, method="POST",
                                     headers={"X-Token": self.token, **(kopf or {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def lesen(self, p: Path):
        return self.post("/api/lesen", p.read_bytes(), {"X-Dateiname": p.name})

    def erstellen(self, uid, nr, **extra):
        d = {"id": uid, "nummer": nr, "datum": HEUTE.isoformat(), "ordner": str(self.ordner)}
        d.update(extra)
        return self.post("/api/erstellen", d)

    def test_03_nur_pdf_vorhanden_zaehlt_als_vergeben(self):
        st, d = self.lesen(ab(self.t))
        kunde = d["auftrag"]["kunde"]
        # versendete Rechnung, nur noch als PDF vorhanden – anderer Kunde
        (self.ordner / "2026-0142_Rechnung_Anderer_Kunde_GmbH.pdf").write_bytes(b"%PDF")
        st, f = self.erstellen(d["id"], "2026-0142")
        self.assertEqual(st, 409)
        self.assertIn("bereits vergeben", f["fehler"])
        # gleicher Kunde, nur PDF vorhanden → Rückfrage statt stillem Überschreiben
        nur_pdf = self.ordner / ra.rechnungs_dateiname("2026-0200", kunde).replace(".docx", ".pdf")
        nur_pdf.write_bytes(b"%PDF versendet")
        st, f = self.erstellen(d["id"], "2026-0200")
        self.assertEqual((st, f.get("existiert")), (409, True))
        self.assertEqual(nur_pdf.read_bytes(), b"%PDF versendet")
        self.assertIn("2026-0200", app.vergebene_nummern(self.ordner))

    def test_14_mehrere_tabs(self):
        st, a = self.lesen(ab(self.t, "a.pdf", seed=1))
        st, b = self.lesen(ab(self.t, "b.pdf", seed=2))   # zweiter Tab
        st, f = self.erstellen(a["id"], "2026-0300")        # erster Tab erstellt weiterhin
        self.assertEqual(st, 200, f)


if __name__ == "__main__":
    unittest.main()
