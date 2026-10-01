#!/usr/bin/env python3
"""Regressionstests zu den Funden aus dem Code-Review (je Fund mindestens ein Test).

Aufruf: python -m unittest tests/test_regression.py   (aus dem Ordner rechnung/)
Abhängigkeiten: pdfplumber, reportlab
"""
from __future__ import annotations

import datetime as dt
import json
import random
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path
from unittest import mock

HIER = Path(__file__).resolve().parent
import sys  # noqa: E402

sys.path.insert(0, str(HIER.parent))
sys.path.insert(0, str(HIER))
import app  # noqa: E402
import rechnung_aus_ab as ra  # noqa: E402
import test_rechnung as gen  # noqa: E402

HEUTE = dt.date(2026, 10, 1)


def ab_pdf(ordner: Path, name="ab.pdf", seed=1, **erzwinge) -> tuple[Path, dict]:
    pfad = ordner / name
    soll = gen.erzeuge_ab(pfad, random.Random(seed), erzwinge or None)
    return pfad, soll


class Betraege(unittest.TestCase):  # Fund 1
    def test_ohne_tausenderpunkt(self):
        faelle = {
            "EUR 4500,-": "4500.00", "4500,00 €": "4500.00", "EUR 12345,67": "12345.67",
            "10000": "10000.00", "EUR 4.500,-": "4500.00", "12.345,67 €": "12345.67",
            "EUR 950,-": "950.00", "1.234.567,89": "1234567.89", "EUR 45,5": "45.50",
        }
        for text, soll in faelle.items():
            with self.subTest(text=text):
                self.assertEqual(ra.parse_betrag(text), Decimal(soll))

    def test_ab_ohne_tausenderpunkt_end_to_end(self):
        with tempfile.TemporaryDirectory() as t:
            for stil in (3, 4):
                pfad, soll = ab_pdf(Path(t), f"ab{stil}.pdf", seed=7, betrag_stil=stil)
                a = ra.lese_ab(pfad)
                self.assertEqual(a.summe_netto, soll["summe_netto"])
                self.assertGreaterEqual(a.summe_netto, Decimal("100"))


class Liefertermin(unittest.TestCase):  # Fund 9
    def test_text_statt_datum_blockiert_nicht(self):
        with tempfile.TemporaryDirectory() as t:
            pfad, _ = ab_pdf(Path(t), mit_liefer=True, liefer_text=True)
            a = ra.lese_ab(pfad)
            self.assertIsNone(a.liefertermin)
            self.assertTrue(any("Liefertermin" in h.text for h in a.hinweise))
            erg = ra.erstelle_rechnung(pfad, "2026-0001", datum=HEUTE, ausgabe=Path(t) / "r.docx")
            self.assertTrue(erg.docx.exists())
            self.assertIn("Liefertermin", " ".join(app.hinweise_fuer_ui(erg.hinweise)))


class Nummern(unittest.TestCase):  # Fund 3
    def test_vorschlag(self):
        n = app.naechste_nummer
        self.assertEqual(n("2026-0142", [], HEUTE), "2026-0143")
        self.assertEqual(n("2026-0140", ["2026-0141", "2026-0142"], HEUTE), "2026-0143")
        self.assertEqual(n("RE-2026-0142", [], HEUTE), "RE-2026-0143")
        self.assertEqual(n("2026/0142", ["2026_0150"], HEUTE), "2026/0151")
        self.assertEqual(n("2025-0142", [], HEUTE), "2026-0001")
        self.assertEqual(n("2025-0142", ["2026-0003"], HEUTE), "2026-0004")
        self.assertEqual(n(None, [], HEUTE), "2026-0001")
        self.assertEqual(n("ABC", [], HEUTE), "2026-0001")

    def test_letzte_nummer_nur_vorwaerts(self):
        self.assertEqual(app.hoehere_nummer("2026-0142", "2026-0140"), "2026-0142")
        self.assertEqual(app.hoehere_nummer("2026-0142", "2026-0143"), "2026-0143")
        self.assertEqual(app.hoehere_nummer("2026-0142", "RE-2026-0001"), "RE-2026-0001")
        self.assertEqual(app.hoehere_nummer(None, "2026-0001"), "2026-0001")


class Pdf(unittest.TestCase):  # Funde 4 und 8
    def test_fehlgeschlagener_export_ist_hinweis_und_altes_pdf_weg(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            pfad, _ = ab_pdf(t)
            ziel = t / "r.docx"
            altes = ziel.with_suffix(".pdf")
            altes.write_bytes(b"%PDF alt")
            with mock.patch.object(ra, "finde_soffice", return_value="/bin/false"):
                erg = ra.erstelle_rechnung(pfad, "2026-0001", datum=HEUTE, ausgabe=ziel, pdf=True)
            self.assertTrue(erg.docx.exists())
            self.assertIsNone(erg.pdf)
            self.assertFalse(altes.exists(), "altes PDF darf nicht als Ergebnis stehen bleiben")
            self.assertTrue(any("PDF-Export fehlgeschlagen" in h.text for h in erg.hinweise))

    def test_ohne_pdf_wird_veraltetes_pdf_entfernt(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            pfad, _ = ab_pdf(t)
            ziel = t / "r.docx"
            ziel.with_suffix(".pdf").write_bytes(b"%PDF alt")
            erg = ra.erstelle_rechnung(pfad, "2026-0001", datum=HEUTE, ausgabe=ziel, pdf=False)
            self.assertFalse(ziel.with_suffix(".pdf").exists())
            self.assertIsNone(erg.pdf)


class Bestellnummer(unittest.TestCase):  # Wunsch: Bestellnummer des Kunden berücksichtigen
    def text(self, docx: Path) -> str:
        return "\n".join(gen.dokument_text(docx).splitlines())

    def test_aus_ab_ueberschreiben_und_weglassen(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            pfad, soll = ab_pdf(t, seed=3)
            self.assertTrue(soll["bestell_nr"])
            # 1. aus der AB
            erg = ra.erstelle_rechnung(pfad, "2026-0001", datum=HEUTE, ausgabe=t / "a.docx")
            tx = self.text(erg.docx)
            self.assertIn(f"Ihre Bestellung  {soll['bestell_nr']} vom", tx)
            self.assertIn(f"Bestell-Nr. {soll['bestell_nr']}", tx)
            # 2. überschrieben (z. B. Kostenstelle/abweichende PO)
            erg = ra.erstelle_rechnung(pfad, "2026-0002", datum=HEUTE, ausgabe=t / "b.docx",
                                       bestell_nr="PO-77", bestell_datum=dt.date(2026, 9, 1))
            tx = self.text(erg.docx)
            self.assertIn("Ihre Bestellung  PO-77 vom 01.09.2026", tx)
            self.assertIn("gemäß Ihrer Bestellung PO-77 vom 01.09.2026 und Auftragsbestätigung", tx)
            self.assertIn("Verwendungszweck: Rechnung 2026-0002 / Bestell-Nr. PO-77", tx)
            self.assertNotIn(soll["bestell_nr"], tx)
            # 3. bewusst keine
            erg = ra.erstelle_rechnung(pfad, "2026-0003", datum=HEUTE, ausgabe=t / "c.docx", bestell_nr="")
            tx = self.text(erg.docx)
            self.assertNotIn("Ihre Bestellung", tx)
            self.assertIn("Kostenstelle: —", tx)
            self.assertIn("Verwendungszweck: Rechnung 2026-0003\n", tx + "\n")
            self.assertTrue(any("Bestellnummer" in h.text for h in erg.hinweise))


class Vorlage(unittest.TestCase):  # Fußzeile, Webseite, Schrift
    def test_fusszeile_und_schrift(self):
        import zipfile
        with tempfile.TemporaryDirectory() as t:
            pfad, _ = ab_pdf(Path(t))
            erg = ra.erstelle_rechnung(pfad, "RE 2026/77", datum=HEUTE, ausgabe=Path(t) / "r.docx")
            with zipfile.ZipFile(erg.docx) as z:
                fuss = z.read("word/footer1.xml").decode()
                stile = z.read("word/styles.xml").decode()
            texte = ra.absatz_texte(fuss)
            self.assertIn("Rechnung RE 2026/77 · cyber-shield.org", texte)
            self.assertNotIn("cyber-shield.dev", fuss)
            self.assertIn("IBAN DE67 5075 0094 0000 0954 31", texte)
            # Seitenzahlen gleich formatiert wie der Text daneben
            self.assertEqual(fuss.count('<w:fldChar w:fldCharType="begin"/>'), 2)
            self.assertNotIn("<w:r><w:fldChar", fuss)
            self.assertIn('w:ascii="Calibri"', stile)
            self.assertNotIn("Carlito", stile)

    def test_einheitliche_ausrichtung(self):
        """Word-2013-Modus: tblInd verschiebt Kästen → kein Einzug; Tabellen = Satzspiegel."""
        import re
        import zipfile
        with tempfile.TemporaryDirectory() as t:
            pfad, _ = ab_pdf(Path(t))
            erg = ra.erstelle_rechnung(pfad, "2026-0001", datum=HEUTE, ausgabe=Path(t) / "r.docx")
            with zipfile.ZipFile(erg.docx) as z:
                doc = z.read("word/document.xml").decode()
        self.assertNotIn("<w:tblInd", doc)
        breiten = re.findall(r'<w:tblW w:w="(\d+)"', doc)
        self.assertTrue(set(breiten) <= {"10206", "5800"}, breiten)  # Satzspiegel bzw. Summenblock
        # Positionstabelle: Text der ersten/letzten Spalte bündig mit dem Fließtext
        pos = next(m.group(0) for m in re.finditer(r"<w:tbl>.*?</w:tbl>", doc, re.S) if "Bezeichnung der Leistung" in m.group(0))
        for tr in re.findall(r"<w:tr[ >].*?</w:tr>", pos, re.S):
            tcs = re.findall(r"<w:tcPr>.*?</w:tcPr>", tr)
            self.assertIn('<w:left w:w="0"', tcs[0])
            self.assertIn('<w:right w:w="0"', tcs[-1])


class Hinweise(unittest.TestCase):  # Fund 14
    def test_ui_ohne_standardwerte_und_cli_optionen(self):
        hs = [ra.Hinweis("Leistungsende = 01.10.2026", "--bis", standard=True),
              ra.Hinweis("USt-IdNr. fehlt", "--ust-id")]
        self.assertEqual(app.hinweise_fuer_ui(hs), ["USt-IdNr. fehlt"])
        self.assertEqual(str(hs[0]), "Leistungsende = 01.10.2026 (überschreibbar mit --bis)")
        self.assertEqual(str(hs[1]), "USt-IdNr. fehlt (--ust-id)")


class Server(unittest.TestCase):
    """Startet app.main() mit eigenem Status-Ordner und spricht die API über HTTP an."""

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
        self.zustand = app.Handler.zustand
        self.ordner = self.t / "rechnungen"

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

    def lesen(self, pfad: Path):
        return self.post("/api/lesen", pfad.read_bytes(), {"X-Dateiname": pfad.name})

    def erstellen(self, uid, nr, **extra):
        d = {"id": uid, "nummer": nr, "datum": HEUTE.isoformat(), "ordner": str(self.ordner)}
        d.update(extra)
        return self.post("/api/erstellen", d)

    def test_ablauf_doppelte_nummer_relativer_ordner_oeffnen(self):  # Funde 3, 12, 13, 15
        a_pdf, _ = ab_pdf(self.t, "a.pdf", seed=1)
        b_pdf, _ = ab_pdf(self.t, "b.pdf", seed=2)
        zaehler = mock.Mock(wraps=ra.lese_ab)
        with mock.patch.object(ra, "lese_ab", zaehler):
            st, d = self.lesen(a_pdf)
            self.assertEqual(st, 200, d)
            # Fund 15: Beträge kommen vom Server
            self.assertEqual(d["auftrag"]["brutto_fmt"],
                             ra.fmt_betrag(Decimal(d["auftrag"]["summe_netto"])
                                           + ra.ust_von(Decimal(d["auftrag"]["summe_netto"]))))
            st, e = self.erstellen(d["id"], "2026-0142")
            self.assertEqual(st, 200, e)
            self.assertEqual(zaehler.call_count, 1, "AB nur einmal lesen (Fund 13)")
        self.assertEqual(e["naechste_nummer"], "2026-0143")

        # Fund 12: relativer Ordner
        st, f = self.erstellen(d["id"], "2026-0143", ordner="Rechnungen")
        self.assertEqual(st, 422)
        self.assertIn("vollständiger Pfad", f["fehler"])

        # Fund 3: gleiche Nummer für anderen Kunden → abgelehnt, nicht überschreibbar
        st, db = self.lesen(b_pdf)
        self.assertEqual(st, 200)
        if db["auftrag"]["kunde"] != d["auftrag"]["kunde"]:
            st, f = self.erstellen(db["id"], "2026-0142", ueberschreiben=True)
            self.assertEqual(st, 409)
            self.assertIn("bereits vergeben", f["fehler"])
        # Korrektur einer älteren Nummer setzt den Vorschlag nicht zurück
        st, f = self.erstellen(db["id"], "2026-0100")
        self.assertEqual(st, 200, f)
        self.assertEqual(f["naechste_nummer"], "2026-0143")
        st, s = self.post("/api/start", {})
        self.assertEqual(s["nummer"], "2026-0143")

        # Rechnungsnummer frei eingebbar (Leerzeichen, Schrägstrich); ungültige Zeichen klar gemeldet
        st, f = self.erstellen(db["id"], "RE 2026/77")
        self.assertEqual(st, 200, f)
        self.assertTrue(f["docx"].endswith("RE_2026_77_Rechnung_" + ra._dateiname(db["auftrag"]["kunde"]) + ".docx"))
        st, f = self.erstellen(db["id"], "2026<0001>")
        self.assertEqual(st, 422)
        self.assertIn("Erlaubt:", f["fehler"])
        st, f = self.erstellen(db["id"], "  ")
        self.assertEqual(st, 422)

        # nur selbst erstellte Dateien dürfen geöffnet werden
        st, _ = self.post("/api/oeffnen", {"pfad": "/etc/passwd"})
        self.assertEqual(st, 403)

    def test_historie(self):  # Wunsch: Historie der letzten Rechnungen
        a_pdf, _ = ab_pdf(self.t, "a.pdf", seed=1)
        st, d = self.lesen(a_pdf)
        self.assertEqual(self.erstellen(d["id"], "2026-0001")[0], 200)
        time.sleep(1.1)  # Sekundenauflösung des Zeitstempels
        st, e2 = self.erstellen(d["id"], "2026-0002")
        self.assertEqual(st, 200)
        # per Kommandozeile erstellte Rechnung im Ordner (ohne gemerkten Eintrag)
        fremd = self.ordner / "2025-0900_Rechnung_Alt_GmbH.docx"
        fremd.write_bytes(b"x")
        os_zeit = time.time() - 86400
        import os
        os.utime(fremd, (os_zeit, os_zeit))
        st, h = self.post("/api/historie", {})
        nummern = [e["nummer"] for e in h["eintraege"]]
        self.assertEqual(nummern, ["2026-0002", "2026-0001", "2025-0900"])
        self.assertEqual(h["eintraege"][0]["brutto"], e2["brutto"])
        self.assertEqual(h["eintraege"][2]["kunde"], "Alt GmbH")
        # Rechnungen aus der Historie dürfen auch in einer neuen Sitzung geöffnet werden
        self.zustand.erstellt.clear()
        with mock.patch.object(app, "oeffne") as geoeffnet:
            self.assertEqual(self.post("/api/oeffnen", {"pfad": h["eintraege"][1]["docx"]})[0], 200)
            self.assertEqual(self.post("/api/oeffnen", {"pfad": str(fremd)})[0], 200)
            self.assertEqual(self.post("/api/oeffnen", {"pfad": str(a_pdf)})[0], 403)
            self.assertEqual(geoeffnet.call_count, 2)
        # gelöschte Rechnung verschwindet aus der Historie
        Path(h["eintraege"][1]["docx"]).unlink()
        st, h = self.post("/api/historie", {})
        self.assertEqual([e["nummer"] for e in h["eintraege"]], ["2026-0002", "2025-0900"])
        # Überschreiben derselben Rechnung: ein Eintrag, nicht zwei
        self.assertEqual(self.erstellen(d["id"], "2026-0002", ueberschreiben=True)[0], 200)
        st, h = self.post("/api/historie", {})
        self.assertEqual([e["nummer"] for e in h["eintraege"]].count("2026-0002"), 1)

    def test_hochgeladene_pdfs_werden_geloescht(self):  # Fund 11
        tmp = self.zustand.tmp
        a_pdf, _ = ab_pdf(self.t, "a.pdf", seed=1)
        b_pdf, _ = ab_pdf(self.t, "b.pdf", seed=2)
        self.lesen(a_pdf)
        self.assertEqual(len(list(tmp.iterdir())), 1)
        self.lesen(b_pdf)
        self.assertEqual(len(list(tmp.iterdir())), 1, "vorherige AB muss gelöscht sein")
        (self.t / "kaputt.pdf").write_bytes(b"%PDF-1.4 kaputt")
        st, _ = self.lesen(self.t / "kaputt.pdf")
        self.assertNotEqual(st, 200)
        self.assertEqual(len(list(tmp.iterdir())), 1, "unlesbare AB muss gelöscht sein")
        self.post("/api/beenden", {})
        self.faden.join(5)
        self.assertFalse(tmp.exists(), "Temp-Ordner muss beim Beenden weg sein")

    def test_waechter_nutzt_monotone_uhr(self):  # Fund 5
        self.post("/api/ping", {})
        self.assertLess(abs(self.zustand.start - time.monotonic()), 60)
        self.assertLess(abs(self.zustand.letzter_kontakt - time.monotonic()), 60)


if __name__ == "__main__":
    unittest.main()
