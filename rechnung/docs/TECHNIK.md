# CS Rechnung – Technische Dokumentation

Stand: Version 1.9 (Branch `claude/invoice-template-script-1snhqe`).

## 1. Überblick

```
 Beleg (PDF)                     CS Rechnung (lokal auf dem Mac)                          Ergebnis
 ───────────                     ───────────────────────────────                          ────────
 CS-Auftrags-   ─┐   Browser-UI ──HTTP 127.0.0.1──▶ app.py ──▶ rechnung_aus_ab.py ──▶  <Nr>_Rechnung_<Kunde>.docx
 bestätigung     ├─▶ (ui/index.html)   (+Token)      (Server)   ├─ lese_beleg()         (+ .pdf via LibreOffice)
 SAP-Ariba-     ─┘                                              ├─ lese_ab()           im Ablageordner
 Rechnung                                                       ├─ quelle_ariba.py
                                                                └─ fuelle_vorlage() ◀── vorlagen/*.dotx
```

- Alles läuft lokal. Der Server lauscht nur auf `127.0.0.1`; es werden **keine Belegdaten** ins Internet übertragen.
- Internetzugriff gibt es nur beim ersten Start bzw. nach geänderten Abhängigkeiten: `pip` lädt `pdfplumber`
  (mit `pdfminer.six`, `pypdfium2`, `Pillow`) von PyPI.
- Python ≥ 3.9 (getestet mit 3.9 – entspricht Apples System-Python – und 3.11).

## 2. Dateien im Repository (`rechnung/`)

| Datei | Aufgabe |
|---|---|
| `rechnung_aus_ab.py` | Kern und Kommandozeile: Belegart erkennen, CS-AB lesen, Vorlage füllen, DOCX schreiben, PDF-Export |
| `quelle_ariba.py` | Leser für SAP-Ariba-„Standardrechnungen“ |
| `app.py` | lokaler HTTP-Server der App (API, Einstellungen, Historie, Nummernlogik) |
| `ui/index.html` | Oberfläche (HTML/CSS/JS ohne externe Abhängigkeiten) |
| `vorlagen/2026-OKT_CS-Rechnung_Vorlage.dotx` | Rechnungsvorlage mit Platzhaltern `[…]` |
| `macos/CS-Rechnung` | Startprogramm der App (bash) |
| `macos/Info.plist`, `macos/AppIcon.icns` | App-Bundle-Metadaten und Symbol |
| `macos/build_app.sh` | baut `dist/CS Rechnung.app` und `dist/CS-Rechnung-mac.zip` (`build_app.sh <Version>`) |
| `requirements.txt` | `pdfplumber` (Laufzeit), `reportlab` (nur Tests) |
| `tests/` | Tests, siehe Abschnitt 9 |

## 3. Speicherorte auf dem Mac

| Ort | Inhalt | Lebensdauer |
|---|---|---|
| `/Applications/CS Rechnung.app` | Programm (Startprogramm, Python-Code, Vorlage, Oberfläche) | bis zum nächsten Update |
| `~/Library/Application Support/CS-Rechnung/venv/` | eigene Python-Umgebung mit `pdfplumber` | wird neu gebaut, wenn sich `requirements-app.txt` ändert |
| `~/Library/Application Support/CS-Rechnung/einstellungen.json` | Einstellungen, Nummernstand, Historie (s. u.) | dauerhaft |
| `~/Library/Application Support/CS-Rechnung/app.log` | Startprotokoll, Fehlermeldungen | wächst; kann gelöscht werden |
| `~/Documents/Rechnungen/` (änderbar) | erzeugte Rechnungen `*.docx` / `*.pdf` | dauerhaft (Ablage des Anwenders) |
| `$TMPDIR/cs-rechnung-XXXX/` | hochgeladene Belege (die letzten 5, für mehrere Tabs) | ältere sofort, unlesbare sofort, alle beim Beenden gelöscht |

`einstellungen.json`:

| Schlüssel | Bedeutung |
|---|---|
| `ordner` | zuletzt verwendeter Ablageordner |
| `letzte_nummer` | höchste eigene Rechnungsnummer (bewegt sich nur vorwärts; SAP-Nummern zählen nicht) |
| `pdf` | Häkchen „zusätzlich PDF“ |
| `historie` | bis zu 200 Einträge: Nummer, Kunde, Brutto, Rechnungsdatum, AB-Nr./Quelle, Projekt, Zeitpunkt, DOCX-Pfad |
| `laufend` | URL der laufenden Instanz inkl. Zugriffstoken (wird beim Beenden entfernt) |

Unter Linux (Entwicklung/Tests) liegt die Datei unter `$XDG_CONFIG_HOME/cs-rechnung/` bzw. `~/.config/cs-rechnung/`.

## 4. Ablauf

### 4.1 Start (`macos/CS-Rechnung`)

1. Python ≥ 3.9 suchen: `/opt/homebrew/bin`, `/usr/local/bin`, python.org-Framework, `/usr/bin/python3`, `PATH`.
2. Beim ersten Start bzw. bei geänderter `requirements-app.txt`: venv anlegen, `pip` aktualisieren, `pdfplumber` installieren.
3. `app.py` **abgekoppelt** starten (`nohup … &`) und das Startprogramm beenden. Dadurch startet jeder Klick im
   Dock neu; läuft schon eine Instanz, öffnet `app.py` nur deren Fenster.
4. Startfehler (Python fehlt, Einrichtung gescheitert, `app.py` bricht ab) → Dialog per `osascript` + Eintrag im Log.

### 4.2 Server (`app.py`)

- `ThreadingHTTPServer` auf `127.0.0.1`, zufälliger Port, zufälliges Token (`secrets.token_urlsafe`).
- Seite: `GET /<token>/`; alle API-Aufrufe `POST` mit Kopfzeile `X-Token` (sonst 403).
- **Programmstand:** Prüfsumme über `app.py`, `rechnung_aus_ab.py`, `quelle_ariba.py` und `ui/index.html`
  (`STAND`, Antwort von `/api/ping-frei`). Läuft bereits eine Instanz gleichen Stands, wird sie wiederverwendet (auch mit `--kein-browser`; nie zwei Instanzen,
  die dieselbe `einstellungen.json` schreiben). Findet ein Start eine laufende Instanz mit anderem Stand – typisch nach einem
  Update ohne vorheriges Beenden –, beendet er sie und startet neu. Die Oberfläche wird beim Serverstart einmal geladen,
  damit Code und Oberfläche immer zusammenpassen.
- Beenden: Knopf „Beenden“, oder 5 Minuten ohne Lebenszeichen der Seite (Ping alle 10 s), oder 3 Minuten ohne
  ersten Seitenaufruf. Gemessen mit `time.monotonic()` (Ruhezustand zählt nicht).

| Endpunkt | Funktion |
|---|---|
| `/api/start` | Ablageordner, Nummernvorschlag, heutiges Datum, PDF-Export möglich? |
| `/api/lesen` | Beleg (rohe PDF-Bytes, max. 50 MB) lesen → erkannte Daten + Vorschläge als JSON |
| `/api/erstellen` | Rechnung erzeugen; prüft Nummer, Ablageordner, Dubletten; aktualisiert Nummernstand und Historie |
| `/api/historie` | letzte 50 Rechnungen (Historie + Dateien im Ablageordner) |
| `/api/oeffnen` | Datei in Word/Vorschau bzw. im Finder öffnen – **nur** Dateien aus dieser Sitzung oder der Historie |
| `/api/ping`, `/api/ping-frei` | Lebenszeichen / „läuft schon?“ für den zweiten Start |
| `/api/beenden` | Server beenden |

### 4.3 Beleg lesen (`lese_beleg`)

Erkennung am Text der ersten Seite:

| Merkmal | Leser |
|---|---|
| „Standardrechnung“ **und** („Lieferantenreferenznr“ **oder** „Ursprünglicher Bestellauftrag“/„RECHNUNGSANSCHRIFT“) | `quelle_ariba.lese_ariba` |
| „Auftragsbest…“ | `lese_ab` |
| sonst | Fehler „Unbekannter Beleg“ |

Beide Leser arbeiten mit Wortkoordinaten aus `pdfplumber` (Spalten und Zeilen), nicht mit festen Seitenpositionen.
Ergebnis ist ein `Auftrag`-Objekt.

**CS-Auftragsbestätigung (`lese_ab`)**
- Kopfblock rechts: `Datum`, `Kunde`, `Anschrift` (→ Straße / PLZ Ort), `Kunden-Nr.`, `Kundenkontakt`,
  `Rechnungsempf`, `Projekt` (Folgezeilen werden angehängt).
- Titel `Auftragsbestätigung <Nr>`, `Ihre Bestellung [Nr.] <Nr> … [vom <Datum>]` (Nummer = erstes Wort mit Ziffer, Datum
  optional), `Liefertermin` (unlesbar oder ungültig → Hinweis), `Zahlungsziel: N Tage`.
- Produkttabelle über die Spaltenköpfe `Produkte / Einheit / Menge / Summe`; `Leistungsbeschreibung:` bis `Summe Auftrag`.
- Fußzeile (unterste 8 % jeder Seite) wird ignoriert; mehrseitige ABs werden zusammengesetzt.

**SAP-Ariba-Rechnung (`lese_ariba`)** – SAP Ariba gibt dieselbe Rechnung in zwei Formen aus, beide werden erkannt:

| Ausgabeform | Merkmal | Vorlage | Besonderheit |
|---|---|---|---|
| Kopie („von Menschen lesbare Darstellung“) | „Lieferantenreferenznr.“ | RE-2026-09-30-05 | enthält Bestelldatum |
| Druckansicht aus dem Portal (US-Letter, Seitenzähler „n/m“) | „Ursprünglicher Bestellauftrag“, „RECHNUNGSANSCHRIFT:“ | RE-2026-09-30-03 | **kein** Bestelldatum (Hinweis, Feld in der App leer) |

- Rechnungsnummer, Rechnungsdatum, Service-Start/-Ende, Bestellauftragsnr./-datum, Nettozahlungsbedingungen (Tage).
- USt-IdNr. des Kunden (zweite Kennung in der Zeile unter „Umsatzsteuer-/Steuernummer“).
- Empfänger aus „Rechnungsanschrift“ (Spalte zwischen „Rechnungsanschrift“ und „Zahlungsempfänger“; Region-Zeile wird übersprungen).
- Positionen aus der Positionstabelle (Beschreibungsspalte, Menge, Zwischensumme); Titel = erster Teil der Beschreibung,
  Rest als Beschreibung. Mehrseitige Belege werden zusammengesetzt.

**Prüfungen beim Lesen:** Pflichtfelder vorhanden; Summe der Positionen = Auftrags- bzw. Nettosumme; bei SAP zusätzlich
Steuersatz = 19 % und Netto + Steuer = fälliger Betrag. Beträge werden mit und ohne Tausenderpunkt gelesen
(`4.500,-`, `4500,00 €`, `EUR 12.345,67`, `4 500,00 €`).

### 4.4 Rechnung erzeugen (`erstelle_rechnung` → `fuelle_vorlage`)

Standardwerte, wenn nichts angegeben ist (CLI und App):

| Wert | AB | SAP |
|---|---|---|
| Rechnungsnummer | Pflichtangabe | aus dem Beleg |
| Rechnungsdatum | heute | aus dem Beleg |
| Leistungszeitraum | Bestelldatum (sonst AB-Datum) → Liefertermin (sonst Rechnungsdatum) | Service-Start → -Ende |
| Übergabe Bericht/Roadmap | Leistungsende | keine (Satz entfällt) – außer mit `--uebergabe` |
| USt-IdNr. Empfänger | „—“ | aus dem Beleg |
| Fälligkeit | Rechnungsdatum + Zahlungsziel | ebenso |
| USt | 19 % (`UST_SATZ`), kaufmännisch gerundet | ebenso |

Die `.dotx` ist eine ZIP-Datei; bearbeitet werden `word/document.xml` und `word/footer*.xml` direkt als XML:

- **Platzhalter run-übergreifend ersetzen** (`ersetze`): Word zerlegt Texte in „Runs“; ein Platzhalter wie
  `[2026-014-AB]` kann über mehrere Runs verteilt sein. Ersetzt wird auf dem Gesamttext eines Absatzes, der neue Text
  landet im ersten betroffenen Run (dessen Formatierung bleibt erhalten).
- **Positionszeile** der Vorlage wird je Beleg-Position geklont und gefüllt.
- **Infozeile „Ihre Bestellung“** wird aus der Zeile „Kundennummer“ geklont, wenn eine Bestellnummer vorliegt.
- **Optionale Teile:** Ohne AB-Nummer, Kundennummer oder Ansprechpartner werden die Zeilen entfernt; ohne Übergabedatum
  entfallen die Sätze zu Bericht/Roadmap; ohne AB wird „gemäß Ihrer Bestellung …“ verwendet; bei SAP wird der Hinweis
  auf die Übermittlung über SAP Ariba eingefügt.
- Content-Type `template` → `document` (aus `.dotx` wird `.docx`).
- **Eingesetzte Werte geschützt:** Eckige Klammern in eingesetzten Daten (Kunde `ACME [K-1042]`, Projekt
  `Pentest [Phase 2]`) werden bis zum Schluss maskiert; spätere Regeln greifen nicht auf eingesetzte Daten zu.
- **Abschlussprüfung:** Steht danach noch irgendwo ein `[…]`-Platzhalter der Vorlage, wird **keine** Datei geschrieben (Fehler).
- **Atomar schreiben:** erst Temp-Datei im Zielordner, dann ersetzen – eine vorhandene Rechnung bleibt bei Schreibfehlern erhalten.
- **Beleg nie anfassen:** Heißt das PDF-Ziel wie der Beleg selbst (z. B. `RE-….pdf` → `RE-….docx`), wird kein PDF erzeugt
  und der Beleg nicht gelöscht; ein PDF neben der Rechnung wird nur entfernt/ersetzt, wenn es zur eigenen Vorfassung gehört.
- Rechnungsnummer wird im Kern geprüft (erlaubt: Buchstaben, Ziffern, Leerzeichen, `. _ / # + -`) und steht mit dem Kunden
  in `docProps/core.xml` (`dc:identifier`).

Platzhalter der Vorlage:

| Platzhalter | Wert |
|---|---|
| `[2026-0142]` | Rechnungsnummer (Kopf, Infoblock, Verwendungszweck, Fußzeile) |
| `[TT.MM.JJJJ]` | je nach Stelle: Rechnungsdatum, Zahlbar bis, Übergabedatum |
| `[TT.MM.] – [TT.MM.JJJJ]` | Leistungszeitraum |
| `[Leistungsbezeichnung]` | Projekt |
| `[Kundenname GmbH]`, `[Ansprechpartner]`, `[Straße Nr.]`, `[PLZ Ort]` | Empfänger |
| `[DE…]` | USt-IdNr. Empfänger |
| `[—]` (Bestell-Nr. / Kostenstelle) | Bestellnummer des Kunden |
| `[2026-014-AB]` | AB-Nummer |
| `[K-1042]` | Kundennummer |
| `[2026-014]` (Angebot) | Angebotsnummer, sonst entfällt der Satzteil |
| `[0,00 €]` | Positionsbeträge, dann Netto, USt, Brutto |
| `STANDARD — [Leistungsbezeichnung] (Festpreis)`, Beispieltext, `1 Pauschale`, `01` | Positionszeile |

Layout-Festlegungen in der Vorlage: alle Tabellen ohne Einzug (Kästen = Satzspiegel, Word-2013-Tabellenmodus),
Text der Positionstabelle bündig mit dem Fließtext, Innenabstand farbiger Kästen 17 pt, Summenblock 5.800 Twips breit,
Tabellenzeilen werden nicht über Seiten geteilt, Standardschrift Calibri (LibreOffice nutzt das maßgleiche Carlito),
Fußzeile dreispaltig mit Webseite `cyber-shield.org`.

### 4.5 PDF-Export (optional)

`soffice --headless --convert-to pdf` (LibreOffice im `PATH` oder `/Applications/LibreOffice.app`). Ein vorhandenes PDF
gleichen Namens wird vorher gelöscht; entsteht kein neues, gibt es einen Hinweis – das DOCX ist trotzdem fertig.
Wird ohne PDF überschrieben, wird das veraltete PDF entfernt.

## 5. Rechnungsnummern

- Vorschlag: höchste Nummer im Schema der zuletzt verwendeten eigenen Nummer (Präfix + Ziffern, z. B. `2026-`, `RE-2026-`),
  ermittelt aus `letzte_nummer` **und** den Dateinamen im Ablageordner, + 1; enthält das Präfix ein anderes Jahr als heute,
  beginnt die Zählung im aktuellen Jahr bei 1.
- `letzte_nummer` bewegt sich nur vorwärts (Korrektur einer alten Rechnung setzt den Vorschlag nicht zurück).
- Dubletten: gleiche Nummer für anderen Kunden im Ablageordner → abgelehnt; gleiche Datei → Rückfrage. Gezählt werden
  `.docx` **und** `.pdf` (eine versendete Rechnung, von der nur noch das PDF existiert, bleibt vergeben).
- `letzte_nummer` vergleicht Schema ohne Jahr, dann (Jahr, Nummer): die Korrektur einer Vorjahresrechnung setzt den
  Vorschlag nicht zurück.
- Dateinamen sind verlustbehaftet (`Acme & Partner` und `Acme Partner`, `RE 2026/77` und `RE_2026_77` ergeben denselben
  Namen). Jede erzeugte Rechnung trägt deshalb in den Dokumenteigenschaften (`docProps/core.xml`, `dc:identifier`)
  Rechnungsnummer und Kunde im Original. Gehört eine vorhandene Datei gleichen Namens zu einer anderen Rechnung,
  wird nicht überschrieben – auch nicht nach Bestätigung. Diese Prüfung sitzt im Kern (`erstelle_rechnung`,
  `DateiExistiert`) und gilt damit auch für die Kommandozeile (dort: Abbruch, Ersetzen derselben Rechnung nur mit
  `--ueberschreiben`).

## 6. Sicherheit

- Server nur auf `127.0.0.1`, zufälliger Port, Token für jede Anfrage.
- `/api/oeffnen` öffnet nur Dateien, die die App selbst erstellt hat oder die in der Historie stehen.
- Dateinamen und Belegtexte werden in der Oberfläche nur als Text eingesetzt (kein HTML).
- Hochgeladene Belege liegen nur temporär vor (Abschnitt 3).
- Die App ist nicht von Apple signiert/notarisiert; die Quarantäne-Markierung wird bei der Installation mit `xattr -cr` entfernt.

## 7. Bauen und Ausliefern

```bash
cd rechnung
./macos/build_app.sh 1.9        # → dist/CS Rechnung.app, dist/CS-Rechnung-mac.zip
```

Das Bundle enthält `rechnung_aus_ab.py`, `quelle_ariba.py`, `app.py`, `ui/index.html`, die Vorlage und
`requirements-app.txt`. Läuft unter macOS und Linux (keine Xcode-Abhängigkeit).

## 8. Erweitern

**Neue Belegart** (z. B. weiteres Kundenportal):
1. Modul `quelle_<name>.py` mit `ist_<name>(text)` und `lese_<name>(pfad) -> Auftrag` (Muster: `quelle_ariba.py`).
2. In `lese_beleg` ein Erkennungsmerkmal ergänzen.
3. `Auftrag.quelle` setzen; vorbelegte Werte (`rechnungsnr`, `rechnungsdatum`, `leistung_von/bis`, `ust_id_kunde`) nach Bedarf.
4. Modul in `macos/build_app.sh` mitkopieren; Generator und Tests nach Vorbild `tests/test_ariba.py`.

**Vorlage ändern:** Platzhalter in eckigen Klammern beibehalten oder die Regeln in `fuelle_vorlage` anpassen;
die Abschlussprüfung meldet vergessene Platzhalter.

## 9. Tests

```bash
cd rechnung
pip install -r requirements.txt
python tests/test_rechnung.py --iterationen 10 --je 6 [--echt AB.pdf] [--pdf]   # synthetische ABs, 10 Iterationen
python -m unittest tests/test_regression.py tests/test_ariba.py tests/test_review2.py  # Regressionen, SAP, 2. Review
python app.py --kein-browser   # in zweitem Terminal, dann:
node tests/ui_test.js <URL> <AB-A.pdf> <AB-B.pdf> <Ordner> [SAP.pdf]          # Oberfläche (Playwright)
```

Die synthetischen Belege bilden das Layout je eines echten Belegs nach (AB-2025-10-29-2, RE-2026-09-30-05). Echte
Kundenbelege liegen wegen Kundendaten nicht im Repository.

## 10. Bekannte Grenzen

- Unterstützt sind genau die zwei Layouts oben; abweichende Belege werden mit Meldung abgelehnt, nicht geraten.
- Umsatzsteuer fest 19 %; Reverse-Charge, andere Sätze und Fremdwährungen sind nicht vorgesehen.
- Überschriften der Vorlage nutzen die Schrift Montserrat; ist sie auf dem Mac nicht installiert, ersetzt Word sie.
- Ohne Apple-Signatur (Developer ID, 99 USD/Jahr, für eine GmbH mit D-U-N-S-Nummer) erscheint auf fremden Macs
  die Gatekeeper-Warnung; für die eigene Nutzung genügt `xattr -cr`.
- Auf einem echten Mac sind Startprogramm, Dock-Verhalten und Word-Darstellung nicht automatisiert getestet
  (Entwicklung und Tests laufen unter Linux mit LibreOffice).
