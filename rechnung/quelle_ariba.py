"""Leser für SAP-Ariba-Rechnungen ("Standardrechnung", z. B. Infineon Supplier Portal).

Liefert dasselbe Auftrag-Objekt wie der AB-Leser, zusätzlich Rechnungsnummer,
Rechnungsdatum, Leistungszeitraum und USt-IdNr. des Kunden aus dem Beleg.
Layout-Grundlage: RE-2026-09-30-05 (Apache-FOP-PDF aus SAP Ariba, 2 Seiten).
"""
from __future__ import annotations

import re
from decimal import Decimal

import rechnung_aus_ab as ra


def ist_ariba(text: str) -> bool:
    return "Standardrechnung" in text and "Lieferantenreferenznr" in text


def _wert(text: str, muster: str, pflicht: str | None = None) -> str:
    m = re.search(muster, text)
    if not m:
        if pflicht:
            raise ra.AbFehler(f"SAP-Rechnung: {pflicht} nicht gefunden")
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


def _adresse(zeilen: list[str]) -> tuple[str, str, str]:
    """Ariba-Adressblock: Name, Straße, Ort, [Region], PLZ, Land, [Adressen-ID]."""
    zeilen = [z for z in zeilen if not z.startswith("Adressen-ID")]
    if len(zeilen) < 4:
        raise ra.AbFehler(f"SAP-Rechnung: Rechnungsanschrift unvollständig: {zeilen}")
    name, strasse, ort = zeilen[0], zeilen[1], zeilen[2]
    plz = next((z for z in zeilen[3:] if re.fullmatch(r"\d{4,5}", z)), "")
    if not plz:
        raise ra.AbFehler(f"SAP-Rechnung: keine Postleitzahl in der Rechnungsanschrift: {zeilen}")
    return name, strasse, f"{plz} {ort}"


def _beschreibung_teilen(text: str) -> tuple[str, str]:
    """'Not Available / Cyber Shield – Consulting Services for X - Includes …'
    -> Titel 'Consulting Services for X', Beschreibung 'Includes …; …'."""
    t = re.sub(r"\s+", " ", text).strip()
    t = re.sub(r"^Not Available\s*/\s*", "", t)          # Teilenr. des Lieferanten fehlt
    t = re.sub(r"^Cyber Shield\s*[–-]\s*", "", t)          # eigener Firmenname als Präfix
    teile = [p.strip() for p in re.split(r"\s+-\s+", t) if p.strip()]
    if not teile:
        raise ra.AbFehler("SAP-Rechnung: Positionsbeschreibung leer")
    return teile[0], " · ".join(teile[1:])


def lese_ariba(pdf_pfad) -> ra.Auftrag:
    import pdfplumber

    with pdfplumber.open(str(pdf_pfad)) as pdf:
        words, y_off = [], 0.0
        for seite in pdf.pages:   # alle Seiten untereinander (Positionen können umbrechen)
            for w in seite.extract_words():
                if re.fullmatch(r"Page|\d+", w["text"]) and w["top"] > seite.height * 0.95:
                    continue      # Seitenzahl "Page N"
                w = dict(w)
                w["top"] += y_off
                words.append(w)
            y_off += float(seite.height)
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    zeilen = ra._zeilen(words)
    zt = [ra._text(z) for z in zeilen]

    nr = _wert(text, r"Rechnungsnummer\s+(\S+)", "Rechnungsnummer")
    rdatum = ra.parse_datum(_wert(text, r"Rechnungsdatum\s+(?:\w+,\s*)?([^\n]*?\d{4})", "Rechnungsdatum"))
    von = _wert(text, r"Service-Startdatum\s+([^\n]+)")
    bis = _wert(text, r"Service-Enddatum\s+([^\n]+)")
    po = _wert(text, r"Bestellauftragsnr\.\s*:\s*(\S+)")
    po_datum = _wert(text, r"Bestellauftragsdatum:\s*([^\n]+)")
    tage = int(_wert(text, r"Nettozahlungsbedingungen \(Tage\)\s*:\s*\n?\s*(\d+)", "Zahlungsziel (Nettozahlungsbedingungen)"))
    netto = _betrag_eur(_wert(text, r"Gesamtbetrag ohne Steuern\s+([\d.,]+ EUR)", "Gesamtbetrag ohne Steuern"))
    steuer = _betrag_eur(_wert(text, r"Gesamtbetrag der Steuern\s+([\d.,]+ EUR)", "Gesamtbetrag der Steuern"))
    faellig = _betrag_eur(_wert(text, r"Fälliger Betrag\s+([\d.,]+ EUR)", "Fälliger Betrag"))
    saetze = set(re.findall(r"Umsatzsteuer\s+(?:Position\s+.*?)?[\d.,]+ EUR\s+(\d+(?:,\d+)?) %", text))
    if saetze and saetze != {str(ra.UST_SATZ)}:
        raise ra.AbFehler(f"SAP-Rechnung mit Steuersatz {', '.join(sorted(saetze))} % – unterstützt ist nur {ra.UST_SATZ} %")
    if ra.ust_von(netto) != steuer or netto + steuer != faellig:
        raise ra.AbFehler(f"SAP-Rechnung: Beträge passen nicht zusammen (netto {netto}, Steuer {steuer}, fällig {faellig})")

    # USt-IdNr. des Kunden: zweite Kennung in der Zeile unter "Umsatzsteuer-/Steuernummer"
    ust_kunde = ""
    for t in zt:
        ids = re.findall(r"\b[A-Z]{2}\d{8,12}\b", t)
        if len(ids) >= 2:
            ust_kunde = ids[1]
            break

    # Rechnungsanschrift (Abrechnungsinformationen): Spalte zwischen "Rechnungsanschrift" und "Zahlungsempfänger"
    kopf = next((z for z in zeilen if {"Rechnungsanschrift", "Zahlungsempfänger"} <= {w["text"] for w in z}), None)
    if kopf is None:
        raise ra.AbFehler("SAP-Rechnung: Block 'Rechnungsanschrift' nicht gefunden")
    k = {w["text"]: w for w in kopf}
    ende = next((z[0]["top"] for z in zeilen if ra._text(z).startswith("Zahlungsbedingungen")), 1e9)
    kunde, strasse, plz_ort = _adresse(_spalte(zeilen, k["Rechnungsanschrift"]["x0"] - 2,
                                               k["Zahlungsempfänger"]["x0"] - 2, kopf[0]["top"] + 2, ende))

    # Positionen: Tabellenkopf "Positionsnr. … Menge … Preis … Zwischensumme"
    pk = next((z for z in zeilen if {"Positionsnr.", "Menge", "Zwischensumme"} <= {w["text"] for w in z}), None)
    if pk is None:
        raise ra.AbFehler("SAP-Rechnung: Positionstabelle nicht gefunden")
    p = {w["text"]: w for w in pk}
    x_beschr = next(w["x0"] for w in pk if w["text"] == "Teilenr.")
    x_kunde_tnr = [w["x0"] for w in pk if w["text"] == "Teilenr."][1]
    x_menge, x_preis = p["Menge"]["x0"], p["Preis"]["x0"]
    positionen: list[ra.Position] = []
    roh = None
    for z, t in zip(zeilen, zt):
        if z[0]["top"] <= pk[0]["top"] + 12:
            continue
        if t.startswith(("Servicedetails", "Steuern,", "Steuerübersicht")):
            if roh:
                positionen.append(roh)
                roh = None
            if t.startswith("Steuerübersicht"):
                break
            continue
        menge = ra._text([w for w in z if x_menge - 2 <= w["x0"] < x_preis - 20])
        betraege = re.findall(r"[\d.]+,\d{2} EUR", ra._text([w for w in z if w["x0"] >= x_preis - 20]))
        beschr = ra._text([w for w in z if x_beschr - 2 <= w["x0"] < x_kunde_tnr - 2])
        if re.match(r"^\d+\s", t) and betraege:          # neue Position
            if roh:
                positionen.append(roh)
            roh = {"beschr": [beschr], "menge": re.sub(r"\s*/\s*\(.*?\)", "", menge).strip() or "1",
                   "betrag": _betrag_eur(betraege[-1])}
        elif roh is not None and beschr:
            roh["beschr"].append(beschr)
    if roh:
        positionen.append(roh)
    if not positionen:
        raise ra.AbFehler("SAP-Rechnung: keine Position gefunden")
    pos_obj = []
    for r in positionen:
        titel, beschreibung = _beschreibung_teilen(" ".join(r["beschr"]))
        pos_obj.append(ra.Position(titel=titel, produkt="", einheit="", menge=r["menge"],
                                   betrag=r["betrag"], beschreibung=beschreibung))
    if sum((x.betrag for x in pos_obj), Decimal("0")) != netto:
        raise ra.AbFehler("SAP-Rechnung: Summe der Positionen ≠ Gesamtbetrag ohne Steuern")

    projekt = pos_obj[0].titel if len(pos_obj) == 1 else "; ".join(x.titel for x in pos_obj)
    return ra.Auftrag(
        ab_nr="", ab_datum=rdatum, kunde=kunde, strasse=strasse, plz_ort=plz_ort,
        kunden_nr="", kontakt="", projekt=projekt, summe_netto=netto, zahlungsziel_tage=tage,
        bestell_nr=po, bestell_datum=ra.parse_datum(po_datum) if po_datum else None,
        liefertermin=None, rechnungs_mail="", positionen=pos_obj,
        hinweise=[ra.Hinweis("Original wurde elektronisch über SAP Ariba eingereicht – Rechnungsnummer "
                             "unverändert lassen, sonst droht Doppelzahlung")],
        quelle="SAP Ariba", rechnungsnr=nr, rechnungsdatum=rdatum,
        leistung_von=ra.parse_datum(von) if von else None,
        leistung_bis=ra.parse_datum(bis) if bis else None,
        ust_id_kunde=ust_kunde,
    )
