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
| Auftragsbestätigung, Kundennummer | AB-Titel, `Kunden-Nr.` |
| Bestellnummer des Kunden (Infoblock, Anschreiben, Verwendungszweck, unter der Anschrift) | `Ihre Bestellung … vom …`, überschreibbar mit `--bestellnr` / `--bestelldatum` (`--bestellnr ""` = keine) |
| Positionen (Titel, Beschreibung, Menge, Betrag) | Produkttabelle + `Leistungsbeschreibung`, mehrere Positionen → mehrere Zeilen |
| Netto / USt 19 % / Brutto | `Summe Auftrag (Netto)` (muss = Summe der Positionen sein) |
| Zahlbar bis, „N Tage netto“ | Rechnungsdatum + AB `Zahlungsziel` |
| Rechnungs-Nr., Rechnungsdatum | `--rechnungsnr`, `--datum` (Standard heute) |
| Leistungszeitraum | `--von`/`--bis`, Standard Bestelldatum → Liefertermin |
| Übergabe Bericht/Roadmap | `--uebergabe`, Standard = Leistungsende |
| USt-IdNr. Empfänger, Angebot | `--ust-id`, `--angebot` (fehlen in der AB; ohne Angabe „—“ bzw. Satzteil entfällt) |

Das Skript bricht ab, wenn ein Pflichtfeld fehlt, die Positionssumme nicht zur AB-Summe passt
oder nach dem Füllen noch ein `[…]`-Platzhalter im Dokument steht.

Tests:
- `python tests/test_rechnung.py --iterationen 10 --je 6 --echt AB.pdf --pdf` – synthetische ABs (Zufallsvarianten)
- `python -m unittest tests/test_regression.py` – Regressionstests zu den Review-Funden und zur Bestellnummer
- `node tests/ui_test.js <URL> <AB-A.pdf> <AB-B.pdf> <Ordner>` – Oberfläche (Playwright), App vorher mit `python app.py --kein-browser` starten

## macOS-App „CS Rechnung“

`macos/build_app.sh` baut `dist/CS Rechnung.app` (+ ZIP). Die App richtet beim ersten Start eine eigene
Python-Umgebung in `~/Library/Application Support/CS-Rechnung/` ein und öffnet die Oberfläche (`app.py` + `ui/index.html`,
Server nur auf 127.0.0.1 mit Zugriffstoken) im Standardbrowser. Rechnungen landen standardmäßig in `~/Documents/Rechnungen`.
Unten in der App: „Letzte Rechnungen“ (gemerkte Rechnungen + alle `*_Rechnung_*.docx` im Ablageordner, durchsuchbar, öffnen in Word/PDF/Finder).
Ohne geöffnetes Fenster beendet sie sich nach 5 Minuten selbst. Log: `~/Library/Application Support/CS-Rechnung/app.log`.
Voraussetzung: Python ≥ 3.9 (`xcode-select --install`, Homebrew oder python.org). PDF-Export nur mit LibreOffice.
