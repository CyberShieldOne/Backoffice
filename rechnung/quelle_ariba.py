"""Leser für SAP-Ariba-Rechnungen ("Standardrechnung", z. B. Infineon Supplier Portal).

Liefert dasselbe Auftrag-Objekt wie der AB-Leser, zusätzlich Rechnungsnummer,
Rechnungsdatum, Leistungszeitraum und USt-IdNr. des Kunden aus dem Beleg.

SAP Ariba gibt dieselbe Rechnung in zwei Formen als PDF aus:
- **Kopie** ("Dies ist eine von Menschen lesbare Darstellung (Kopie)…", Apache FOP, Hochformat 748×1159 pt,
  Merkmal "Lieferantenreferenznr."). Grundlage: RE-2026-09-30-05.
- **Druckansicht** aus dem Portal (US-Letter, Seitenzähler "1/4", Merkmale "Ursprünglicher Bestellauftrag",
  "RECHNUNGSANSCHRIFT:"). Grundlage: RE-2026-09-30-03. Enthält kein Bestelldatum.
"""
from __future__ import annotations

import re
from decimal import Decimal

import rechnung_aus_ab as ra

QUELLE = "SAP Ariba"
SEITENZAEHLER = re.compile(r"Page\s+\d+|\d+\s*/\s*\d+")


def variante(text: str) -> str | None:
    """'kopie', 'druck' oder None (keine SAP-Ariba-Rechnung) – am Text der ersten Seite."""
    if "Standardrechnung" not in text:
        return None
    if "Lieferantenreferenznr" in text:
        return "kopie"
    if "Ursprünglicher Bestellauftrag" in text or "RECHNUNGSANSCHRIFT" in text:
        return "druck"
    return None


def ist_ariba(text: str) -> bool:
    return variante(text) is not None


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


def _menge(text: str) -> str:
    return re.sub(r"\s*/\s*\(.*?\)", "", text).strip() or "1"


def lese_ariba(pdf_pfad, pdf=None) -> ra.Auftrag:
    with ra.oeffne_pdf(pdf_pfad, pdf) as pdf:
        erste = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
        words, text = _woerter(pdf)
    art = variante(erste)
    if art is None:
        raise ra.AbFehler("SAP-Rechnung: Ausgabeform nicht erkannt")
    zeilen = ra._zeilen(words)
    f = (_lese_kopie if art == "kopie" else _lese_druck)(zeilen, [ra._text(z) for z in zeilen], text)

    _pruefe_betraege(text, f["netto"], f["steuer"], f["faellig"])
    pos = _positionen(f["positionen"], f["netto"])
    projekt = pos[0].titel if len(pos) == 1 else "; ".join(x.titel for x in pos)
    hinweise = [ra.Hinweis("Original wurde elektronisch über SAP Ariba eingereicht – Rechnungsnummer "
                           "unverändert lassen, sonst droht Doppelzahlung")]
    po_datum = None
    if f["po_datum"]:
        po_datum = ra.parse_datum(f["po_datum"])
    elif f["po"]:
        hinweise.append(ra.Hinweis("Bestelldatum steht nicht in dieser SAP-Ausgabe – bei Bedarf selbst angeben",
                                   "--bestelldatum"))
    return ra.Auftrag(
        ab_nr="", ab_datum=f["rdatum"], kunde=f["kunde"], strasse=f["strasse"], plz_ort=f["plz_ort"],
        kunden_nr="", kontakt="", projekt=projekt, summe_netto=f["netto"], zahlungsziel_tage=f["tage"],
        bestell_nr=f["po"], bestell_datum=po_datum, liefertermin=None, rechnungs_mail="",
        positionen=pos, hinweise=hinweise, quelle=QUELLE, rechnungsnr=f["nr"], rechnungsdatum=f["rdatum"],
        leistung_von=ra.parse_datum(f["von"]) if f["von"] else None,
        leistung_bis=ra.parse_datum(f["bis"]) if f["bis"] else None,
        ust_id_kunde=f["ust_kunde"],
    )


# --------------------------------------------------------------------------
# Ausgabeform "Kopie" (RE-2026-09-30-05)
# --------------------------------------------------------------------------

def _adresse_kopie(zeilen: list[str]) -> tuple[str, str, str]:
    """Name, Straße, Ort, [Region], PLZ, Land, [Adressen-ID]."""
    zeilen = [z for z in zeilen if not z.startswith("Adressen-ID")]
    if len(zeilen) < 4:
        raise ra.AbFehler(f"SAP-Rechnung: Rechnungsanschrift unvollständig: {zeilen}")
    name, strasse, ort = zeilen[0], zeilen[1], zeilen[2]
    plz = next((z for z in zeilen[3:] if re.fullmatch(r"\d{4,5}", z)), "")
    if not plz:
        raise ra.AbFehler(f"SAP-Rechnung: keine Postleitzahl in der Rechnungsanschrift: {zeilen}")
    return name, strasse, f"{plz} {ort}"


def _lese_kopie(zeilen, zt, text) -> dict:
    f = {
        "nr": _wert(text, r"Rechnungsnummer\s+(\S+)", "Rechnungsnummer"),
        "rdatum": ra.parse_datum(_wert(text, r"Rechnungsdatum\s+(?:\w+,\s*)?([^\n]*?\d{4})", "Rechnungsdatum")),
        "von": _wert(text, r"Service-Startdatum\s+([^\n]+)"),
        "bis": _wert(text, r"Service-Enddatum\s+([^\n]+)"),
        "po": _wert(text, r"Bestellauftragsnr\.\s*:\s*(\S+)"),
        "po_datum": _wert(text, r"Bestellauftragsdatum:\s*([^\n]+)"),
        "tage": int(_wert(text, r"Nettozahlungsbedingungen \(Tage\)\s*:\s*\n?\s*(\d+)",
                          "Zahlungsziel (Nettozahlungsbedingungen)")),
        "netto": _betrag_eur(_wert(text, r"Gesamtbetrag ohne Steuern\s+([\d.,]+ EUR)", "Gesamtbetrag ohne Steuern")),
        "steuer": _betrag_eur(_wert(text, r"Gesamtbetrag der Steuern\s+([\d.,]+ EUR)", "Gesamtbetrag der Steuern")),
        "faellig": _betrag_eur(_wert(text, r"Fälliger Betrag\s+([\d.,]+ EUR)", "Fälliger Betrag")),
        "ust_kunde": "",
    }
    # USt-IdNr. des Kunden: zweite Kennung in der Zeile unter "Umsatzsteuer-/Steuernummer"
    for t in zt:
        ids = re.findall(r"\b[A-Z]{2}\d{8,12}\b", t)
        if len(ids) >= 2:
            f["ust_kunde"] = ids[1]
            break

    # Rechnungsanschrift (Abrechnungsinformationen): Spalte zwischen "Rechnungsanschrift" und "Zahlungsempfänger"
    kopf = next((z for z in zeilen if {"Rechnungsanschrift", "Zahlungsempfänger"} <= {w["text"] for w in z}), None)
    if kopf is None:
        raise ra.AbFehler("SAP-Rechnung: Block 'Rechnungsanschrift' nicht gefunden")
    k = {w["text"]: w for w in kopf}
    ende = next((z[0]["top"] for z in zeilen if ra._text(z).startswith("Zahlungsbedingungen")), 1e9)
    f["kunde"], f["strasse"], f["plz_ort"] = _adresse_kopie(_spalte(
        zeilen, k["Rechnungsanschrift"]["x0"] - 2, k["Zahlungsempfänger"]["x0"] - 2, kopf[0]["top"] + 2, ende))

    # Positionen: Tabellenkopf "Positionsnr. … Menge … Preis … Zwischensumme"
    pk = next((z for z in zeilen if {"Positionsnr.", "Menge", "Zwischensumme"} <= {w["text"] for w in z}), None)
    if pk is None:
        raise ra.AbFehler("SAP-Rechnung: Positionstabelle nicht gefunden")
    p = {w["text"]: w for w in pk}
    x_beschr = next(w["x0"] for w in pk if w["text"] == "Teilenr.")
    x_kunde_tnr = [w["x0"] for w in pk if w["text"] == "Teilenr."][1]
    x_menge, x_preis = p["Menge"]["x0"], p["Preis"]["x0"]
    roh, akt = [], None
    for z, t in zip(zeilen, zt):
        if z[0]["top"] <= pk[0]["top"] + 12:
            continue
        if t.startswith(("Servicedetails", "Steuern,", "Steuerübersicht")):
            if akt:
                roh.append(akt)
                akt = None
            if t.startswith("Steuerübersicht"):
                break
            continue
        menge = ra._text([w for w in z if x_menge - 2 <= w["x0"] < x_preis - 20])
        betraege = re.findall(r"[\d.]+,\d{2} EUR", ra._text([w for w in z if w["x0"] >= x_preis - 20]))
        beschr = ra._text([w for w in z if x_beschr - 2 <= w["x0"] < x_kunde_tnr - 2])
        if re.match(r"^\d+\s", t) and betraege:          # neue Position
            if akt:
                roh.append(akt)
            akt = {"beschr": [beschr], "menge": _menge(menge), "betrag": _betrag_eur(betraege[-1])}
        elif akt is not None and beschr:
            akt["beschr"].append(beschr)
    if akt:
        roh.append(akt)
    f["positionen"] = roh
    return f


# --------------------------------------------------------------------------
# Ausgabeform "Druckansicht" (RE-2026-09-30-03)
# --------------------------------------------------------------------------

def _adresse_druck(zeilen: list[str]) -> tuple[str, str, str]:
    """Name, 'Postanschrift …:', Straße, 'PLZ Ort', [Region], Land, [Adressen-ID]."""
    zeilen = [z for z in zeilen if not z.startswith(("Postanschrift", "Adressen-ID"))]
    if len(zeilen) < 3:
        raise ra.AbFehler(f"SAP-Rechnung: Rechnungsanschrift unvollständig: {zeilen}")
    name, strasse = zeilen[0], zeilen[1]
    plz_ort = next((z for z in zeilen[2:] if re.match(r"^\d{4,5}\s+\S", z)), "")
    if not plz_ort:
        raise ra.AbFehler(f"SAP-Rechnung: keine Postleitzahl in der Rechnungsanschrift: {zeilen}")
    return name, strasse, plz_ort


def _lese_druck(zeilen, zt, text) -> dict:
    datum = r"(\d{1,2}\.\s*[A-Za-zÄÖÜäöü]+\.?\s*\d{4})"
    f = {
        "nr": _wert(text, r"Rechnungsnummer:?\s*(\S+)", "Rechnungsnummer"),
        # Datum steht in der Druckansicht direkt unter der Nummer (über der Beschriftung "Rechnungsdatum:")
        "rdatum": ra.parse_datum(_wert(text, r"Rechnungsnummer:?\s*\S+\s+(?:[A-Za-zÄÖÜäöü]+,\s*)?" + datum,
                                       "Rechnungsdatum")),
        "von": _wert(text, r"Leistungszeitraum\s+Startdatum:\s*" + datum),
        "bis": _wert(text, r"Leistungszeitraum\s+Startdatum:\s*[^\n]*\n?\s*Enddatum:\s*" + datum),
        "po": _wert(text, r"Ursprünglicher Bestellauftrag:\s*(\S+)"),
        "po_datum": "",
        "tage": int(_wert(text, r"Nettobedingung:\s*(\d+)\s*Tage", "Zahlungsziel (Nettobedingung)")),
        "netto": _betrag_eur(_wert(text, r"Gesamtbetrag ohne Steuern:\s*([\d.,]+ EUR)", "Gesamtbetrag ohne Steuern")),
        "steuer": _betrag_eur(_wert(text, r"Steuern insgesamt:\s*([\d.,]+ EUR)", "Steuern insgesamt")),
        "faellig": _betrag_eur(_wert(text, r"Fälliger Betrag:\s*([\d.,]+ EUR)", "Fälliger Betrag")),
        "ust_kunde": _wert(text, r"Umsatzsteuer-/Steuernummer des Kunden:\s*([A-Z]{2}\d{8,12})"),
    }
    # Rechnungsanschrift: Spalte zwischen "RECHNUNGSANSCHRIFT:" und "LIEFERANT:"
    kopf = next((z for z in zeilen if {"RECHNUNGSANSCHRIFT:", "LIEFERANT:"} <= {w["text"] for w in z}), None)
    if kopf is None:
        raise ra.AbFehler("SAP-Rechnung: Block 'RECHNUNGSANSCHRIFT' nicht gefunden")
    k = {w["text"]: w for w in kopf}
    ende = next((z[0]["top"] for z in zeilen if z[0]["top"] > kopf[0]["top"]
                 and ra._text(z).startswith(("RECHNUNGSABSENDER", "VERSANDINFORMATIONEN", "Steuernummer des"))), 1e9)
    f["kunde"], f["strasse"], f["plz_ort"] = _adresse_druck(_spalte(
        zeilen, k["RECHNUNGSANSCHRIFT:"]["x0"] - 2, k["LIEFERANT:"]["x0"] - 2, kopf[0]["top"] + 2, ende))

    # Positionen: Kopf "Positionsnr. … Teilenr. / Beschreibung … Menge / Einheit … Preis … Zwischensumme".
    # Je Position: Zeile mit Beträgen, darunter die Beschreibung (Spalte "Teilenr. / Beschreibung") bis "DETAILS".
    pk = next((z for z in zeilen if {"Positionsnr.", "Menge", "Zwischensumme"} <= {w["text"] for w in z}), None)
    if pk is None:
        raise ra.AbFehler("SAP-Rechnung: Positionstabelle nicht gefunden")
    p = {w["text"]: w for w in pk}
    x_beschr, x_menge, x_preis = p["Teilenr."]["x0"], p["Menge"]["x0"], p["Preis"]["x0"]
    roh, akt, sammeln = [], None, False
    for z, t in zip(zeilen, zt):
        if z[0]["top"] <= pk[0]["top"] + 5:
            continue
        if t.startswith(("Steuerübersicht", "Rechnungsübersicht")):
            break
        betraege = re.findall(r"[\d.]+,\d{2} EUR", ra._text([w for w in z if w["x0"] >= x_preis - 5]))
        beschr = ra._text([w for w in z if x_beschr - 2 <= w["x0"] < x_menge - 2])
        if len(betraege) >= 2 and ra._text([w for w in z if x_menge - 2 <= w["x0"] < x_preis - 5]):
            akt = {"beschr": [beschr] if beschr else [],
                   "menge": _menge(ra._text([w for w in z if x_menge - 2 <= w["x0"] < x_preis - 5])),
                   "betrag": _betrag_eur(betraege[-1])}
            roh.append(akt)
            sammeln = True
            continue
        if t.startswith("DETAILS"):
            sammeln = False
            continue
        if sammeln and akt is not None and beschr:
            akt["beschr"].append(beschr)
    f["positionen"] = roh
    return f
