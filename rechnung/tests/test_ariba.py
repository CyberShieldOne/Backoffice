#!/usr/bin/env python3
"""Tests für SAP-Ariba-Rechnungen als Eingabe (synthetische Belege im Layout von RE-2026-09-30-05).

Aufruf: python -m unittest tests/test_ariba.py   (aus dem Ordner rechnung/)
"""
from __future__ import annotations

import datetime as dt
import random
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER.parent))
sys.path.insert(0, str(HIER))
import rechnung_aus_ab as ra  # noqa: E402
import test_rechnung as gen  # noqa: E402

from reportlab.pdfgen import canvas  # noqa: E402

SEITE = (748.8, 1159.2)
MON = ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."]
KUNDEN = [("Infineon Technologies AG", "Am Campeon 1-15", "Neubiberg", "85579", "09 – Bavaria"),
          ("Beispiel Halbleiter GmbH", "Industriestraße 12", "Dresden", "01109", "14 – Saxony"),
          ("Müller & Söhne AG", "Königsallee 1a", "Düsseldorf", "40212", "")]
LEISTUNGEN = [
    ("Consulting Services for Redmimicry POC",
     ["Includes required Redmimicry Licenses for POC", "Includes Vendor support",
      "Includes Cyber Shield support", "Customer needs to provide VMs; 8 (Monate) x 5600 EUR"]),
    ("Entra ID Conditional Access Audit", ["Scoping", "Validierung", "Bericht und Roadmap"]),
    ("Managed Detection Monatspauschale", ["24/7 Monitoring", "Incident Triage"]),
]


def de(d: dt.date) -> str:
    return f"{d.day}. {MON[d.month - 1]} {d.year}"


def umbrechen(text: str, n: int = 34) -> list[str]:
    return gen._umbrechen(text, n)


def erzeuge_ariba(pfad: Path, rnd: random.Random) -> dict:
    kunde = rnd.choice(KUNDEN)
    rdatum = dt.date(2026, 1, 1) + dt.timedelta(days=rnd.randint(0, 270))
    von = rdatum.replace(day=1)
    bis = rdatum
    po_datum = von - dt.timedelta(days=rnd.randint(10, 200))
    nr = f"RE-{rdatum:%Y-%m-%d}-{rnd.randint(1, 20):02d}"
    po = str(rnd.randint(4500000000, 4599999999))
    tage = rnd.choice([30, 45, 60, 90])
    ust_id = f"DE{rnd.randint(100000000, 999999999)}"
    pos = []
    for i in range(rnd.choice([1, 1, 2, 3])):
        titel, teile = rnd.choice(LEISTUNGEN)
        betrag = Decimal(rnd.randint(5, 300) * 100) + Decimal(rnd.choice([0, 0, 50, 99])) / 100
        pos.append((titel, rnd.sample(teile, rnd.randint(1, len(teile))), betrag))
    netto = sum((p[2] for p in pos), Decimal("0"))
    steuer = ra.ust_von(netto)
    eur = lambda b: ra.fmt_betrag(b).replace(" €", " EUR")  # noqa: E731

    c = canvas.Canvas(str(pfad), pagesize=SEITE)
    H = SEITE[1]
    y = {"wert": 0}

    def t(x, top, s, size=7.5, rechts=False):
        c.setFont("Helvetica", size)
        if rechts:
            c.drawRightString(x, H - top - size * 0.8, s)
        else:
            c.drawString(x, H - top - size * 0.8, s)

    t(90, 70, "Standardrechnung", 12)
    t(88, 90, "Die ursprüngliche Steuerrechnung ist elektronisch eingereicht")
    t(374, 90, "Rechnungsnummer")
    t(652, 90, nr, rechts=True)
    t(374, 100, "(Lieferantenreferenznr.)")
    t(374, 118, "Rechnungsdatum")
    t(652, 118, f"Mittwoch, {de(rdatum)}, 9:31 Uhr GMT+02:00", rechts=True)
    t(374, 137, "Service-Startdatum")
    t(652, 137, de(von), rechts=True)
    t(374, 156, "Service-Enddatum")
    t(652, 156, de(bis), rechts=True)
    t(374, 175, "Fälliger Betrag")
    t(652, 175, eur(netto + steuer), rechts=True)
    t(103, 216, "Lieferant")
    t(233, 216, "Kunde")
    for i, s in enumerate(["CS Cyber Shield GmbH", "Am Mühlzaun 1", "Gelnhausen", "Hesse", "63571", "Germany"]):
        t(103, 227 + i * 9, s)
    for i, s in enumerate([kunde[0], kunde[1], kunde[2], kunde[3], "Germany"]):
        t(233, 227 + i * 9, s)
    t(103, 288, "Umsatzsteuer-/Steuernummer")
    t(233, 288, "Umsatzsteuer-/Steuernummer")
    t(103, 305, "DE363562591")
    t(233, 305, ust_id)
    t(93, 345, "Positionen", 9)
    t(330, 345, f"Bestellauftragsnr. : {po}")
    t(500, 345, f"Bestellauftragsdatum: {de(po_datum)}")
    for x, s in [(92.8, "Positionsnr."), (138.1, "Nr. der"), (223, "Art"), (257, "Teilenr. des Lieferanten/"),
                 (376, "Teilenr. des Kunden"), (472.3, "Menge"), (556.2, "Preis pro"), (599.7, "Zwischensumme")]:
        t(x, 358, s)
    t(138.1, 367, "Bestellauftragsposition")
    t(257, 367, "Beschreibung")
    top = 380.0
    for i, (titel, teile, betrag) in enumerate(pos, 1):
        beschr = umbrechen(f"Not Available / Cyber Shield – {titel} - " + " - ".join(teile))
        t(92.8, top, str(i))
        t(138.1, top, str(i * 2))
        t(223, top, "Service")
        t(472.3, top, "1 / (1)")
        t(584.2, top, eur(betrag), rechts=True)
        t(652.2, top, eur(betrag), rechts=True)
        for j, z in enumerate(beschr):
            t(257, top + j * 8.5, z)
        top += len(beschr) * 8.5 + 6
        t(127, top, "Servicedetails:")
        t(127, top + 10, f"Service Period : {de(von)} : {de(bis)}")
        t(127, top + 22, "Steuern, Ermäßigungen und Zuschläge")
        t(132, top + 35, "Kategorie Gilt für Details Grundbetrag Satz (%) Betrag")
        t(132, top + 47, f"Umsatzsteuer Position Lieferdatum : {de(von)} {eur(betrag)} 19 % {eur(ra.ust_von(betrag))}")
        top += 75
    t(109, top, "Steuerübersicht")
    t(109, top + 13, "Kategorie Details Grundbetrag Satz (%) Betrag")
    t(109, top + 26, f"Umsatzsteuer {eur(netto)} 19 % {eur(steuer)}")
    top += 46
    for lab, wert in [("Zwischensumme der Position", eur(netto)), ("Gesamtbetrag ohne Steuern", eur(netto)),
                      ("Gesamtbetrag der Steuern", eur(steuer)), ("Fälliger Betrag", eur(netto + steuer))]:
        t(435, top, lab)
        t(652, top, wert, rechts=True)
        top += 14
    top += 20
    t(103, top, "Abrechnungsinformationen")
    t(103, top + 13, "Rechnungsabsender")
    t(233, top + 13, "Rechnungsanschrift")
    t(416.4, top + 13, "Zahlungsempfänger")
    links = ["CS Cyber Shield GmbH", "Am Mühlzaun 1", "Gelnhausen", "Hesse", "63571", "Germany"]
    mitte = [kunde[0], kunde[1], kunde[2]] + ([kunde[4]] if kunde[4] else []) + [kunde[3], "Germany", "Adressen-ID: 1000"]
    for i in range(max(len(links), len(mitte))):
        if i < len(links):
            t(103, top + 25 + i * 9, links[i])
            t(416.4, top + 25 + i * 9, links[i])
        if i < len(mitte):
            t(233, top + 25 + i * 9, mitte[i])
    top += 25 + 9 * max(len(links), len(mitte)) + 15
    t(94, top, "Zahlungsbedingungen")
    t(94, top + 12, "Nettozahlungsbedingungen (Tage) :")
    t(94, top + 23, str(tage))
    t(363, 1136, "Page 1")
    c.save()
    y["wert"] = top
    return dict(nr=nr, rdatum=rdatum, von=von, bis=bis, po=po, po_datum=po_datum, tage=tage, ust_id=ust_id,
                kunde=kunde[0], strasse=kunde[1], plz_ort=f"{kunde[3]} {kunde[2]}", netto=netto,
                titel=[p[0] for p in pos], betraege=[p[2] for p in pos])


class Ariba(unittest.TestCase):
    def test_zehn_iterationen(self):
        """10 Iterationen × 5 synthetische SAP-Rechnungen: lesen, Rechnung erzeugen, prüfen."""
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            for it in range(1, 11):
                rnd = random.Random(1000 + it)
                for j in range(5):
                    with self.subTest(iteration=it, beleg=j):
                        pfad = t / f"sap_{it}_{j}.pdf"
                        soll = erzeuge_ariba(pfad, rnd)
                        a = ra.lese_beleg(pfad)
                        self.assertEqual(a.quelle, "SAP Ariba")
                        self.assertEqual((a.rechnungsnr, a.rechnungsdatum, a.leistung_von, a.leistung_bis),
                                         (soll["nr"], soll["rdatum"], soll["von"], soll["bis"]))
                        self.assertEqual((a.bestell_nr, a.bestell_datum, a.zahlungsziel_tage, a.ust_id_kunde),
                                         (soll["po"], soll["po_datum"], soll["tage"], soll["ust_id"]))
                        self.assertEqual((a.kunde, a.strasse, a.plz_ort), (soll["kunde"], soll["strasse"], soll["plz_ort"]))
                        self.assertEqual(a.summe_netto, soll["netto"])
                        self.assertEqual([p.titel for p in a.positionen], soll["titel"])
                        self.assertEqual([p.betrag for p in a.positionen], soll["betraege"])

                        erg = ra.erstelle_rechnung(pfad, ausgabe=t / f"r_{it}_{j}.docx")
                        text = gen.dokument_text(erg.docx)
                        self.assertEqual(ra.PLATZHALTER_RX.findall(text), [])
                        self.assertIn(f"Rechnungsnummer  {soll['nr']}", text)
                        self.assertIn(f"Ihre Bestellung  {soll['po']} vom", text)
                        self.assertIn(f"USt-IdNr. des Empfängers: {soll['ust_id']}", text)
                        self.assertIn("elektronisch über SAP Ariba übermittelt", text)
                        self.assertIn(ra.fmt_betrag(soll["netto"] + ra.ust_von(soll["netto"])), text)
                        self.assertNotIn("Auftragsbestätigung", text)
                        self.assertNotIn("Roadmap wurden am", text)
                        self.assertNotIn("Kundennummer", text)
                        self.assertNotIn("z. Hd.", text)
                        self.assertEqual(erg.rechnung.faellig, soll["rdatum"] + dt.timedelta(days=soll["tage"]))

    def test_ab_weiterhin_mit_bericht_und_ab(self):
        """Gegenprobe: eine AB erzeugt weiterhin Auftragsbestätigungs- und Bericht/Roadmap-Sätze."""
        with tempfile.TemporaryDirectory() as t:
            pfad = Path(t) / "ab.pdf"
            gen.erzeuge_ab(pfad, random.Random(5))
            erg = ra.erstelle_rechnung(pfad, "2026-0001", datum=dt.date(2026, 10, 1), ausgabe=Path(t) / "r.docx")
            text = gen.dokument_text(erg.docx)
            self.assertIn("Auftragsbestätigung  AB-", text)
            self.assertIn("Roadmap wurden am", text)
            self.assertNotIn("SAP Ariba", text)

    def test_unbekannter_beleg(self):
        with tempfile.TemporaryDirectory() as t:
            pfad = Path(t) / "x.pdf"
            c = canvas.Canvas(str(pfad))
            c.drawString(100, 700, "Lieferschein 4711")
            c.save()
            with self.assertRaises(ra.AbFehler) as k:
                ra.lese_beleg(pfad)
            self.assertIn("Unbekannter Beleg", str(k.exception))


if __name__ == "__main__":
    unittest.main()
