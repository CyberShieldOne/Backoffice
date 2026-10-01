#!/usr/bin/env python3
"""Zuverlässigkeitstest für rechnung_aus_ab.py.

Je Iteration werden mehrere synthetische Auftragsbestätigungen im Layout der
echten CS-AB erzeugt (zufällige Kunden, Adressen, Datumsformate, Beträge,
1–3 Positionen, umbrechende Felder), durch das Skript geschickt und geprüft:
  * ausgelesene Felder == Sollwerte
  * keine Platzhalter mehr im DOCX, XML wohlgeformt
  * Sollwerte (Kunde, Nummern, Beträge, Daten) stehen im Dokumenttext
  * optional: LibreOffice öffnet das DOCX (PDF-Export) und der PDF-Text enthält den Bruttobetrag

Aufruf:
    python tests/test_rechnung.py --iterationen 10 --je 6 [--echt AB.pdf ...] [--pdf]
Abhängigkeiten: pdfplumber, reportlab
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import shutil
import subprocess
import sys
import tempfile
import traceback
import xml.dom.minidom
import zipfile
from decimal import Decimal
from pathlib import Path

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER.parent))
import rechnung_aus_ab as ra  # noqa: E402

from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

MON_KURZ = ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."]
MON_LANG = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
            "Oktober", "November", "Dezember"]

KUNDEN = ["OHB SE", "Müller & Söhne GmbH & Co. KG", "Stadtwerke Gelnhausen GmbH", "ACME AG",
          "Bäckerei Groß e.K.", "Fraunhofer-Gesellschaft zur Förderung der angewandten Forschung e.V.",
          "Kreisklinik Main-Kinzig gGmbH", "Ölmühle Süd GmbH"]
STRASSEN = ["Manfred-Fuchs-Platz 2-4", "Hauptstraße 1", "Am Mühlzaun 12a", "Lange Gasse 117",
            "Industriepark Höchst, Gebäude C 660", "Zum Wartturm 5"]
ORTE = ["28359 Bremen", "63571 Gelnhausen", "60311 Frankfurt am Main", "80331 München",
        "01067 Dresden", "65929 Frankfurt-Höchst"]
KONTAKTE = ["Jochen Zurborg", "Dr. Anna-Lena Weiß", "Max Mustermann", "Prof. Dr. Jürgen Özdemir"]
PROJEKTE = ["Audit & Validierung von MS Entra ID Conditional Access",
            "Interner Angriffspfad-Test",
            "Microsoft 365 Härtung inkl. Defender for Office und Exchange Online Protection für alle Standorte",
            "MFA-Audit"]
PRODUKTE = [("Cyber Shield Consulting (Off-Site) @ EUR 95/h", "Audit & Validierung von MS Entra ID Conditional Access"),
            ("Cyber Shield Consulting (On-Site) @ EUR 120/h", "Workshop Zero Trust"),
            ("Pentest Paket STANDARD", "Interner Angriffspfad-Test ab kompromittiertem Zugang"),
            ("Lizenz", "Monitoring-Agent 12 Monate")]
EINHEITEN = [["Entra ID", "Tenant Größe", "M (bis 5.000", "Accounts)"], ["Std."], ["Pauschale"],
             ["Tenant"], ["Lizenz /", "Jahr"]]
BESCHR = [
    ["Entspricht dem CS Cyber Shield GmbH Dokument \"Audit und Validierungsplan für Conditional",
     "Access in einer hybrid angebundenen Microsoft Entra ID-Umgebung\", welches auf vier Entra ID",
     "Umgebungen ausgelegt ist."],
    ["In diesem vorliegenden Angebot jedoch angepasst auf 5 Entra ID Umgebungen (Tenants), anstelle",
     "auf 4."],
    ["Der hier angebotene Preis enthält maximal 48 Stunden Consulting."],
    ["Drei Phasen: Scoping, Attack-Path-Validierung, Bericht & Roadmap."],
]


def fmt_ab_betrag(b: Decimal, stil: int) -> str:
    s = ra.fmt_betrag(b).replace(" €", "")
    if stil == 0 and s.endswith(",00"):
        return "EUR " + s[:-3] + ",-"
    if stil == 1:
        return "EUR " + s
    if stil == 3:  # ohne Tausenderpunkt
        return "EUR " + s.replace(".", "")
    if stil == 4 and s.endswith(",00"):  # ohne Tausenderpunkt, ",-"
        return "EUR " + s.replace(".", "")[:-3] + ",-"
    return s + " €"


def fmt_ab_datum(d: dt.date, stil: int) -> str:
    if stil == 0:
        return f"{d.day:02d}. {MON_KURZ[d.month - 1]} {d.year}"
    if stil == 1:
        return f"{d.day}. {MON_LANG[d.month - 1]} {d.year}"
    return d.strftime("%d.%m.%Y")


def _umbrechen(text: str, n: int) -> list[str]:
    zeilen, z = [], ""
    for w in text.split():
        if z and len(z) + 1 + len(w) > n:
            zeilen.append(z)
            z = w
        else:
            z = f"{z} {w}".strip()
    return zeilen + [z]


def erzeuge_ab(pfad: Path, rnd: random.Random, erzwinge: dict | None = None) -> dict:
    """Synthetische AB im Layout der echten CS-AB (Koordinaten aus AB-2025-10-29-2).
    erzwinge: feste Werte für einzelne Zufallsgrößen (mit_liefer, liefer_text, betrag_stil)."""
    erzwinge = erzwinge or {}
    ab_datum = dt.date(2025, 1, 1) + dt.timedelta(days=rnd.randint(0, 600))  # vor dem Test-Rechnungsdatum
    bestell_datum = ab_datum - dt.timedelta(days=rnd.randint(0, 5))
    liefer = ab_datum + dt.timedelta(days=rnd.randint(1, 60))
    mit_bestellung = rnd.random() < 0.8
    mit_liefer = erzwinge.get('mit_liefer', rnd.random() < 0.85)
    # "Liefertermin: nach Vereinbarung" statt Datum
    liefer_text = erzwinge.get('liefer_text', rnd.random() < 0.25)
    kunde = rnd.choice(KUNDEN)
    strasse, ort = rnd.choice(STRASSEN), rnd.choice(ORTE)
    komma = rnd.random() < 0.7
    kontakt = rnd.choice(KONTAKTE)
    projekt = rnd.choice(PROJEKTE)
    ab_nr = f"AB-{ab_datum:%Y-%m-%d}-{rnd.randint(1, 9)}"
    kunden_nr = f"CS-{rnd.randint(1, 12):02d}-{ab_datum.year}-{rnd.randint(1, 99):02d}"
    bestell_nr = str(rnd.randint(10_000_000, 99_999_999))
    ziel = rnd.choice([7, 10, 14, 30])
    betrag_stil = erzwinge.get('betrag_stil', rnd.randint(0, 4))
    datum_stil = rnd.randint(0, 2)

    pos = []
    for _ in range(rnd.choice([1, 1, 1, 2, 3])):
        produkt, titel = rnd.choice(PRODUKTE)
        cent = rnd.choice([0, 0, 0, rnd.randint(1, 99)])
        betrag = Decimal(rnd.randint(1, 250) * 100 + rnd.choice([0, 50])) + Decimal(cent) / 100
        if betrag_stil in (0, 4):
            betrag = betrag.quantize(Decimal("1"))  # Stil ",-" nur ganze Euro
        pos.append(dict(produkt=produkt, titel=titel, einheit=rnd.choice(EINHEITEN),
                        menge=str(rnd.randint(1, 48)), betrag=betrag,
                        beschr=rnd.sample(BESCHR, rnd.randint(1, 3))))
    summe = sum((p["betrag"] for p in pos), Decimal("0"))

    c = canvas.Canvas(str(pfad), pagesize=A4)
    H = A4[1]

    def txt(x, top, s, size=9.1, font="Helvetica"):
        c.setFont(font, size)
        c.drawString(x, H - top - size * 0.8, s)

    txt(331, 124, f"Datum: {fmt_ab_datum(ab_datum, datum_stil)}")
    for i, s in enumerate(["CS Cyber Shield GmbH", "Zum Wartturm 5", "63571 Gelnhausen", "",
                           "Ihr Ansprechpartner", "Markus Schmitt", "+49 174 260 1741", "msc@cyber-shield.org"]):
        txt(71, 129 + i * 10.5, s)
    rechts = [f"Kunde: {kunde}"]
    rechts += _umbrechen(f"Anschrift: {strasse}{',' if komma else ''} {ort}", 45)
    rechts += [f"Kunden-Nr.: {kunden_nr}", f"Kundenkontakt: {kontakt}",
               "Kundenmail: kontakt@example.de", "Rechnungsempf: rechnung@example.de"]
    rechts += _umbrechen(f"Projekt: {projekt}", 45)
    top = 151
    for s in rechts:
        txt(331, top, s)
        top += 12
    titel_top = max(top + 8, 265)
    txt(71, titel_top, f"Auftragsbestätigung {ab_nr}", 12, "Helvetica-Bold")
    top = titel_top + 38
    if mit_bestellung:
        txt(71, top, f"Ihre Bestellung {bestell_nr} per Email vom: {bestell_datum:%d.%m.%Y}")
    top += 12
    if mit_liefer:
        txt(71, top, "Liefertermin: " + ("nach Vereinbarung" if liefer_text else fmt_ab_datum(liefer, datum_stil)))
    top += 53

    def fuss():
        txt(71, 785, "CS Cyber Shield GmbH        HRB 99474        Kreissparkasse Gelnhausen", 7)
        txt(71, 793, "Steuer ID: 019 230 40030    Amtsgericht Hanau    IBAN: DE67 5075 0094 0000 0954 31", 7)

    def platz(top, bedarf):
        """Seitenumbruch, wenn der nächste Block nicht mehr über die Fußzeile passt."""
        if top + bedarf > 760:
            fuss()
            c.showPage()
            return 70.0
        return top

    fs = 6.5
    txt(76.3, top, "Produkte", fs)
    txt(399.9, top, "Einheit", fs)
    txt(453.8, top, "Menge", fs)
    txt(501.0, top, "Summe (Netto)", fs)
    top += 9.4
    for p in pos:
        top = platz(top, 50)
        betrag_s = fmt_ab_betrag(p["betrag"], betrag_stil)
        c.setFont("Helvetica", fs)
        txt(76.3, top, p["produkt"], fs)
        txt(462.3, top, p["menge"], fs)
        c.drawRightString(547.3, H - top - fs * 0.8, betrag_s)
        links = [""] + _umbrechen(p["titel"], 70)
        n = max(len(links), len(p["einheit"]))
        for j in range(n):
            if j < len(p["einheit"]):
                c.drawCentredString(411, H - top - fs * 0.8, p["einheit"][j])
            if 0 < j < len(links):
                txt(76.3, top, links[j], fs)
            top += 8.9
        top += 0.5
        txt(76.3, top, "Leistungsbeschreibung:", fs)
        top += 8.9
        for k, absatz in enumerate(p["beschr"]):
            if k:
                top += 8.9  # Leerzeile zwischen Absätzen
            for z in absatz:
                top = platz(top, 9)
                txt(76.3, top, z, fs)
                top += 8.9
        top += 18
    top += 35
    top = platz(top, 90)
    txt(106.2, top, "Summe Auftrag (Netto)", 12)
    txt(247.8, top, ":", 12)
    txt(460.2, top, fmt_ab_betrag(summe, betrag_stil), 12)
    top += 59
    txt(75, top, "Kaufpreisforderung Preise: Netto, zzgl. gesetzlicher MwSt./gelten nur für Komplettabnahme")
    txt(75, top + 10, f"Zahlungsziel: {ziel} Tage ohne Abzug")
    fuss()
    c.save()

    return dict(ab_nr=ab_nr, ab_datum=ab_datum, kunde=kunde, strasse=strasse, plz_ort=ort,
                kunden_nr=kunden_nr, kontakt=kontakt, projekt=projekt, summe_netto=summe,
                zahlungsziel_tage=ziel, bestell_nr=bestell_nr if mit_bestellung else "",
                bestell_datum=bestell_datum if mit_bestellung else None,
                liefertermin=liefer if mit_liefer and not liefer_text else None,
                positionen=[(p["titel"], p["produkt"], " ".join(p["einheit"]), p["menge"], p["betrag"])
                            for p in pos])


def dokument_text(docx: Path) -> str:
    with zipfile.ZipFile(docx) as z:
        teile = []
        for n in z.namelist():
            daten = z.read(n)
            if n.endswith(".xml") or n.endswith(".rels"):
                xml.dom.minidom.parseString(daten)  # wohlgeformt?
            if n.startswith("word/") and n.endswith(".xml"):
                teile += ra.absatz_texte(daten.decode("utf-8"))
        ct = z.read("[Content_Types].xml").decode()
        assert "document.main+xml" in ct and "template.main+xml" not in ct, "Content-Type nicht DOCX"
    return "\n".join(teile)


def pruefe_fall(ab: Path, soll: dict | None, arbeitsdir: Path, nr: str, pdf: bool) -> list[str]:
    fehler = []
    datum = dt.date(2026, 10, 1)
    erg = ra.erstelle_rechnung(ab, nr, datum=datum, ausgabe=arbeitsdir / f"{nr}.docx", pdf=pdf)
    ziel, r = erg.docx, erg.rechnung
    if pdf and erg.pdf is None:
        fehler.append(f"PDF fehlt: {[str(h) for h in erg.hinweise]}")
    a = r.auftrag
    if soll:
        for k, v in soll.items():
            if k == "positionen":
                ist = [(p.titel, p.produkt, p.einheit, p.menge, p.betrag) for p in a.positionen]
                if ist != v:
                    fehler.append(f"positionen: ist {ist} soll {v}")
            elif getattr(a, k) != v:
                fehler.append(f"{k}: ist {getattr(a, k)!r} soll {v!r}")
    text = dokument_text(ziel)
    rest = ra.PLATZHALTER_RX.findall(text)
    if rest:
        fehler.append(f"Platzhalter übrig: {rest}")
    erwartet = [nr, a.ab_nr, a.kunde, a.kontakt, a.strasse, a.plz_ort, a.kunden_nr,
                ra.fmt_betrag(r.netto), ra.fmt_betrag(r.ust), ra.fmt_betrag(r.brutto),
                ra.fmt_datum(r.datum), ra.fmt_datum(r.faellig), r.zeitraum,
                f"{a.zahlungsziel_tage} Tage netto"] + [p.titel for p in a.positionen]
    if a.bestell_nr:  # Bestellnummer des Kunden: Infoblock, Anschreiben, Verwendungszweck
        erwartet += [f"Ihre Bestellung  {r.bestellung}", f"gemäß Ihrer Bestellung {r.bestellung} und",
                     f"Verwendungszweck: Rechnung {nr} / Bestell-Nr. {a.bestell_nr}",
                     f"Kostenstelle: {a.bestell_nr}"]
    elif "Ihre Bestellung" in text:
        fehler.append("Bestell-Zeile trotz fehlender Bestellnummer")
    for s in erwartet:
        if s not in text:
            fehler.append(f"fehlt im Dokument: {s!r}")
    if text.count(ra.fmt_betrag(r.brutto)) < 1 or r.brutto != r.netto + r.ust:
        fehler.append("Bruttobetrag inkonsistent")
    if r.ust != (r.netto * Decimal("0.19")).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP"):
        fehler.append("USt falsch")
    if "Interner Angriffspfad-Test ab kompromittiertem Zugang. Drei Phasen" in text:
        fehler.append("Beispielbeschreibung der Vorlage nicht ersetzt")
    if pdf:
        pdf_txt = subprocess.run(["pdftotext", "-layout", str(ziel.with_suffix(".pdf")), "-"],
                                 capture_output=True, text=True).stdout
        if ra.fmt_betrag(r.brutto) not in pdf_txt:
            fehler.append("Bruttobetrag nicht (einzeilig) im gerenderten PDF")
    return fehler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterationen", type=int, default=10)
    ap.add_argument("--je", type=int, default=6, help="synthetische ABs je Iteration")
    ap.add_argument("--echt", type=Path, nargs="*", default=[], help="echte AB-PDFs (ohne Sollwerte)")
    ap.add_argument("--pdf", action="store_true", help="zusätzlich mit LibreOffice rendern")
    args = ap.parse_args()
    if args.pdf and not (shutil.which("soffice") and shutil.which("pdftotext")):
        print("--pdf braucht soffice und pdftotext")
        return 2

    gesamt_ok = gesamt = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for it in range(1, args.iterationen + 1):
            rnd = random.Random(it)
            ok = n = 0
            for echt in args.echt:
                n += 1
                try:
                    f = pruefe_fall(echt, None, tmp, f"2026-{it:02d}00", args.pdf)
                except Exception as e:  # noqa: BLE001
                    f = [f"Ausnahme: {e!r}", traceback.format_exc()]
                ok += not f
                for x in f:
                    print(f"  [it {it}] {echt.name}: {x}")
            for j in range(args.je):
                n += 1
                ab = tmp / f"ab_{it}_{j}.pdf"
                try:
                    soll = erzeuge_ab(ab, rnd)
                    f = pruefe_fall(ab, soll, tmp, f"2026-{it:02d}{j + 1:02d}", args.pdf)
                except Exception as e:  # noqa: BLE001
                    f = [f"Ausnahme: {e!r}", traceback.format_exc()]
                ok += not f
                for x in f:
                    print(f"  [it {it}] {ab.name}: {x}")
            print(f"Iteration {it:2d}: {ok}/{n} ok")
            gesamt_ok += ok
            gesamt += n
    print(f"GESAMT: {gesamt_ok}/{gesamt} ok")
    return 0 if gesamt_ok == gesamt else 1


if __name__ == "__main__":
    sys.exit(main())
