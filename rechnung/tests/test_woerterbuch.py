#!/usr/bin/env python3
"""Tests für das Wörterbuch der Feldbezeichnungen (woerterbuch.json).

Die synthetischen Belege werden mit umbenannten Beschriftungen erzeugt (Synonyme aus dem Wörterbuch)
und müssen genauso gelesen werden wie die Originale.

Aufruf: python -m unittest tests/test_woerterbuch.py   (aus dem Ordner rechnung/)
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER.parent))
sys.path.insert(0, str(HIER))
import rechnung_aus_ab as ra  # noqa: E402
import test_ariba as sap  # noqa: E402
import test_rechnung as gen  # noqa: E402
import woerterbuch as wb  # noqa: E402

from reportlab.pdfgen import canvas  # noqa: E402

AB_SYNONYME = {
    "Datum:": "AB-Datum:", "Kunde:": "Auftraggeber:", "Anschrift:": "Adresse:",
    "Kunden-Nr.:": "Kundennummer:", "Kundenkontakt:": "Ansprechpartner:", "Projekt:": "Betreff:",
    "Ihre Bestellung": "Ihre Bestellnummer", "Liefertermin:": "Lieferdatum:",
    "Produkte": "Leistung", "Menge": "Anzahl", "Summe (Netto)": "Betrag",
    "Leistungsbeschreibung:": "Leistungsumfang:", "Summe Auftrag (Netto)": "Gesamtsumme (Netto)",
    "Zahlungsziel:": "Zahlbar innerhalb von",
}
KOPIE_SYNONYME = {
    "Rechnungsnummer": "Rechnungs-Nr.", "Service-Startdatum": "Leistungsbeginn",
    "Service-Enddatum": "Leistungsende", "Bestellauftragsnr. :": "Bestellnummer:",
    "Bestellauftragsdatum:": "Bestelldatum:", "Nettozahlungsbedingungen (Tage) :": "Zahlungsziel:",
    "Zwischensumme": "Gesamtpreis", "Positionsnr.": "Pos.-Nr.",
}
DRUCK_SYNONYME = {
    "Startdatum:": "Leistungsbeginn:", "Enddatum:": "Leistungsende:", "Nettobedingung:": "Zahlungsziel:",
    "Gesamtbetrag ohne Steuern:": "Nettobetrag gesamt:", "Steuern insgesamt:": "Steuerbetrag gesamt:",
    "Fälliger Betrag:": "Zu zahlender Betrag:",
    "Umsatzsteuer-/Steuernummer des Kunden:": "USt-IdNr. des Kunden:", "Positionsnr.": "Pos.-Nr.",
}


@contextlib.contextmanager
def umbenannt(synonyme: dict[str, str], genau: dict[str, str] | None = None):
    """Beschriftungen beim Zeichnen ersetzen (Präfix-Treffer, längster zuerst; 'genau': ganzer Text)."""
    genau = genau or {}
    orig = {n: getattr(canvas.Canvas, n) for n in ("drawString", "drawRightString", "drawCentredString")}
    schluessel = sorted(synonyme, key=len, reverse=True)

    def huelle(f):
        def neu(self, x, y, text, *a, **k):
            if text in genau:
                return f(self, x, y, genau[text], *a, **k)
            alt = next((s for s in schluessel if text.startswith(s)), None)
            if alt is not None:
                text = synonyme[alt] + text[len(alt):]
            return f(self, x, y, text, *a, **k)
        return neu
    for n, f in orig.items():
        setattr(canvas.Canvas, n, huelle(f))
    try:
        yield
    finally:
        for n, f in orig.items():
            setattr(canvas.Canvas, n, f)


@contextlib.contextmanager
def woerterbuch(aenderung):
    """Wörterbuch für die Dauer des Tests aus einer veränderten Kopie laden."""
    daten = json.loads(wb.DATEI.read_text(encoding="utf-8"))
    aenderung(daten)
    alt = wb.DATEI
    with tempfile.TemporaryDirectory() as t:
        wb.DATEI = Path(t) / "woerterbuch.json"
        wb.DATEI.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
        wb.neu_laden()
        try:
            yield
        finally:
            wb.DATEI = alt
            wb.neu_laden()


class Woerterbuch(unittest.TestCase):
    def test_datei_gueltig(self):
        def pruefe(knoten, pfad):
            if isinstance(knoten, dict):
                for k, v in knoten.items():
                    if not k.startswith("_"):
                        pruefe(v, pfad + [k])
                return
            self.assertIsInstance(knoten, list, "/".join(pfad))
            self.assertTrue(all(isinstance(s, str) and s.strip() for s in knoten), "/".join(pfad))
            if pfad[0] != "erkennung" or pfad[-1] == "eins_von":
                self.assertTrue(knoten, f"{'/'.join(pfad)} ist leer")
        pruefe(wb.laden(), [])

    def test_ab_mit_synonymen(self):
        with tempfile.TemporaryDirectory() as t:
            for i in range(10):
                with self.subTest(i=i):
                    pfad = Path(t) / f"ab_{i}.pdf"
                    with umbenannt(AB_SYNONYME):
                        soll = gen.erzeuge_ab(pfad, random.Random(500 + i))
                    a = ra.lese_beleg(pfad)
                    for k, v in soll.items():
                        if k == "positionen":
                            v2 = [(p.titel, p.produkt, p.einheit, p.menge, p.betrag) for p in a.positionen]
                            self.assertEqual(v2, v)
                        else:
                            self.assertEqual(getattr(a, k), v, k)

    def test_sap_mit_synonymen(self):
        with tempfile.TemporaryDirectory() as t:
            for i in range(10):
                druck = i % 2 == 1
                with self.subTest(i=i, druck=druck):
                    pfad = Path(t) / f"sap_{i}.pdf"
                    with umbenannt(DRUCK_SYNONYME if druck else KOPIE_SYNONYME):
                        soll = (sap.erzeuge_ariba_druck if druck else sap.erzeuge_ariba)(pfad, random.Random(700 + i))
                    a = ra.lese_beleg(pfad)
                    self.assertEqual((a.rechnungsnr, a.rechnungsdatum, a.leistung_von, a.leistung_bis),
                                     (soll["nr"], soll["rdatum"], soll["von"], soll["bis"]))
                    self.assertEqual((a.bestell_nr, a.bestell_datum, a.zahlungsziel_tage, a.ust_id_kunde),
                                     (soll["po"], soll["po_datum"], soll["tage"], soll["ust_id"]))
                    self.assertEqual((a.kunde, a.strasse, a.plz_ort), (soll["kunde"], soll["strasse"], soll["plz_ort"]))
                    self.assertEqual([p.titel for p in a.positionen], soll["titel"])
                    self.assertEqual([p.betrag for p in a.positionen], soll["betraege"])

    def test_neue_bezeichnung_nur_im_woerterbuch(self):
        """Unbekannte Beschriftung scheitert; ein Eintrag in woerterbuch.json genügt – ohne Codeänderung."""
        with tempfile.TemporaryDirectory() as t:
            pfad = Path(t) / "ab.pdf"
            with umbenannt({"Kunden-Nr.:": "Debitor:", "Zahlungsziel:": "Skontofrei binnen"}):
                soll = gen.erzeuge_ab(pfad, random.Random(42))
            with self.assertRaises(ra.AbFehler):
                ra.lese_beleg(pfad)

            def ergaenzen(d):
                d["ab"]["kopf"]["kunden_nr"].append("Debitor")
                d["ab"]["zahlungsziel"].append("Skontofrei binnen")
            with woerterbuch(ergaenzen):
                a = ra.lese_beleg(pfad)
            self.assertEqual((a.kunden_nr, a.zahlungsziel_tage), (soll["kunden_nr"], soll["zahlungsziel_tage"]))

    def test_spaltenkopf_nimmt_keinen_wert_aus_folgezeile(self):
        """'Summe (Netto)' ist Spaltenkopf der Tabelle – die Summe kommt trotzdem aus 'Summe Auftrag (Netto)'."""
        m = wb.suche("Produkte Einheit Menge Summe (Netto)\nWorkshop 1 Tag 1 95,00 €\n",
                     ("ab", "summe_netto"), r"([^\n]*\d[^\n]*)")
        self.assertIsNone(m)


class Review3(unittest.TestCase):
    """Funde aus dem dritten xhigh-Review (Wörterbuch-Umbau)."""

    def _ab(self, synonyme=None, genau=None, seed=42, **erzwinge):
        t = tempfile.mkdtemp()
        pfad = Path(t) / "ab.pdf"
        with umbenannt(synonyme or {}, genau):
            soll = gen.erzeuge_ab(pfad, random.Random(seed), erzwinge or None)
        return pfad, soll

    def test_titel_ohne_umlaut(self):
        pfad, soll = self._ab({"Auftragsbestätigung": "Auftragsbestatigung"})
        self.assertEqual(ra.lese_beleg(pfad).ab_nr, soll["ab_nr"])

    def test_liefertermin_synonym_im_projekt(self):
        pfad, soll = self._ab({"Projekt: ": "Projekt: Fertigstellung "}, mit_liefer=True, liefer_text=False)
        a = ra.lese_beleg(pfad)
        self.assertEqual(a.liefertermin, soll["liefertermin"])
        self.assertFalse([h for h in a.hinweise if "Liefertermin" in str(h)])

    def test_summe_ohne_leerzeichen_vor_klammer(self):
        pfad, soll = self._ab({"Summe Auftrag (Netto)": "Summe Auftrag(Netto)"})
        self.assertEqual(ra.lese_beleg(pfad).summe_netto, soll["summe_netto"])

    def test_summenwort_in_position_ist_nicht_die_summe(self):
        pfad, soll = self._ab(genau={p: "Nettosumme 1.000 € monatlich" for p, _ in gen.PRODUKTE})
        a = ra.lese_beleg(pfad)
        self.assertEqual(a.summe_netto, soll["summe_netto"])
        self.assertEqual(len(a.positionen), len(soll["positionen"]))

    def test_kopffeld_ohne_doppelpunkt(self):
        pfad, soll = self._ab({"Kunden-Nr.:": "Kundennummer"})
        self.assertEqual(ra.lese_beleg(pfad).kunden_nr, soll["kunden_nr"])

    def test_bestellnummer_nicht_aus_laengerem_wort(self):
        nr, datum = ra.lies_bestellung("Kundenbestellnummer: 999 vom 01.01.2025\nIhre Bestellung 4711 vom 02.02.2025", [])
        self.assertEqual((nr, datum), ("4711", dt.date(2025, 2, 2)))
        self.assertIsNone(wb.suche("Teilfertigstellung: 01.01.2026", ("ab", "liefertermin"), r"(\S+)"))

    def test_zahlungsziel_in_naechster_zeile(self):
        m = wb.suche("Zahlungsziel:\n14 Tage netto", ("ab", "zahlungsziel"), r"[^\n\d]{0,30}?(\d+)\s*Tag",
                     naechste_zeile=True)
        self.assertEqual(m.group(1), "14")

    def test_sap_menge_mit_nachkommastellen(self):
        with tempfile.TemporaryDirectory() as t:
            for druck in (False, True):
                with self.subTest(druck=druck):
                    pfad = Path(t) / f"sap_{druck}.pdf"
                    with umbenannt({}, {"1 / (1)": "1,50 / (Std.)"}):
                        soll = (sap.erzeuge_ariba_druck if druck else sap.erzeuge_ariba)(pfad, random.Random(3))
                    a = ra.lese_beleg(pfad)
                    self.assertEqual([p.menge for p in a.positionen], ["1,50"] * len(soll["titel"]))
                    self.assertEqual([p.betrag for p in a.positionen], soll["betraege"])

    def test_sap_anschrift_ohne_rechte_spalte_bricht_ab(self):
        with tempfile.TemporaryDirectory() as t:
            pfad = Path(t) / "sap.pdf"
            with umbenannt({}, {"LIEFERANT:": "VERKÄUFER:"}):
                sap.erzeuge_ariba_druck(pfad, random.Random(5))
            with self.assertRaisesRegex(ra.AbFehler, "rechts neben 'Rechnungsanschrift'"):
                ra.lese_beleg(pfad)

    def test_details_nur_als_eigene_zeile(self):
        self.assertTrue(wb.ist_zeile("DETAILS", "ariba", "position_details"))
        self.assertTrue(wb.ist_zeile("Servicedetails:", "ariba", "position_details"))
        self.assertFalse(wb.ist_zeile("Details siehe Angebot Nummer 4711", "ariba", "position_details"))

    def test_kaputtes_woerterbuch_klare_meldung(self):
        alt = wb.DATEI
        with tempfile.TemporaryDirectory() as t:
            wb.DATEI = Path(t) / "woerterbuch.json"
            wb.DATEI.write_text('{"ab": {"titel": ["Auftragsbestätigung",]}}', encoding="utf-8")
            wb.neu_laden()
            try:
                with self.assertRaisesRegex(wb.WoerterbuchFehler, "Zeile 1"):
                    wb.liste("ab", "titel")
            finally:
                wb.DATEI = alt
                wb.neu_laden()


if __name__ == "__main__":
    unittest.main()
