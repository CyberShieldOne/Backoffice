#!/usr/bin/env python3
"""Rechnung aus Auftragsbestätigung erzeugen.

Liest eine Auftragsbestätigung (PDF, CS-Cyber-Shield-Layout) aus und füllt
die Platzhalter der Word-Vorlage 2026-OKT_CS-Rechnung_Vorlage.dotx.

Aufruf:
    python rechnung_aus_ab.py AB.pdf --rechnungsnr 2026-0142
    python rechnung_aus_ab.py AB.pdf --rechnungsnr 2026-0142 --datum 01.10.2026 --pdf

Abhängigkeit: pip install pdfplumber
Optional für --pdf: LibreOffice (soffice) im PATH.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

# Als Skript gestartet heißt das Modul "__main__"; quelle_ariba importiert "rechnung_aus_ab" –
# ohne diese Zeile gäbe es zwei Modulkopien und zwei verschiedene AbFehler-Klassen.
sys.modules.setdefault("rechnung_aus_ab", sys.modules[__name__])

VORLAGE = Path(__file__).resolve().parent / "vorlagen" / "2026-OKT_CS-Rechnung_Vorlage.dotx"
UST_SATZ = Decimal("19")
FUSS_AB = 0.92  # Anteil der Seitenhöhe, ab dem die AB-Fußzeile beginnt

MONATE = {
    "jan": 1, "januar": 1, "jän": 1, "jänner": 1,
    "feb": 2, "februar": 2,
    "mär": 3, "märz": 3, "mrz": 3, "maerz": 3,
    "apr": 4, "april": 4,
    "mai": 5,
    "jun": 6, "juni": 6,
    "jul": 7, "juli": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "okt": 10, "oktober": 10,
    "nov": 11, "november": 11,
    "dez": 12, "dezember": 12,
}


class AbFehler(Exception):
    """Pflichtangabe in der Auftragsbestätigung nicht gefunden/inkonsistent."""


class DateiExistiert(AbFehler):
    """Zieldatei existiert. fremd=True: sie gehört zu einer anderen Rechnung (nie überschreiben)."""

    def __init__(self, text: str, fremd: bool):
        super().__init__(text)
        self.fremd = fremd


# --------------------------------------------------------------------------
# Hilfsfunktionen Datum / Betrag
# --------------------------------------------------------------------------

def parse_datum(text: str) -> dt.date:
    try:
        return _parse_datum(text)
    except ValueError:  # z. B. 31.09.2026
        raise AbFehler(f"Kein gültiges Datum: {text.strip()!r}")


def _parse_datum(text: str) -> dt.date:
    t = text.strip()
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})\b", t)
    if m:
        j = int(m.group(3))
        return dt.date(j + 2000 if j < 100 else j, int(m.group(2)), int(m.group(1)))
    m = re.search(r"(\d{1,2})\.?\s+([A-Za-zÄÖÜäöü]+)\.?\s+(\d{4})", t)
    if m:
        mon = MONATE.get(m.group(2).lower())
        if mon:
            return dt.date(int(m.group(3)), mon, int(m.group(1)))
    raise AbFehler(f"Datum nicht lesbar: {text!r}")


# Rest einer Summenzeile nach der Bezeichnung: nur ein Betrag (für parse_betrag), sonst nichts
SUMMENZEILE = (r"((?:EUR|€)?[ \t]*\d[\d. \t\u00a0\u202f\u2009]*(?:,(?:\d{1,2}|-{1,2}|–))?[ \t]*(?:€|EUR)?)[ \t]*$")


def parse_betrag(text: str) -> Decimal:
    """'EUR 4.500,-' / '4500,00 €' / 'EUR 12.345,67' / '4 500,00 €' -> Decimal."""
    # Leerzeichen als Tausendertrenner (DIN 5008, auch U+00A0/U+202F/U+2009) entfernen
    text = re.sub(r"(?<=\d)[ \u00a0\u202f\u2009](?=\d{3}(?!\d))", "", text)
    # mit Tausenderpunkten (mind. eine Gruppe) ODER ganz ohne – nie nach 3 Ziffern abschneiden
    m = re.search(r"(\d{1,3}(?:\.\d{3})+(?!\d)|\d+)(?:,(\d{1,2}|-{1,2}|–))?", text)
    if not m:
        raise AbFehler(f"Betrag nicht lesbar: {text!r}")
    ganz = m.group(1).replace(".", "")
    cent = m.group(2) or "00"
    if not cent.isdigit():
        cent = "00"
    return Decimal(f"{ganz}.{cent.ljust(2, '0')}")


def fmt_betrag(b: Decimal) -> str:
    b = b.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    ganz, cent = f"{b:.2f}".split(".")
    neg = ganz.startswith("-")
    ganz = ganz.lstrip("-")
    gruppen = []
    while len(ganz) > 3:
        gruppen.insert(0, ganz[-3:])
        ganz = ganz[:-3]
    gruppen.insert(0, ganz)
    return f"{'-' if neg else ''}{'.'.join(gruppen)},{cent} €"


def ust_von(netto: Decimal) -> Decimal:
    return (netto * UST_SATZ / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_datum(d: dt.date) -> str:
    return d.strftime("%d.%m.%Y")


# --------------------------------------------------------------------------
# Auftragsbestätigung lesen
# --------------------------------------------------------------------------

@dataclass
class Hinweis:
    """Meldung an den Anwender. standard=True: ein Standardwert wurde eingesetzt
    (die Oberfläche zeigt ihn ohnehin im Formular); option = zugehörige CLI-Option."""
    text: str
    option: str = ""
    standard: bool = False

    def __str__(self) -> str:
        if self.option:
            return f"{self.text} ({'überschreibbar mit ' if self.standard else ''}{self.option})"
        return self.text


@dataclass
class Position:
    titel: str
    produkt: str
    einheit: str
    menge: str
    betrag: Decimal
    beschreibung: str = ""


@dataclass
class Auftrag:
    ab_nr: str
    ab_datum: dt.date
    kunde: str
    strasse: str
    plz_ort: str
    kunden_nr: str
    kontakt: str
    projekt: str
    summe_netto: Decimal
    zahlungsziel_tage: int
    bestell_nr: str = ""
    bestell_datum: dt.date | None = None
    liefertermin: dt.date | None = None
    rechnungs_mail: str = ""
    positionen: list[Position] = field(default_factory=list)
    hinweise: list[Hinweis] = field(default_factory=list)
    # nur bei Belegen, die schon eine Rechnung sind (z. B. SAP Ariba):
    quelle: str = "Auftragsbestätigung"
    rechnungsnr: str = ""
    rechnungsdatum: dt.date | None = None
    leistung_von: dt.date | None = None
    leistung_bis: dt.date | None = None
    ust_id_kunde: str = ""


import woerterbuch as wb  # noqa: E402  (Feldbezeichnungen aus woerterbuch.json)


def _zeilen(words, tol=2.0):
    """Wörter zu Zeilen gruppieren (nach top), Zeilen nach x sortiert."""
    zeilen: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if zeilen and abs(zeilen[-1][0]["top"] - w["top"]) <= tol:
            zeilen[-1].append(w)
        else:
            zeilen.append([w])
    return [sorted(z, key=lambda w: w["x0"]) for z in zeilen]


def _text(ws) -> str:
    return " ".join(w["text"] for w in ws).strip()


def _kopf_felder(words, x_rechts: float, y_bis: float) -> dict[str, str]:
    """Rechter Kopfblock 'Bezeichnung: Wert' (Bezeichnungen aus dem Wörterbuch, Schlüssel = Feldname
    wie 'kunde', 'kunden_nr'); Folgezeilen ohne Bezeichnung gehören zum vorigen Feld."""
    felder: dict[str, str] = {}
    aktuell = None
    regeln = [(feld, re.compile(r"^" + wb.rx("ab", "kopf", feld) + r"(?![A-Za-zÄÖÜäöüß])\s*:?\s*(.*)$"))
              for feld in wb.laden()["ab"]["kopf"]]
    for z in _zeilen([w for w in words if w["x0"] >= x_rechts and w["top"] < y_bis]):
        t = _text(z)
        # längste passende Bezeichnung gewinnt ("Kunden-Nr." vor "Kunde")
        treffer = [(m.start(1), feld, m) for feld, rx in regeln if (m := rx.match(t))]
        if treffer:
            _, aktuell, m = max(treffer, key=lambda x: x[0])
            felder[aktuell] = m.group(1).strip()
        elif aktuell:
            felder[aktuell] = (felder[aktuell] + " " + t).strip()
    return felder


def oeffne_pdf(pdf_pfad, pdf=None):
    """Bereits geöffnetes PDF weiterverwenden (nicht schließen) oder Datei öffnen."""
    import contextlib

    import pdfplumber
    return contextlib.nullcontext(pdf) if pdf is not None else pdfplumber.open(str(pdf_pfad))


def lese_ab(pdf_pfad: Path, pdf=None) -> Auftrag:
    with oeffne_pdf(pdf_pfad, pdf) as pdf:
        words = []
        volltext = []
        y_off = 0.0
        for pg in pdf.pages:
            for w in pg.extract_words(keep_blank_chars=False, use_text_flow=False):
                if w["top"] > float(pg.height) * FUSS_AB:
                    continue  # Fußzeile (Firmendaten/Bank) jeder Seite ignorieren
                w = dict(w)
                w["top"] += y_off
                words.append(w)
            volltext.append(pg.crop((0, 0, float(pg.width), float(pg.height) * FUSS_AB)).extract_text() or "")
            y_off += float(pg.height)
        breite = float(pdf.pages[0].width)
    text = "\n".join(volltext)
    zeilen = _zeilen(words)
    zeilen_txt = [_text(z) for z in zeilen]

    # --- AB-Nummer (Titelzeile) -----------------------------------------
    titel_rx = re.compile(r"^" + wb.rx("ab", "titel") + r"\s*:?\s+(\S.*)$")
    ab_zeile = next((i for i, t in enumerate(zeilen_txt) if titel_rx.match(t)), None)
    if ab_zeile is None:
        raise AbFehler("Zeile 'Auftragsbestätigung <Nr>' nicht gefunden")
    ab_nr = titel_rx.match(zeilen_txt[ab_zeile]).group(1).strip()
    y_titel = zeilen[ab_zeile][0]["top"]

    # --- Kopfblock rechts -----------------------------------------------
    datum_rx = re.compile(r"^" + wb.rx("ab", "kopf", "datum") + r"\s*:")
    datum_w = next((w for z in zeilen if z[0]["top"] < y_titel
                    for i, w in enumerate(z) if datum_rx.match(_text(z[i:i + 3]))), None)
    x_rechts = (datum_w["x0"] - 5) if datum_w else breite * 0.5
    kopf = _kopf_felder(words, x_rechts, y_titel)
    for pflicht in ("datum", "kunde", "anschrift", "kunden_nr", "kontakt", "projekt"):
        if not kopf.get(pflicht):
            raise AbFehler("Kopffeld " + wb.nicht_gefunden("ab", "kopf", pflicht))

    anschrift = kopf["anschrift"]
    m = re.match(r"^(.*?)[,;]?\s+((?:D-)?\d{5}\s+.+)$", anschrift)
    if not m:
        raise AbFehler(f"Anschrift nicht in Straße / PLZ Ort zerlegbar: {anschrift!r}")
    strasse, plz_ort = m.group(1).rstrip(", "), m.group(2).strip()

    # --- Bestellung / Liefertermin / Zahlungsziel / Summe ----------------
    lese_hinweise: list[Hinweis] = []
    bestell_nr, bestell_datum = lies_bestellung(text, lese_hinweise)
    liefertermin = None
    # Bezeichnung am Zeilenanfang (sonst träfe z. B. "Projekt: Fertigstellung …" das Synonym "Fertigstellung");
    # der erste Treffer mit lesbarem Datum gewinnt
    kandidaten = [m.group(1).strip() for m in
                  wb.alle(text, ("ab", "liefertermin"), r"([^\n]*\S[^\n]*)", zeilenanfang=True, naechste_zeile=True)]
    for k in kandidaten:
        try:
            liefertermin = parse_datum(k)
            break
        except AbFehler:
            pass
    if kandidaten and liefertermin is None:
        lese_hinweise.append(Hinweis(f"Liefertermin '{kandidaten[0]}' ist kein Datum – "
                                     "Leistungsende bitte selbst angeben", "--bis"))
    m = wb.suche(text, ("ab", "zahlungsziel"), r"[^\n\d]{0,30}?(\d+)\s*Tag", naechste_zeile=True)
    if not m:
        raise AbFehler("Zahlungsziel (N Tage): " + wb.nicht_gefunden("ab", "zahlungsziel"))
    zahlungsziel = int(m.group(1))
    # nur eine Zeile, die aus Bezeichnung und Betrag besteht; die letzte zählt (Beschreibungen stehen davor)
    m = next(reversed(list(wb.alle(text, ("ab", "summe_netto"), SUMMENZEILE, zeilenanfang=True))), None)
    if not m:
        raise AbFehler("Summe: " + wb.nicht_gefunden("ab", "summe_netto"))
    summe = parse_betrag(m.group(1))

    positionen = _lese_positionen(words, zeilen, zeilen_txt)
    if not positionen:
        raise AbFehler("Keine Produktposition in der Tabelle gefunden")
    pos_summe = sum((p.betrag for p in positionen), Decimal("0"))
    if pos_summe != summe:
        raise AbFehler(f"Summe der Positionen {pos_summe} ≠ Summe Auftrag {summe}")

    return Auftrag(
        ab_nr=ab_nr,
        ab_datum=parse_datum(kopf["datum"]),
        kunde=kopf["kunde"],
        strasse=strasse,
        plz_ort=plz_ort,
        kunden_nr=kopf["kunden_nr"],
        kontakt=kopf["kontakt"],
        projekt=kopf["projekt"],
        summe_netto=summe,
        zahlungsziel_tage=zahlungsziel,
        bestell_nr=bestell_nr,
        bestell_datum=bestell_datum,
        liefertermin=liefertermin,
        rechnungs_mail=kopf.get("rechnungsempf", ""),
        positionen=positionen,
        hinweise=lese_hinweise,
    )


def lies_bestellung(text: str, hinweise: list[Hinweis]) -> tuple[str, dt.date | None]:
    """Zeile 'Ihre Bestellung [Nr.] <Nummer> … [vom <Datum>]' → (Nummer, Datum).
    Nummer = erstes Wort mit einer Ziffer vor 'vom' (nicht 'per', 'Nr.', 'Email' …); Datum optional."""
    m = wb.suche(text, ("ab", "bestellung"), r"([^\n]*)")
    if not m:
        return "", None
    teile = re.split(r"\bvom\b", m.group(1), maxsplit=1)
    vor, nach = teile[0], (teile[1] if len(teile) > 1 else "")
    nr = re.search(r"(?<![\w.])([A-Za-z0-9][\w./-]*\d[\w./-]*)", vor)
    datum = None
    if nach.strip(" :"):
        try:
            datum = parse_datum(nach)
        except AbFehler:
            hinweise.append(Hinweis(f"Bestelldatum '{nach.strip(' :')}' nicht lesbar – bitte selbst angeben",
                                    "--bestelldatum"))
    return (nr.group(1).rstrip(".,;:") if nr else ""), datum


def lese_beleg(pdf_pfad: Path) -> Auftrag:
    """Belegart erkennen: CS-Auftragsbestätigung oder SAP-Ariba-Rechnung."""
    import quelle_ariba

    with oeffne_pdf(pdf_pfad) as pdf:   # einmal öffnen, für Erkennung und Lesen
        erste = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
        if quelle_ariba.ist_ariba(erste):
            return quelle_ariba.lese_ariba(pdf_pfad, pdf)
        if not wb.erkannt(erste, "auftragsbestaetigung"):
            raise AbFehler("Unbekannter Beleg – erwartet wird eine CS-Auftragsbestätigung "
                           "oder eine SAP-Ariba-Rechnung (Standardrechnung)")
        return lese_ab(pdf_pfad, pdf)


def _lese_positionen(words, zeilen, zeilen_txt) -> list[Position]:
    spalten_namen = ("produkt", "einheit", "menge", "summe")

    def kopfspalten(z):
        """Wort je Spalte im Tabellenkopf (Bezeichnungen aus dem Wörterbuch) oder None."""
        k = {}
        for name in spalten_namen:
            w = next((w for w in z if wb.wort_passt(w["text"], "ab", "tabelle", name)), None)
            if w is None:
                return None
            k[name] = w
        return k if k["produkt"]["x0"] < k["einheit"]["x0"] < k["menge"]["x0"] < k["summe"]["x0"] else None

    kopf_i, k = next(((i, kk) for i, z in enumerate(zeilen) if (kk := kopfspalten(z))), (None, None))
    if kopf_i is None:
        raise AbFehler("Tabellenkopf 'Produkte / Einheit / Menge / Summe' nicht gefunden")
    b_einheit = k["einheit"]["x0"] - 25
    b_menge = (k["einheit"]["x1"] + k["menge"]["x0"]) / 2
    b_summe = (k["menge"]["x1"] + k["summe"]["x0"]) / 2

    summe_rx = re.compile(r"^\s*" + wb.rx("ab", "summe_netto") + r"(?![A-Za-zÄÖÜäöüß])[ \t]*:?[ \t]*" + SUMMENZEILE)
    ende_i = next((i for i in range(kopf_i + 1, len(zeilen_txt)) if summe_rx.match(zeilen_txt[i])), len(zeilen_txt))

    positionen: list[Position] = []
    roh: dict | None = None
    in_beschreibung = False
    beschr_absaetze: list[list[str]] = []
    letzte_top = None

    def abschliessen():
        if roh is None:
            return
        produkt_zeilen = roh["produkt"]
        produkt = produkt_zeilen[0] if produkt_zeilen else ""
        titel = " ".join(produkt_zeilen[1:]).strip() or produkt
        beschr = " ".join(" ".join(a) for a in roh["beschr"] if a).strip()
        positionen.append(Position(
            titel=titel, produkt=produkt,
            einheit=" ".join(roh["einheit"]).strip(),
            menge=" ".join(roh["menge"]).strip(),
            betrag=parse_betrag(" ".join(roh["summe"])),
            beschreibung=re.sub(r"\s+", " ", beschr),
        ))

    for i in range(kopf_i + 1, ende_i):
        z = zeilen[i]
        t = zeilen_txt[i]
        spalten = {"produkt": [], "einheit": [], "menge": [], "summe": []}
        for w in z:
            mitte = (w["x0"] + w["x1"]) / 2
            if w["x0"] < b_einheit:
                spalten["produkt"].append(w)
            elif mitte < b_menge:
                spalten["einheit"].append(w)
            elif mitte < b_summe:
                spalten["menge"].append(w)
            else:
                spalten["summe"].append(w)
        hat_betrag = bool(spalten["summe"]) and re.search(r"\d", _text(spalten["summe"]))
        if hat_betrag and spalten["menge"]:
            abschliessen()
            roh = {"produkt": [], "einheit": [], "menge": [], "summe": [], "beschr": []}
            in_beschreibung = False
        if roh is None:
            continue
        if wb.beginnt_mit(t, "ab", "leistungsbeschreibung"):
            in_beschreibung = True
            rest = re.sub(r"^\s*" + wb.rx("ab", "leistungsbeschreibung") + r"\s*:?\s*", "", t)
            roh["beschr"].append([rest] if rest else [])
            letzte_top = z[0]["top"]
            continue
        if in_beschreibung:
            # Leerzeile (größerer Abstand) = neuer Absatz
            if letzte_top is not None and z[0]["top"] - letzte_top > 14:
                roh["beschr"].append([])
            roh["beschr"][-1].append(t)
            letzte_top = z[0]["top"]
            continue
        for key in ("produkt", "einheit", "menge", "summe"):
            if spalten[key]:
                roh[key].append(_text(spalten[key]))
    abschliessen()
    return positionen


# --------------------------------------------------------------------------
# DOCX-Platzhalter ersetzen (run-übergreifend)
# --------------------------------------------------------------------------

# Eckige Klammern in eingesetzten Werten (Kunde "ACME [K-1042]", Projekt "Pentest [Phase 2]") werden bis
# zum Schluss durch Zeichen aus dem privaten Unicode-Bereich ersetzt: So greift keine spätere Regel auf
# eingesetzte Daten zu und die Platzhalterprüfung meldet nur echte Vorlagenreste.
MASKE = str.maketrans({"[": "\ue000", "]": "\ue001"})
DEMASKE = str.maketrans({"\ue000": "[", "\ue001": "]"})

P_RX = re.compile(r"<w:p(?:\s[^>]*?)?(?<!/)>.*?</w:p>", re.S)
T_RX = re.compile(r"<w:t(\s[^>]*)?>([^<]*)</w:t>")
TR_RX = re.compile(r"<w:tr(?:\s[^>]*?)?(?<!/)>.*?</w:tr>", re.S)


def _ersetze_im_absatz(p_xml: str, regeln) -> str:
    """Wendet Regeln (regex, ersatz) auf den Gesamttext eines Absatzes an.

    Ersetzt wird nur Gruppe 'ph' (falls vorhanden), sonst der ganze Treffer.
    Der Ersatz landet im ersten betroffenen Run (behält dessen Formatierung).
    """
    for rx, ersatz in regeln:
        start = 0  # nach jedem Ersatz dahinter weitersuchen (eingesetzter Text wird nie erneut ersetzt)
        while True:
            ts = list(T_RX.finditer(p_xml))
            if not ts:
                return p_xml
            texte = [html.unescape(m.group(2)) for m in ts]
            gesamt = "".join(texte)
            m = rx.search(gesamt, start)
            if not m:
                break
            a, b = (m.span("ph") if "ph" in rx.groupindex else m.span())
            # eingesetzte Werte maskieren: Klammern darin sind Daten, keine Platzhalter (s. MASKE)
            neu_txt = (ersatz(m) if callable(ersatz) else ersatz).translate(MASKE)
            start = a + len(neu_txt) + (1 if a == b else 0)
            if gesamt[a:b] == neu_txt:
                continue
            neu = list(texte)
            off = 0
            erster = True
            for i, tx in enumerate(texte):
                s, e = off, off + len(tx)
                off = e
                if e <= a or s >= b:
                    continue
                lo, hi = max(a, s) - s, min(b, e) - s
                if erster:
                    neu[i] = tx[:lo] + neu_txt + tx[hi:]
                    erster = False
                else:
                    neu[i] = tx[hi:]
            if erster:  # leerer Treffer an Run-Grenze
                break
            teile = []
            last = 0
            for mt, tx in zip(ts, neu):
                teile.append(p_xml[last:mt.start()])
                attrs = mt.group(1) or ""
                if "xml:space" not in attrs:
                    attrs += ' xml:space="preserve"'
                teile.append(f"<w:t{attrs}>{html.escape(tx, quote=False)}</w:t>")
                last = mt.end()
            teile.append(p_xml[last:])
            p_xml = "".join(teile)
    return p_xml


def ersetze(xml: str, regeln) -> str:
    return P_RX.sub(lambda m: _ersetze_im_absatz(m.group(0), regeln), xml)


def _r(muster: str):
    return re.compile(muster)


def absatz_texte(xml: str) -> list[str]:
    return ["".join(html.unescape(t.group(2)) for t in T_RX.finditer(p.group(0)))
            for p in P_RX.finditer(xml)]


@dataclass
class Rechnung:
    nr: str
    datum: dt.date
    faellig: dt.date
    von: dt.date
    bis: dt.date
    uebergabe: dt.date | None   # None: kein Satz zu Bericht/Roadmap
    ust_id: str
    angebot: str
    auftrag: Auftrag
    bestell_nr: str = ""                 # Bestellnummer des Kunden (Standard: aus der AB)
    bestell_datum: dt.date | None = None

    @property
    def bestellung(self) -> str:
        """'45104423 vom 29.10.2025' bzw. nur die Nummer."""
        if not self.bestell_nr:
            return ""
        return self.bestell_nr + (f" vom {fmt_datum(self.bestell_datum)}" if self.bestell_datum else "")

    @property
    def netto(self) -> Decimal:
        return self.auftrag.summe_netto

    @property
    def ust(self) -> Decimal:
        return ust_von(self.netto)

    @property
    def brutto(self) -> Decimal:
        return self.netto + self.ust

    @property
    def zeitraum(self) -> str:
        von = self.von.strftime("%d.%m.") if self.von.year == self.bis.year else fmt_datum(self.von)
        return f"{von} – {fmt_datum(self.bis)}"


def _positionszeilen(xml: str, r: Rechnung) -> str:
    """Positionszeile (Pos '01') je AB-Position klonen und füllen."""
    vorlage_tr = next((m for m in TR_RX.finditer(xml)
                       if "STANDARD" in "".join(absatz_texte(m.group(0)))), None)
    if vorlage_tr is None:
        raise RuntimeError("Positionszeile in der Vorlage nicht gefunden")
    zeilen = []
    for n, p in enumerate(r.auftrag.positionen, 1):
        # Menge-Spalte ist schmal: dort nur die Zahl, Einheit + Produktzeile in die Beschreibung
        kopf = [f"{p.menge} × {p.einheit}" if p.einheit else ""]
        if p.produkt and p.produkt != p.titel:
            kopf.append(p.produkt)
        kopf = " · ".join(k for k in kopf if k)
        beschreibung = " ".join(t for t in (f"{kopf}." if kopf else "", p.beschreibung) if t)
        menge = p.menge
        regeln = [
            (_r(r"^01$"), f"{n:02d}"),
            # Reihenfolge wichtig: erst Beispielbeschreibung, dann Titel
            (_r(r"^Interner Angriffspfad-Test ab kompromittiertem Zugang\. Drei Phasen.*Retest-Kriterien\.$"),
             beschreibung),
            (_r(r"^STANDARD — \[Leistungsbezeichnung\] \(Festpreis\)$"), p.titel),
            (_r(r"\[TT\.MM\.\] – \[TT\.MM\.JJJJ\]"), r.zeitraum),
            (_r(r"^1 Pauschale$"), menge),
            (_r(r"^19 %$"), f"{UST_SATZ} %"),
            (_r(r"^\[0,00 €\]$"), fmt_betrag(p.betrag)),
        ]
        zeilen.append(re.sub(r' w14:(?:paraId|textId)="[0-9A-Fa-f]+"', "", ersetze(vorlage_tr.group(0), regeln)))
    return xml[:vorlage_tr.start()] + "".join(zeilen) + xml[vorlage_tr.end():]


def _infozeile_bestellung(xml: str) -> str:
    """Infoblock rechts oben (Rechnungsnummer … Kundennummer) um 'Ihre Bestellung' ergänzen:
    Zeile 'Kundennummer' klonen, Beschriftung tauschen, Wert als Platzhalter [BESTELLUNG]."""
    zeile = next((m for m in P_RX.finditer(xml)
                  if "".join(absatz_texte(m.group(0))).startswith("Kundennummer  ")), None)
    if zeile is None:
        raise RuntimeError("Zeile 'Kundennummer' in der Vorlage nicht gefunden")
    neu = _zeile_wie_vorlage(zeile.group(0), "Ihre Bestellung  ", "[BESTELLUNG]")
    return xml[:zeile.end()] + neu + xml[zeile.end():]


def _zeile_wie_vorlage(p_xml: str, label: str, wert: str) -> str:
    """Absatz mit Label-Run (grau) + Wert-Runs (fett): ersten Text = Label, zweiten = Wert, Rest leer."""
    ts = list(T_RX.finditer(p_xml))
    teile, last = [], 0
    for i, mt in enumerate(ts):
        txt = label if i == 0 else (wert if i == 1 else "")
        attrs = mt.group(1) or ""
        if "xml:space" not in attrs:
            attrs += ' xml:space="preserve"'
        teile.append(p_xml[last:mt.start()])
        teile.append(f"<w:t{attrs}>{html.escape(txt, quote=False)}</w:t>")
        last = mt.end()
    teile.append(p_xml[last:])
    # paraId muss eindeutig bleiben
    return re.sub(r' w14:paraId="[0-9A-F]+"', "", "".join(teile))


def _entferne_absatz(xml: str, text_rx: str) -> str:
    """Ganzen Absatz entfernen, dessen Gesamttext auf text_rx passt (z. B. leere Infozeile)."""
    rx = re.compile(text_rx)
    return P_RX.sub(lambda m: "" if rx.fullmatch("".join(absatz_texte(m.group(0)))) else m.group(0), xml)


def _entferne_lauf(xml: str, text: str) -> str:
    """Einen Run (samt Zeilenumbruch davor) mit genau diesem Text entfernen."""
    return re.sub(r"<w:r>(?:(?!</w:r>).)*?<w:t[^>]*>" + re.escape(html.escape(text, quote=False))
                  + r"</w:t></w:r>", "", xml, flags=re.S)


def _optionale_teile(r: "Rechnung") -> list:
    """Regeln für Belege ohne AB, ohne Übergabe von Bericht/Roadmap usw. (vor den allgemeinen Regeln)."""
    a = r.auftrag
    grundlage = f"Ihrer Bestellung {r.bestellung}" if r.bestell_nr else "des Auftrags"
    regeln = []
    if r.uebergabe is None:
        regeln += [(_r(r", Bericht und Roadmap übergeben"), ""),
                   (_r(r" Bericht, priorisierte Findings und 30/60/90-Tage-Roadmap wurden am "
                       r"\[TT\.MM\.JJJJ\] übergeben\."), "")]
    if not a.ab_nr:
        regeln += [(_r(r"Leistung erbracht gemäß Auftragsbestätigung"),
                    f"Leistung erbracht gemäß {grundlage}" if r.bestell_nr else "Leistung erbracht"),
                   (_r(r"Leistungen gemäß Auftragsbestätigung \[2026-014-AB\]"),
                    f"Leistungen gemäß {grundlage}" if r.bestell_nr else "Leistungen"),
                   (_r(r"auf Grundlage der Auftragsbestätigung \[2026-014-AB\]"), f"auf Grundlage {grundlage}")]
    if a.quelle == "SAP Ariba":
        regeln.append((_r(r"Es gelten unsere AGB"),
                       "Die Rechnung wurde elektronisch über SAP Ariba übermittelt. Es gelten unsere AGB"))
    return regeln


def fuelle_vorlage(vorlage: Path, ziel: Path, r: Rechnung) -> None:
    a = r.auftrag
    angebot = (lambda m: f" und des Angebots {r.angebot}") if r.angebot else ""
    regeln_global = _optionale_teile(r) + [
        # Bestellnummer des Kunden: Verwendungszweck, Anschreiben, Infoblock (vor den allgemeinen Regeln)
        (_r(r"Verwendungszweck: Rechnung (?P<ph>\[2026-0142\])"),
         f"{r.nr} / Bestell-Nr. {r.bestell_nr}" if r.bestell_nr else r.nr),
        (_r(r"Leistungen gemäß Auftragsbestätigung"),
         f"Leistungen gemäß Ihrer Bestellung {r.bestellung} und Auftragsbestätigung" if r.bestell_nr
         else "Leistungen gemäß Auftragsbestätigung"),
        (_r(r"^Ihre Bestellung  (?P<ph>\[BESTELLUNG\])$"), r.bestellung),
        (_r(r"\[2026-0142\]"), r.nr),
        (_r(r"\[2026-014-AB\]"), a.ab_nr),
        (_r(r" und des Angebots \[2026-014\]"), angebot),
        (_r(r"\[Leistungsbezeichnung\]"), a.projekt),
        (_r(r"\[Kundenname GmbH\]"), a.kunde),
        (_r(r"\[Ansprechpartner\]"), a.kontakt),
        (_r(r"\[Straße Nr\.\]"), a.strasse),
        (_r(r"\[PLZ Ort\]"), a.plz_ort),
        (_r(r"\[DE…\]"), r.ust_id or "—"),
        (_r(r"Kostenstelle: (?P<ph>\[—\])"), r.bestell_nr or "—"),
        (_r(r"\[K-1042\]"), a.kunden_nr),
        (_r(r"\[TT\.MM\.\] – \[TT\.MM\.JJJJ\]"), r.zeitraum),
        (_r(r"Rechnungsdatum:?\s+(?P<ph>\[TT\.MM\.JJJJ\])"), fmt_datum(r.datum)),
        (_r(r"Zahlbar bis:\s*(?P<ph>\[TT\.MM\.JJJJ\])"), fmt_datum(r.faellig)),
        (_r(r"am (?P<ph>\[TT\.MM\.JJJJ\]) übergeben"), fmt_datum(r.uebergabe) if r.uebergabe else ""),
        (_r(r"^\[TT\.MM\.JJJJ\]$"), fmt_datum(r.faellig)),
        (_r(r"^(?P<ph>\d+) Tage netto ab Rechnungsdatum"), str(a.zahlungsziel_tage)),
        (_r(r"Steuersatz (?P<ph>19) %"), str(UST_SATZ)),
        (_r(r"^Umsatzsteuer (?P<ph>19) %$"), str(UST_SATZ)),
    ]
    with zipfile.ZipFile(vorlage) as zin:
        dateien = {n: zin.read(n) for n in zin.namelist()}
        infos = {i.filename: i for i in zin.infolist()}

    doc = dateien["word/document.xml"].decode("utf-8")
    doc = _positionszeilen(doc, r)
    if r.bestell_nr:
        doc = _infozeile_bestellung(doc)
    # Leere Angaben: Zeile weglassen statt Platzhalter oder "—"
    if not a.ab_nr:
        doc = _entferne_absatz(doc, r"Auftragsbestätigung  \[2026-014-AB\]")
    if not a.kunden_nr:
        doc = _entferne_absatz(doc, r"Kundennummer  \[K-1042\]")
    if not a.kontakt:
        doc = _entferne_lauf(doc, "z. Hd. [Ansprechpartner]")
    doc = ersetze(doc, regeln_global)
    # Summenblock: verbleibende [0,00 €] in Reihenfolge netto, USt, brutto
    summen = iter([r.netto, r.ust, r.brutto])
    doc = ersetze(doc, [(_r(r"^\[0,00 €\]$"), lambda m: fmt_betrag(next(summen)))])
    dateien["word/document.xml"] = doc.encode("utf-8")

    for name in list(dateien):
        if re.match(r"word/(header|footer)\d*\.xml$", name):
            dateien[name] = ersetze(dateien[name].decode("utf-8"), regeln_global).encode("utf-8")

    if "docProps/core.xml" in dateien:
        dateien["docProps/core.xml"] = _kenndaten(dateien["docProps/core.xml"].decode("utf-8"), r).encode("utf-8")

    ct = dateien["[Content_Types].xml"].decode("utf-8")
    ct = ct.replace("wordprocessingml.template.main+xml", "wordprocessingml.document.main+xml")
    dateien["[Content_Types].xml"] = ct.encode("utf-8")

    rest = pruefe_platzhalter(dateien)
    if rest:
        raise RuntimeError("Nicht ersetzte Platzhalter: " + "; ".join(rest))
    for name in dateien:
        if re.match(r"word/.*\.xml$", name):
            dateien[name] = dateien[name].decode("utf-8").translate(DEMASKE).encode("utf-8")

    # Atomar schreiben: erst Temp-Datei im Zielordner, dann ersetzen – eine vorhandene Rechnung
    # bleibt erhalten, wenn das Schreiben scheitert (Platte voll, Netzlaufwerk).
    ziel.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".~", suffix=".docx", dir=str(ziel.parent))
    try:
        with os.fdopen(fd, "wb") as f, zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as zout:
            for name, daten in dateien.items():
                zout.writestr(infos[name], daten, compress_type=zipfile.ZIP_DEFLATED)
        os.replace(tmp, ziel)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _kenndaten(core: str, r: "Rechnung") -> str:
    """Dokumenteigenschaften: Titel/Betreff lesbar, dc:identifier mit Rechnungsnummer und Kunde im
    Original (Dateinamen sind verlustbehaftet: 'Acme & Partner' und 'Acme Partner' ergeben denselben)."""
    ident = html.escape(json.dumps({"rechnungsnr": r.nr, "kunde": r.auftrag.kunde}, ensure_ascii=False), quote=False)
    titel = html.escape(f"Rechnung {r.nr}", quote=False)
    core = re.sub(r"<dc:title>.*?</dc:title>", lambda m: f"<dc:title>{titel}</dc:title>", core, flags=re.S)
    core = re.sub(r"<dc:(subject|identifier)>.*?</dc:\1>", "", core, flags=re.S)
    zusatz = (f"<dc:subject>{html.escape(r.auftrag.kunde, quote=False)}</dc:subject>"
              f"<dc:identifier>{ident}</dc:identifier>")
    if "<dc:title>" in core:
        return core.replace("</dc:title>", "</dc:title>" + zusatz, 1)
    return core.replace("</cp:coreProperties>", zusatz + "</cp:coreProperties>", 1)


def rechnungs_identitaet(docx: Path) -> tuple[str, str] | None:
    """(Rechnungsnummer, Kunde) einer von diesem Programm erzeugten Rechnung, sonst None."""
    try:
        with zipfile.ZipFile(docx) as z:
            core = z.read("docProps/core.xml").decode("utf-8")
        m = re.search(r"<dc:identifier>(.*?)</dc:identifier>", core, re.S)
        if not m:
            return None
        d = json.loads(html.unescape(m.group(1)))
        return d["rechnungsnr"], d["kunde"]
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None


PLATZHALTER_RX = re.compile(r"\[[^\]\[]{0,40}\]")


def pruefe_platzhalter(dateien: dict[str, bytes]) -> list[str]:
    funde = []
    for name, daten in dateien.items():
        if re.match(r"word/(document|header\d*|footer\d*)\.xml$", name):
            for t in absatz_texte(daten.decode("utf-8")):
                funde += [f"{name}: {m}" for m in PLATZHALTER_RX.findall(t)]
    return funde


def finde_soffice() -> str | None:
    mac = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    return shutil.which("soffice") or shutil.which("libreoffice") or (mac if Path(mac).exists() else None)


def nach_pdf(docx: Path) -> Path:
    """DOCX → PDF mit LibreOffice. Wirft RuntimeError, wenn kein neues PDF entsteht."""
    soffice = finde_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice nicht gefunden")
    ziel = docx.with_suffix(".pdf")
    ziel.unlink(missing_ok=True)  # nie ein altes PDF als Ergebnis ausgeben
    try:
        lauf = subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir",
                               str(docx.parent), str(docx)], capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        raise RuntimeError("LibreOffice hat nicht innerhalb von 3 Minuten geantwortet")
    if lauf.returncode != 0 or not ziel.exists():
        grund = (lauf.stderr or lauf.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError("LibreOffice hat kein PDF erzeugt"
                           + (f" ({grund[-1]})" if grund else "")
                           + " – läuft LibreOffice gerade? Dann beenden und erneut versuchen")
    return ziel


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _dateiname(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9ÄÖÜäöüß_-]+", "_", s).strip("_")


NR_ERLAUBT = re.compile(r"[A-Za-z0-9ÄÖÜäöü][A-Za-z0-9ÄÖÜäöü ._/#+-]{0,39}")


def pruefe_rechnungsnr(nr: str) -> str:
    nr = (nr or "").strip()
    if not nr:
        raise AbFehler("Rechnungsnummer fehlt (--rechnungsnr)")
    if not NR_ERLAUBT.fullmatch(nr):
        raise AbFehler(f"Rechnungsnummer '{nr}' enthält Zeichen, die nicht erlaubt sind. "
                       "Erlaubt: Buchstaben, Ziffern, Leerzeichen und . _ / # + -")
    return nr


def rechnungs_dateiname(rechnungsnr: str, kunde: str) -> str:
    return f"{_dateiname(rechnungsnr)}_Rechnung_{_dateiname(kunde)}.docx"


@dataclass
class Ergebnis:
    docx: Path
    pdf: Path | None
    rechnung: "Rechnung"
    hinweise: list[Hinweis]


def erstelle_rechnung(ab_pdf: Path, rechnungsnr: str | None = None, datum: dt.date | None = None,
                      von: dt.date | None = None, bis: dt.date | None = None,
                      uebergabe: dt.date | None = None, ust_id: str | None = None, angebot: str = "",
                      ausgabe: Path | None = None, vorlage: Path = VORLAGE,
                      pdf: bool = False, auftrag: Auftrag | None = None,
                      bestell_nr: str | None = None, bestell_datum: dt.date | None = None,
                      mit_uebergabe: bool | None = None, ueberschreiben: bool = False) -> Ergebnis:
    """Erzeugt die Rechnung. Ein bereits gelesener Auftrag kann übergeben werden.
    None bedeutet jeweils: Wert aus dem Beleg bzw. Standard. Scheitert nur der PDF-Export,
    ist das DOCX trotzdem fertig (Hinweis statt Ausnahme)."""
    a = auftrag or lese_beleg(ab_pdf)
    hinweise = list(a.hinweise)
    rechnungsnr = pruefe_rechnungsnr(rechnungsnr or a.rechnungsnr)
    datum = datum or a.rechnungsdatum or dt.date.today()
    bis = bis or a.leistung_bis
    von = von or a.leistung_von
    if ust_id is None:
        ust_id = a.ust_id_kunde
    if mit_uebergabe is None:  # Bericht/Roadmap standardmäßig nur bei Projekten aus der AB – oder wenn ein Datum angegeben ist
        mit_uebergabe = a.quelle == "Auftragsbestätigung" or uebergabe is not None
    if bis is None:
        bis = a.liefertermin or datum
        hinweise.append(Hinweis(f"Leistungszeitraum-Ende = {'Liefertermin' if a.liefertermin else 'Rechnungsdatum'} "
                                f"{fmt_datum(bis)}", "--bis", standard=True))
    if von is None:
        von = a.bestell_datum or a.ab_datum
        hinweise.append(Hinweis(f"Leistungszeitraum-Beginn = {'Bestelldatum' if a.bestell_datum else 'AB-Datum'} "
                                f"{fmt_datum(von)}", "--von", standard=True))
    if not mit_uebergabe:
        uebergabe = None
    elif uebergabe is None:
        uebergabe = bis
        hinweise.append(Hinweis(f"Übergabe Bericht/Roadmap = {fmt_datum(bis)}", "--uebergabe", standard=True))
    if von > bis:
        raise AbFehler(f"Leistungsbeginn {fmt_datum(von)} liegt nach Leistungsende {fmt_datum(bis)}")
    if not ust_id:
        hinweise.append(Hinweis("USt-IdNr. des Empfängers nicht angegeben – '—' eingesetzt", "--ust-id"))
    if not angebot:
        hinweise.append(Hinweis("Angebotsnummer nicht angegeben – Verweis auf Angebot entfernt", "--angebot"))
    # Bestellnummer des Kunden: Angabe beim Aufruf hat Vorrang vor der AB ("" = bewusst keine)
    if bestell_nr is None:
        bestell_nr, bestell_datum = a.bestell_nr, (bestell_datum or a.bestell_datum)
    bestell_nr = bestell_nr.strip()
    if not bestell_nr:
        bestell_datum = None
        hinweise.append(Hinweis("Keine Bestellnummer des Kunden – nur '—' unter der Anschrift", "--bestellnr"))
    r = Rechnung(nr=rechnungsnr, datum=datum,
                 faellig=datum + dt.timedelta(days=a.zahlungsziel_tage),
                 von=von, bis=bis, uebergabe=uebergabe, ust_id=ust_id, angebot=angebot, auftrag=a,
                 bestell_nr=bestell_nr, bestell_datum=bestell_datum)
    if ausgabe is None:
        ausgabe = ab_pdf.parent / rechnungs_dateiname(rechnungsnr, a.kunde)
    pdf_ziel = ausgabe.with_suffix(".pdf")
    # Das PDF-Ziel kann der Beleg selbst sein (z. B. RE-2026-09-30-05.pdf → RE-2026-09-30-05.docx): nie anfassen
    ist_beleg = ab_pdf is not None and pdf_ziel.resolve() == Path(ab_pdf).resolve()
    # Überschreiben nur derselben Rechnung und nur mit Zustimmung (gilt für App und Kommandozeile)
    if ausgabe.exists():
        ident = rechnungs_identitaet(ausgabe)
        if ident and ident != (rechnungsnr, a.kunde):
            raise DateiExistiert(f"{ausgabe.name} gehört zu Rechnung {ident[0]} für {ident[1]} – "
                                 "bitte Rechnungsnummer ändern.", fremd=True)
        if not ueberschreiben:
            raise DateiExistiert(f"{ausgabe.name} existiert bereits.", fremd=False)
    elif pdf_ziel.exists() and not ist_beleg and not ueberschreiben:
        raise DateiExistiert(f"{pdf_ziel.name} existiert bereits.", fremd=False)
    vorfassung = ausgabe.exists() or (pdf_ziel.exists() and not ist_beleg)
    fuelle_vorlage(vorlage, ausgabe, r)
    pdf_pfad = None
    if pdf and ist_beleg:
        hinweise.append(Hinweis(f"PDF nicht erzeugt: {pdf_ziel.name} ist der Beleg selbst – anderen Ausgabenamen wählen"))
    elif pdf:
        try:
            pdf_pfad = nach_pdf(ausgabe)
        except RuntimeError as e:
            hinweise.append(Hinweis(f"PDF-Export fehlgeschlagen: {e}. Das DOCX ist fertig."))
    elif vorfassung and pdf_ziel.exists() and not ist_beleg:
        pdf_ziel.unlink()  # gehörte zur vorherigen Fassung dieser Rechnung
        hinweise.append(Hinweis(f"Veraltetes {pdf_ziel.name} der vorherigen Fassung entfernt"))
    return Ergebnis(ausgabe, pdf_pfad, r, hinweise)


def _datum_arg(s: str) -> dt.date:
    try:
        return parse_datum(s)
    except AbFehler as e:
        raise argparse.ArgumentTypeError(str(e))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rechnung (DOCX) aus Auftragsbestätigung oder SAP-Ariba-Rechnung (PDF)")
    ap.add_argument("ab_pdf", type=Path, help="Auftragsbestätigung oder SAP-Ariba-Rechnung als PDF")
    ap.add_argument("--rechnungsnr", help="z. B. 2026-0142 (bei SAP-Rechnungen Standard: deren Nummer)")
    ap.add_argument("--datum", type=_datum_arg, help="Rechnungsdatum TT.MM.JJJJ (Standard: heute)")
    ap.add_argument("--von", type=_datum_arg, help="Leistungsbeginn (Standard: Bestelldatum)")
    ap.add_argument("--bis", type=_datum_arg, help="Leistungsende (Standard: Liefertermin)")
    ap.add_argument("--uebergabe", type=_datum_arg, help="Übergabe Bericht/Roadmap (Standard: --bis)")
    ap.add_argument("--ohne-uebergabe", action="store_true", help="Satz zu Bericht/Roadmap weglassen")
    ap.add_argument("--ust-id", help="USt-IdNr. des Empfängers (Standard: aus dem Beleg)")
    ap.add_argument("--angebot", default="", help="Angebotsnummer")
    ap.add_argument("--bestellnr", help="Bestellnummer des Kunden (Standard: aus der AB)")
    ap.add_argument("--bestelldatum", type=_datum_arg, help="Datum der Bestellung (Standard: aus der AB)")
    ap.add_argument("--vorlage", type=Path, default=VORLAGE)
    ap.add_argument("-o", "--ausgabe", type=Path, help="Ziel-DOCX")
    ap.add_argument("--pdf", action="store_true", help="zusätzlich PDF via LibreOffice")
    ap.add_argument("--ueberschreiben", action="store_true", help="vorhandene Fassung derselben Rechnung ersetzen")
    args = ap.parse_args(argv)
    try:
        erg = erstelle_rechnung(
            args.ab_pdf, args.rechnungsnr, args.datum, args.von, args.bis, args.uebergabe,
            args.ust_id, args.angebot, args.ausgabe, args.vorlage, args.pdf,
            bestell_nr=args.bestellnr, bestell_datum=args.bestelldatum,
            mit_uebergabe=False if args.ohne_uebergabe else None, ueberschreiben=args.ueberschreiben)
        ziel, r, hinweise = erg.docx, erg.rechnung, erg.hinweise
    except DateiExistiert as e:
        print(f"FEHLER: {e}" + ("" if e.fremd else " Ersetzen mit --ueberschreiben."), file=sys.stderr)
        return 3
    except (AbFehler, wb.WoerterbuchFehler) as e:
        print(f"FEHLER (Beleg): {e}", file=sys.stderr)
        return 2
    a = r.auftrag
    print(f"Rechnung {r.nr} → {ziel}" + (f" (+ {erg.pdf.name})" if erg.pdf else ""))
    print(f"  Quelle:     {a.quelle}")
    print(f"  Kunde:      {a.kunde}, {a.strasse}, {a.plz_ort}" + (f" (z. Hd. {a.kontakt})" if a.kontakt else ""))
    print(f"  Beleg:      {a.ab_nr or a.rechnungsnr} vom {fmt_datum(a.ab_datum)}, Kunden-Nr. {a.kunden_nr or '—'}, "
          f"Bestellung {r.bestellung or '—'}")
    print(f"  Leistung:   {a.projekt}, Zeitraum {r.zeitraum}")
    print(f"  Beträge:    netto {fmt_betrag(r.netto)} + USt {fmt_betrag(r.ust)} = {fmt_betrag(r.brutto)}")
    print(f"  Zahlbar bis {fmt_datum(r.faellig)} ({a.zahlungsziel_tage} Tage)")
    if a.rechnungs_mail:
        print(f"  Versand an: {a.rechnungs_mail}")
    for h in hinweise:
        print(f"  Hinweis:    {h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
