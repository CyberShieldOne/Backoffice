"""Leser für SAP-Ariba-Rechnungen ("Standardrechnung", z. B. Infineon Supplier Portal).

Liefert dasselbe Auftrag-Objekt wie der AB-Leser, zusätzlich Rechnungsnummer,
Rechnungsdatum, Leistungszeitraum und USt-IdNr. des Kunden aus dem Beleg.

SAP Ariba gibt dieselbe Rechnung in (mindestens) zwei Formen als PDF aus:
- **Kopie** ("Dies ist eine von Menschen lesbare Darstellung (Kopie)…", Apache FOP, 748×1159 pt,
  Merkmal "Lieferantenreferenznr."). Grundlage: RE-2026-09-30-05.
- **Druckansicht** aus dem Portal (US-Letter, Seitenzähler "1/4", "Ursprünglicher Bestellauftrag",
  "RECHNUNGSANSCHRIFT:"). Grundlage: RE-2026-09-30-03. Enthält kein Bestelldatum.

Beide werden mit demselben Code gelesen: Alle Bezeichnungen stehen in woerterbuch.json ("ariba"),
Spalten werden über die Tabellenköpfe gefunden, Adressen über den Block "Rechnungsanschrift".
Weicht eine weitere Ausgabe nur in der Formulierung ab, genügt ein Eintrag im Wörterbuch.
"""
from __future__ import annotations

import re
from decimal import Decimal

import rechnung_aus_ab as ra
import woerterbuch as wb

QUELLE = "SAP Ariba"
SEITENZAEHLER = re.compile(r"Page\s+\d+|\d+\s*/\s*\d+")
DATUM = r"(\d{1,2}\.\s*[A-Za-zÄÖÜäöü]+\.?\s*\d{4}|\d{1,2}\.\d{1,2}\.\d{2,4})"


def variante(text: str) -> str | None:
    """'kopie', 'druck' oder None (keine SAP-Ariba-Rechnung) – am Text der ersten Seite."""
    if wb.erkannt(text, "ariba_kopie"):
        return "kopie"
    if wb.erkannt(text, "ariba_druck"):
        return "druck"
    return None


def ist_ariba(text: str) -> bool:
    return variante(text) is not None


def _feld(text: str, feld: str, wert: str, pflicht: bool = False, naechste_zeile: bool = False) -> str:
    m = wb.suche(text, ("ariba", feld), wert, naechste_zeile=naechste_zeile)
    if not m:
        if pflicht:
            raise ra.AbFehler("SAP-Rechnung: " + wb.nicht_gefunden("ariba", feld))
        return ""
    return m.group(1).strip()


def _betrag_eur(text: str) -> Decimal:
    return ra.parse_betrag(text.replace("EUR", ""))


def _spalte(zeilen, x_von: float, x_bis: float, y_von: float, y_bis: float) -> list[str]:
    """Textzeilen eines Spaltenbereichs (Wörter mit x0 in [x_von, x_bis))."""
    aus = []
    for z in zeilen:
        if not (y_von < z[0]["top"] < y_bis):
            continue
        t = ra._text([w for w in z if x_von <= w["x0"] < x_bis])
        if t:
            aus.append(t)
    return aus


def _beschreibung_teilen(text: str) -> tuple[str, str]:
    """'Not Available / Cyber Shield – Consulting Services for X - Includes …'
    -> Titel 'Consulting Services for X', Beschreibung 'Includes … · …'."""
    t = re.sub(r"\s+", " ", text).strip()
    t = re.sub(r"^Not Available\s*/?\s*", "", t)         # Teilenr. des Lieferanten fehlt
    t = re.sub(r"^Cyber Shield\s*[–-]\s*", "", t)          # eigener Firmenname als Präfix
    teile = [p.strip() for p in re.split(r"\s+-\s+", t) if p.strip()]
    if not teile:
        raise ra.AbFehler("SAP-Rechnung: Positionsbeschreibung leer")
    return teile[0], " · ".join(teile[1:])


def _woerter(pdf) -> tuple[list[dict], str]:
    """Wörter aller Seiten untereinander (Positionen können umbrechen), ohne Seitenzähler."""
    words, y_off = [], 0.0
    for seite in pdf.pages:
        for zeile in ra._zeilen(seite.extract_words()):
            # Nur ganze Zeilen entfernen, die exakt ein Seitenzähler sind ("Page 2", "2/4") – nie einzelne
            # Zahlen: Eine Positionszeile am Seitenende behält Menge und Positionsnummer.
            if zeile[0]["top"] > seite.height * 0.9 and SEITENZAEHLER.fullmatch(ra._text(zeile)):
                continue
            for w in zeile:
                w = dict(w)
                w["top"] += y_off
                words.append(w)
        y_off += float(seite.height)
    return words, "\n".join(p.extract_text() or "" for p in pdf.pages)


def _pruefe_betraege(text: str, netto: Decimal, steuer: Decimal, faellig: Decimal) -> None:
    saetze = set(re.findall(r"Umsatzsteuer\s+(?:Position\s+.*?)?(?:[\d.,]+ EUR\s+)?(\d+(?:,\d+)?) %", text))
    if saetze and saetze != {str(ra.UST_SATZ)}:
        raise ra.AbFehler(f"SAP-Rechnung mit Steuersatz {', '.join(sorted(saetze))} % – unterstützt ist nur {ra.UST_SATZ} %")
    if ra.ust_von(netto) != steuer or netto + steuer != faellig:
        raise ra.AbFehler(f"SAP-Rechnung: Beträge passen nicht zusammen (netto {netto}, Steuer {steuer}, fällig {faellig})")


def _menge(text: str) -> str:
    return re.sub(r"\s*/\s*\(.*?\)", "", text).strip() or "1"


def _adresse(zeilen: list[str]) -> tuple[str, str, str]:
    """Adressblock beider Ausgaben: Name, [Postanschrift…:], Straße, ('PLZ Ort' | Ort, [Region], PLZ),
    Land, [Adressen-ID]."""
    zeilen = [z for z in zeilen if not re.match(r"(?i)^(Postanschrift|Adressen-ID)", z)]
    if len(zeilen) < 3:
        raise ra.AbFehler(f"SAP-Rechnung: Rechnungsanschrift unvollständig: {zeilen}")
    name, strasse = zeilen[0], zeilen[1]
    plz_ort = next((z for z in zeilen[2:] if re.match(r"^\d{4,5}\s+\S", z)), "")
    if not plz_ort:   # Kopie: Ort und PLZ in getrennten Zeilen
        plz = next((z for z in zeilen[3:] if re.fullmatch(r"\d{4,5}", z)), "")
        if plz:
            plz_ort = f"{plz} {zeilen[2]}"
    if not plz_ort:
        raise ra.AbFehler(f"SAP-Rechnung: keine Postleitzahl in der Rechnungsanschrift: {zeilen}")
    return name, strasse, plz_ort


def _anschrift(zeilen) -> tuple[str, str, str]:
    """Spalte unter 'Rechnungsanschrift' bis zur nächsten Spalte rechts davon."""
    for z in zeilen:
        links = next((w for w in z if wb.wort_passt(w["text"], "ariba", "kopf_anschrift", "rechnungsanschrift")), None)
        if links is None:
            continue
        rechts = [w["x0"] for w in z if w["x0"] > links["x0"]
                  and wb.wort_passt(w["text"], "ariba", "kopf_anschrift", "rechts_davon")]
        x_bis = min(rechts) if rechts else 1e9
        ende = next((y[0]["top"] for y in zeilen if y[0]["top"] > z[0]["top"]
                     and wb.beginnt_mit(ra._text(y), "ariba", "anschrift_ende")), 1e9)
        return _adresse(_spalte(zeilen, links["x0"] - 2, x_bis - 2, z[0]["top"] + 2, ende))
    raise ra.AbFehler("SAP-Rechnung: Block 'Rechnungsanschrift' nicht gefunden")


def _positionen_roh(zeilen, zt) -> list[dict]:
    """Positionstabelle beider Ausgaben. Kopfwörter aus dem Wörterbuch ('ariba/tabelle').
    Neue Position = Zeile mit Menge und Beträgen; Beschreibung = Spalte 'Teilenr./Beschreibung'
    bis zu Details/Servicedetails; Tabelle endet an Steuer-/Rechnungsübersicht."""
    def wort(z, name):
        return next((w for w in z if wb.wort_passt(w["text"], "ariba", "tabelle", name)), None)

    pk = next((z for z in zeilen if wort(z, "position") and wort(z, "menge") and wort(z, "zwischensumme")), None)
    if pk is None:
        raise ra.AbFehler("SAP-Rechnung: Positionstabelle nicht gefunden")
    x_menge, x_preis = wort(pk, "menge")["x0"], (wort(pk, "preis") or wort(pk, "zwischensumme"))["x0"]
    beschr_w = [w["x0"] for w in pk if wb.wort_passt(w["text"], "ariba", "tabelle", "beschreibung")]
    if not beschr_w:
        raise ra.AbFehler("SAP-Rechnung: Spalte 'Beschreibung' in der Positionstabelle nicht gefunden")
    x_beschr = beschr_w[0]
    # zweite Beschreibungsspalte (Kopie: "Teilenr. des Kunden") begrenzt die erste; "Teilenr. / Beschreibung"
    # (Druckansicht) steht dicht beieinander und zählt als eine Spalte
    x_beschr_ende = next((x for x in beschr_w[1:] if x > x_beschr + 50 and x < x_menge), x_menge)

    roh, akt, sammeln = [], None, False
    for z, t in zip(zeilen, zt):
        if z[0]["top"] <= pk[0]["top"] + 5:
            continue
        if wb.beginnt_mit(t, "ariba", "tabelle_ende"):
            break
        if wb.beginnt_mit(t, "ariba", "position_details"):
            sammeln = False
            continue
        menge = ra._text([w for w in z if x_menge - 2 <= w["x0"] < x_preis - 5])
        betraege = re.findall(r"[\d.]+,\d{2} EUR", ra._text([w for w in z if w["x0"] >= x_preis - 20]))
        beschr = ra._text([w for w in z if x_beschr - 2 <= w["x0"] < x_beschr_ende - 2])
        # Mengenspalte: Beträge, die in die Spalte ragen (Druckansicht), abziehen; übrig bleiben muss
        # eine Menge ("1", "1,5", "8 / (Monate)") – Steuer-/Summenzeilen ("19 %", "Steuer:") fallen raus
        menge = re.sub(r"[\d.]+,\d{2}(?:\s*EUR)?", "", menge).strip()
        ist_menge = re.fullmatch(r"\d+(?:,\d+)?(?:\s*/\s*\(?[^)]*\)?)?", menge) is not None
        if betraege and ist_menge:                               # neue Position
            akt = {"beschr": [beschr] if beschr else [], "menge": _menge(menge),
                   "betrag": _betrag_eur(betraege[-1])}
            roh.append(akt)
            sammeln = True
        elif sammeln and akt is not None and beschr:
            akt["beschr"].append(beschr)
    return roh


def _positionen(roh: list[dict], netto: Decimal) -> list[ra.Position]:
    if not roh:
        raise ra.AbFehler("SAP-Rechnung: keine Position gefunden")
    pos = []
    for r in roh:
        titel, beschreibung = _beschreibung_teilen(" ".join(r["beschr"]))
        pos.append(ra.Position(titel=titel, produkt="", einheit="", menge=r["menge"],
                               betrag=r["betrag"], beschreibung=beschreibung))
    if sum((x.betrag for x in pos), Decimal("0")) != netto:
        raise ra.AbFehler("SAP-Rechnung: Summe der Positionen ≠ Gesamtbetrag ohne Steuern")
    return pos


def lese_ariba(pdf_pfad, pdf=None) -> ra.Auftrag:
    with ra.oeffne_pdf(pdf_pfad, pdf) as pdf:
        erste = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
        words, text = _woerter(pdf)
    if variante(erste) is None:
        raise ra.AbFehler("SAP-Rechnung: Ausgabeform nicht erkannt")
    zeilen = ra._zeilen(words)
    zt = [ra._text(z) for z in zeilen]

    nr = _feld(text, "rechnungsnummer", r"(\S+)", pflicht=True)
    rdatum = _feld(text, "rechnungsdatum", r"(?:[A-Za-zÄÖÜäöü]+,\s*)?" + DATUM)
    if not rdatum:   # Druckansicht: Datum steht unter der Nummer, über der Beschriftung "Rechnungsdatum:"
        m = re.search(wb.rx("ariba", "rechnungsnummer") + r"\s*:?\s*\S+\s+(?:[A-Za-zÄÖÜäöü]+,\s*)?" + DATUM, text)
        rdatum = m.group(1) if m else ""
    if not rdatum:
        raise ra.AbFehler("SAP-Rechnung: Rechnungsdatum nicht gefunden")
    von = _feld(text, "leistung_von", DATUM, naechste_zeile=True)
    bis = _feld(text, "leistung_bis", DATUM, naechste_zeile=True)
    po = _feld(text, "bestellnummer", r"(\S*\d\S*)")
    po_datum = _feld(text, "bestelldatum", DATUM)
    tage = int(_feld(text, "zahlungsziel_tage", r"(\d+)", pflicht=True, naechste_zeile=True))
    netto = _betrag_eur(_feld(text, "netto", r"([\d.,]+ EUR)", pflicht=True))
    steuer = _betrag_eur(_feld(text, "steuer", r"([\d.,]+ EUR)", pflicht=True))
    faellig = _betrag_eur(_feld(text, "faellig", r"([\d.,]+ EUR)", pflicht=True))
    ust_kunde = _feld(text, "ust_kunde", r"([A-Z]{2}\d{8,12})")
    if not ust_kunde:   # Kopie: zweite Kennung in der Zeile unter "Umsatzsteuer-/Steuernummer"
        ust_kunde = next((ids[1] for t in zt if len(ids := re.findall(r"\b[A-Z]{2}\d{8,12}\b", t)) >= 2), "")

    _pruefe_betraege(text, netto, steuer, faellig)
    kunde, strasse, plz_ort = _anschrift(zeilen)
    pos = _positionen(_positionen_roh(zeilen, zt), netto)
    projekt = pos[0].titel if len(pos) == 1 else "; ".join(x.titel for x in pos)
    hinweise = [ra.Hinweis("Original wurde elektronisch über SAP Ariba eingereicht – Rechnungsnummer "
                           "unverändert lassen, sonst droht Doppelzahlung")]
    if po and not po_datum:
        hinweise.append(ra.Hinweis("Bestelldatum steht nicht in dieser SAP-Ausgabe – bei Bedarf selbst angeben",
                                   "--bestelldatum"))
    return ra.Auftrag(
        ab_nr="", ab_datum=ra.parse_datum(rdatum), kunde=kunde, strasse=strasse, plz_ort=plz_ort,
        kunden_nr="", kontakt="", projekt=projekt, summe_netto=netto, zahlungsziel_tage=tage,
        bestell_nr=po, bestell_datum=ra.parse_datum(po_datum) if po_datum else None,
        liefertermin=None, rechnungs_mail="", positionen=pos, hinweise=hinweise, quelle=QUELLE,
        rechnungsnr=nr, rechnungsdatum=ra.parse_datum(rdatum),
        leistung_von=ra.parse_datum(von) if von else None,
        leistung_bis=ra.parse_datum(bis) if bis else None,
        ust_id_kunde=ust_kunde,
    )
