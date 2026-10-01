# CS Rechnung – Anleitung

CS Rechnung erzeugt aus einer **CS-Auftragsbestätigung** oder einer **SAP-Ariba-Rechnung**
(z. B. Infineon Supplier Portal) eine Rechnung im Cyber-Shield-Design als Word-Datei, auf Wunsch zusätzlich als PDF.

---

## 1. Installation (einmalig)

Voraussetzungen:

- macOS 11 oder neuer
- Python 3.9 oder neuer. Fehlt Python, meldet die App das beim Start; Abhilfe: im Terminal `xcode-select --install`
  ausführen oder Python von python.org installieren.
- Internet beim **ersten** Start (die App lädt einmalig die PDF-Bibliothek `pdfplumber`).
- Optional für den PDF-Export: LibreOffice unter `/Applications/LibreOffice.app`.

Installation bzw. Update: `CS-Rechnung-mac.zip` herunterladen, dann im Terminal:

```bash
cd ~/Downloads
pkill -f "Resources/app/app.py"
Z=$(ls -t CS-Rechnung-mac*.zip 2>/dev/null | head -1)
if [ -n "$Z" ]; then rm -rf "CS Rechnung.app"; ditto -x -k "$Z" .; fi
rm -rf "/Applications/CS Rechnung.app"
mv "CS Rechnung.app" /Applications/
xattr -cr "/Applications/CS Rechnung.app"
open "/Applications/CS Rechnung.app"
```

| Zeile | Zweck |
|---|---|
| `pkill …` | beendet eine noch laufende ältere Version |
| `ls -t … \| head -1` | nimmt die neueste ZIP, auch wenn sie „(1)“ im Namen trägt; hat Safari schon entpackt, wird der fertige Ordner genutzt |
| `xattr -cr …` | entfernt die Download-Markierung; ohne sie blockiert macOS die (nicht von Apple signierte) App |

Einstellungen, Historie und Rechnungen bleiben bei einem Update erhalten.

**Ins Dock legen:** Finder → *Programme* → „CS Rechnung“ ins Dock ziehen. Ein Klick öffnet die Oberfläche im
Standardbrowser. Das Dock-Symbol zeigt keinen „läuft“-Punkt, weil die App im Hintergrund arbeitet.

Erscheint trotzdem „Apple konnte nicht überprüfen …“: einmal `xattr -cr "/Applications/CS Rechnung.app"` ausführen.

---

## 2. Rechnung erstellen

1. **Beleg hineinziehen** (oder Feld anklicken und auswählen): Auftragsbestätigung **oder** SAP-Ariba-Rechnung als PDF
   (beide Ariba-Ausgaben: „Kopie“ und Druckansicht aus dem Portal; die Druckansicht enthält kein Bestelldatum).
   Die App erkennt die Belegart selbst und zeigt sie an, z. B. „✓ RE-2026-09-30-05.pdf (SAP Ariba)“.
2. **Prüfen** unter „Aus dem Beleg gelesen“: Kunde, Anschrift, Beleg- und Bestellnummer, Positionen, Netto/USt/Brutto.
3. **Rechnungsdaten ergänzen** (alle Felder änderbar):

   | Feld | Vorbelegung bei AB | Vorbelegung bei SAP-Rechnung |
   |---|---|---|
   | Rechnungsnummer | nächste freie eigene Nummer (Vorschlag darunter, „übernehmen“) | **Nummer der SAP-Rechnung** – nicht ändern, sonst Doppelzahlungsrisiko |
   | Rechnungsdatum | heute | Datum der SAP-Rechnung |
   | Zahlbar bis | Rechnungsdatum + Zahlungsziel der AB | Rechnungsdatum + Zahlungsziel aus SAP |
   | Bestellnummer / -datum des Kunden | aus der AB | aus SAP |
   | Leistung von / bis | Bestelldatum → Liefertermin | Service-Start → Service-Ende |
   | Bericht übergeben am | Leistungsende | leer – leer bedeutet: Satz zu Bericht/Roadmap entfällt |
   | USt-IdNr. Empfänger | leer (steht nicht in der AB) | aus SAP |
   | Angebotsnummer | leer – leer bedeutet: Verweis entfällt | leer |
   | Ablageordner | `~/Documents/Rechnungen` (letzter Ordner wird gemerkt) | ebenso |

4. **„Rechnung erstellen“** klicken. Danach: *In Word öffnen*, *PDF öffnen* (falls erstellt), *Im Finder zeigen*,
   **Weitere Rechnung erstellen** (zurück zu Schritt 1: Beleg und Kundendaten geleert, nächste freie Nummer und
   heutiges Datum gesetzt, Ablageordner und PDF-Haken bleiben).
   Hinweise unter dem Ergebnis erklären, was fehlte oder weggelassen wurde.

Dateiname: `<Rechnungsnummer>_Rechnung_<Kunde>.docx`, z. B. `2026-0142_Rechnung_OHB_SE.docx`.

### Rechnungsnummer

- Frei eingebbar: Buchstaben, Ziffern, Leerzeichen und `. _ / # + -`, z. B. `RE 2026/77`.
- Der Vorschlag ist die höchste vergebene Nummer im Ablageordner + 1. Im neuen Jahr beginnt er wieder bei 1.
- Eine Nummer, die im Ablageordner schon für einen **anderen** Kunden existiert, wird abgelehnt.
- Gleiche Nummer und gleicher Kunde: Rückfrage „Überschreiben?“.
- SAP-Nummern verändern die eigene Nummernfolge nicht.

### Was auf der Rechnung steht

- Infoblock rechts: Rechnungsnummer, -datum, Leistungszeitraum, Auftragsbestätigung, Kundennummer, Bestellung.
- Bestellnummer des Kunden zusätzlich im Anschreiben, im Verwendungszweck und unter der Anschrift.
- Zeilen ohne Wert entfallen (z. B. bei SAP-Rechnungen: Auftragsbestätigung, Kundennummer, „z. Hd.“).
- Bei SAP-Rechnungen: Satz „Die Rechnung wurde elektronisch über SAP Ariba übermittelt.“
- Umsatzsteuer immer 19 %.

---

## 3. Letzte Rechnungen

Unten in der App: die zuletzt erstellten Rechnungen und alle Rechnungen in den bisher benutzten Ablageordnern,
neueste zuerst. Suche nach Nummer, Kunde, Projekt oder Betrag. Je Zeile: *Word*, *PDF*, *Finder*.
Im Finder gelöschte Rechnungen verschwinden aus der Liste.

**Rechnungen fehlen in der Liste?** Im Feld *Ablageordner* den Ordner eintragen, in dem sie liegen, und das Feld
verlassen (Tab) – die Rechnungen dort erscheinen sofort. Nach der nächsten erstellten Rechnung merkt sich die App
den Ordner dauerhaft. Rechnungen, die so zurückkommen, zeigen keinen Bruttobetrag (der steht nur im Dokument).

---

## 4. Beenden

„Beenden“ oben rechts – oder einfach den Browser-Tab schließen: Die App beendet sich dann nach spätestens 5 Minuten selbst.

---

## 5. Kommandozeile (ohne App)

```bash
cd CS-Rechnung-Code
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python rechnung_aus_ab.py <Beleg.pdf> [--rechnungsnr 2026-0142] [--datum 01.10.2026] [--pdf]
```

Weitere Optionen: `--von`, `--bis`, `--uebergabe`, `--ohne-uebergabe`, `--ust-id`, `--angebot`, `--bestellnr`,
`--bestelldatum`, `-o <Ziel.docx>`, `--ueberschreiben` (vorhandene Fassung derselben Rechnung ersetzen).
Bei SAP-Rechnungen ist `--rechnungsnr` nicht nötig. Eine Datei, die zu einer anderen Rechnung gehört, wird nie überschrieben.

---

## 6. Wenn etwas nicht klappt

| Meldung / Verhalten | Ursache / Abhilfe |
|---|---|
| „Apple konnte nicht überprüfen …“ | `xattr -cr "/Applications/CS Rechnung.app"` |
| „Python 3 fehlt …“ | im Terminal `xcode-select --install` oder Python von python.org installieren |
| „Einrichtung fehlgeschlagen (Internetverbindung?)“ | erster Start ohne Internet → mit Internet erneut starten |
| „Unbekannter Beleg …“ | PDF ist weder CS-AB noch SAP-Ariba-Standardrechnung |
| „… nicht gefunden (Bezeichnungen laut woerterbuch.json: …)“ | Beleg nennt das Feld anders → Bezeichnung in `woerterbuch.json` ergänzen (Technik, Abschnitt 4.4) |
| „woerterbuch.json ist fehlerhaft (Zeile …)“ | Tippfehler beim Ergänzen, meist Komma nach dem letzten Eintrag → an der genannten Stelle korrigieren, App beenden und neu starten |
| „Summe der Positionen ≠ …“ / sonst abweichender Aufbau | Beleg weicht vom bekannten Layout ab → Beleg an die Entwicklung geben |
| „Keine Verbindung zu CS Rechnung“ | App wurde beendet → Dock-Symbol erneut anklicken |
| PDF-Knopf fehlt / „PDF-Export fehlgeschlagen“ | LibreOffice fehlt oder ist gerade geöffnet → beenden, erneut erstellen; das DOCX ist trotzdem fertig |
| Sonstiges | Protokoll: `~/Library/Application Support/CS-Rechnung/app.log` |

Zurücksetzen (Python-Umgebung neu aufbauen): `rm -rf ~/Library/Application\ Support/CS-Rechnung/venv` und App neu starten.
Einstellungen und Historie liegen in `einstellungen.json` im selben Ordner.
