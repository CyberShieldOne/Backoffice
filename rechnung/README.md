# Rechnung aus Auftragsbestätigung

Füllt `vorlagen/2026-OKT_CS-Rechnung_Vorlage.dotx` mit den Daten einer CS-Auftragsbestätigung (PDF).

```
pip install -r requirements.txt
python rechnung_aus_ab.py AB.pdf --rechnungsnr 2026-0142 [--datum 01.10.2026] [--pdf]
```

| Vorlage | Quelle |
|---|---|
| Kunde, Ansprechpartner, Straße, PLZ Ort | AB-Kopf `Kunde`, `Kundenkontakt`, `Anschrift` |
| Leistungsbezeichnung | AB `Projekt` |
| Auftragsbestätigung, Kundennummer, Bestell-Nr. | AB-Titel, `Kunden-Nr.`, `Ihre Bestellung …` |
| Positionen (Titel, Beschreibung, Menge, Betrag) | Produkttabelle + `Leistungsbeschreibung`, mehrere Positionen → mehrere Zeilen |
| Netto / USt 19 % / Brutto | `Summe Auftrag (Netto)` (muss = Summe der Positionen sein) |
| Zahlbar bis, „N Tage netto“ | Rechnungsdatum + AB `Zahlungsziel` |
| Rechnungs-Nr., Rechnungsdatum | `--rechnungsnr`, `--datum` (Standard heute) |
| Leistungszeitraum | `--von`/`--bis`, Standard Bestelldatum → Liefertermin |
| Übergabe Bericht/Roadmap | `--uebergabe`, Standard = Leistungsende |
| USt-IdNr. Empfänger, Angebot | `--ust-id`, `--angebot` (fehlen in der AB; ohne Angabe „—“ bzw. Satzteil entfällt) |

Das Skript bricht ab, wenn ein Pflichtfeld fehlt, die Positionssumme nicht zur AB-Summe passt
oder nach dem Füllen noch ein `[…]`-Platzhalter im Dokument steht.

Test: `python tests/test_rechnung.py --iterationen 10 --je 6 --echt AB.pdf --pdf`
