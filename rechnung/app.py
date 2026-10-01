#!/usr/bin/env python3
"""CS Rechnung – lokale Oberfläche für rechnung_aus_ab.py.

Startet einen Server nur auf 127.0.0.1 (zufälliger Port, Zugriffstoken) und öffnet
die Oberfläche im Standardbrowser. Der Server beendet sich, wenn das Fenster
geschlossen ist (kein Lebenszeichen der Seite mehr).

Aufruf: python app.py [--kein-browser] [--port N]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER))
import rechnung_aus_ab as ra  # noqa: E402

if sys.platform == "darwin":
    STATUS_DIR = Path.home() / "Library" / "Application Support" / "CS-Rechnung"
else:
    STATUS_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cs-rechnung"
STATUS_DATEI = STATUS_DIR / "einstellungen.json"
STANDARD_ORDNER = Path.home() / "Documents" / "Rechnungen"
# Programmstand: Prüfsumme über Code und Oberfläche. Eine laufende Instanz mit anderem Stand
# (z. B. nach einem Update) wird beim Start beendet, damit nie alter Code mit neuer Oberfläche läuft.
STAND = hashlib.sha256(b"".join(
    (HIER / f).read_bytes() for f in ("app.py", "rechnung_aus_ab.py", "quelle_ariba.py", "woerterbuch.py",
                                       "woerterbuch.json", "ui/index.html")
    if (HIER / f).exists())).hexdigest()[:16]
UI_HTML = (HIER / "ui" / "index.html").read_text(encoding="utf-8")  # einmal laden: Oberfläche passt zum Code
LEERLAUF_S = 300       # ohne Lebenszeichen der Seite → beenden (Browser drosseln Hintergrund-Tabs auf 1/min)
ERSTKONTAKT_S = 180    # Zeit bis zum ersten Seitenaufruf


def lade_status() -> dict:
    try:
        return json.loads(STATUS_DATEI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def speichere_status(daten: dict) -> None:
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_DATEI.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")


NR_RX = re.compile(r"^(.*?)(\d+)$")


def zerlege_nummer(nr: str) -> tuple[str, str] | None:
    """'RE-2026-0142' -> ('RE-2026-', '0142'); ohne Ziffern am Ende None."""
    m = NR_RX.match(nr or "")
    return (m.group(1), m.group(2)) if m else None


def _praefix_schluessel(praefix: str) -> str:
    # wie im Dateinamen ('2026/' und '2026_' gelten als gleich)
    return ra._dateiname(praefix)


def vergebene_nummern(ordner: Path) -> list[str]:
    """Rechnungsnummern (in Dateinamen-Schreibweise) der Rechnungen im Ablageordner."""
    try:
        return sorted({f.name.split("_Rechnung_", 1)[0] for f in ordner.glob("*_Rechnung_*")
                       if f.suffix.lower() in (".docx", ".pdf")})
    except OSError:
        return []


HISTORIE_MAX = 200
UPLOADS_MAX = 5   # gleichzeitig vorgehaltene Belege (mehrere Browser-Tabs)


def merke_in_historie(st: dict, eintrag: dict) -> None:
    """Rechnung vorne in die Historie (gleiche Datei ersetzt den alten Eintrag)."""
    alt = [e for e in st.get("historie", []) if e.get("docx") != eintrag["docx"]]
    st["historie"] = [eintrag] + alt[:HISTORIE_MAX - 1]


def historie(st: dict, ordner: Path, anzahl: int = 50) -> list[dict]:
    """Letzte Rechnungen: gemerkte Einträge, deren DOCX noch existiert, plus Rechnungen im
    Ablageordner ohne Eintrag (z. B. per Kommandozeile erstellt) – neueste zuerst."""
    eintraege, bekannt = [], set()
    for e in st.get("historie", []):
        docx = Path(e.get("docx", ""))
        if docx.is_file():
            pdf = docx.with_suffix(".pdf")
            eintraege.append(dict(e, pdf=str(pdf) if pdf.is_file() else None))
            bekannt.add(str(docx))
    try:
        dateien = list(ordner.glob("*_Rechnung_*.docx"))
    except OSError:
        dateien = []
    for f in dateien:
        if str(f) in bekannt or f.name.startswith("~$"):
            continue
        nr, kunde = f.stem.split("_Rechnung_", 1)
        zeit = dt.datetime.fromtimestamp(f.stat().st_mtime)
        pdf = f.with_suffix(".pdf")
        eintraege.append({"nummer": nr, "kunde": kunde.replace("_", " "), "brutto": None,
                          "rechnungsdatum": None, "erstellt": zeit.isoformat(timespec="seconds"),
                          "docx": str(f), "pdf": str(pdf) if pdf.is_file() else None})
    eintraege.sort(key=lambda e: e.get("erstellt") or "", reverse=True)
    return eintraege[:anzahl]


def naechste_nummer(letzte: str | None, vergeben: list[str], heute: dt.date) -> str:
    """Höchste bekannte Nummer im Schema der zuletzt verwendeten + 1; neues Jahr → wieder ab 1."""
    teile = zerlege_nummer(letzte or "")
    if not teile:
        praefix, ziffern, hoechste = f"{heute.year}-", "0000", 0
    else:
        praefix, ziffern = teile
        hoechste = int(ziffern)
        jahr = re.search(r"(?<!\d)(20\d{2})(?!\d)", praefix)
        if jahr and int(jahr.group(1)) != heute.year:
            praefix = praefix[:jahr.start(1)] + str(heute.year) + praefix[jahr.end(1):]
            hoechste = 0
    schluessel = _praefix_schluessel(praefix)
    for v in vergeben:
        t = zerlege_nummer(v)
        if t and _praefix_schluessel(t[0]) == schluessel:
            hoechste = max(hoechste, int(t[1]))
    return f"{praefix}{hoechste + 1:0{len(ziffern)}d}"


JAHR_RX = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


def _schema(praefix: str) -> tuple[str, int]:
    """('RE-2026-' ) -> ('RE-{J}-', 2026): Schema ohne Jahr + Jahr (0, wenn keins)."""
    m = JAHR_RX.search(praefix)
    if not m:
        return _praefix_schluessel(praefix), 0
    return _praefix_schluessel(praefix[:m.start()] + "{J}" + praefix[m.end():]), int(m.group(1))


def hoehere_nummer(gespeichert: str | None, neu: str) -> str:
    """Gespeicherte 'letzte Nummer' nur vorwärts bewegen: Korrektur einer alten Rechnung – auch aus
    einem Vorjahr – setzt den Vorschlag nicht zurück. Ein neues Schema ersetzt das alte."""
    a, b = zerlege_nummer(gespeichert or ""), zerlege_nummer(neu)
    if not a or not b:
        return neu
    (sa, ja), (sb, jb) = _schema(a[0]), _schema(b[0])
    if sa != sb:
        return neu
    return neu if (jb, int(b[1])) >= (ja, int(a[1])) else gespeichert  # type: ignore[return-value]


def iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


def auftrag_json(a: ra.Auftrag) -> dict:
    return {
        "ab_nr": a.ab_nr, "ab_datum": iso(a.ab_datum), "kunde": a.kunde, "strasse": a.strasse,
        "plz_ort": a.plz_ort, "kunden_nr": a.kunden_nr, "kontakt": a.kontakt, "projekt": a.projekt,
        "summe_netto": str(a.summe_netto), "zahlungsziel_tage": a.zahlungsziel_tage,
        "bestell_nr": a.bestell_nr, "bestell_datum": iso(a.bestell_datum),
        "liefertermin": iso(a.liefertermin), "rechnungs_mail": a.rechnungs_mail,
        "quelle": a.quelle, "rechnungsnr": a.rechnungsnr, "rechnungsdatum": iso(a.rechnungsdatum),
        "ust_id_kunde": a.ust_id_kunde,
        "positionen": [{"titel": p.titel, "produkt": p.produkt, "einheit": p.einheit,
                        "beschreibung": p.beschreibung, "menge": p.menge, "betrag": str(p.betrag),
                        "betrag_fmt": ra.fmt_betrag(p.betrag)} for p in a.positionen],
        # Beträge rechnet nur der Server (gleiche Rundung wie auf der Rechnung)
        "ust_satz": str(ra.UST_SATZ),
        "netto_fmt": ra.fmt_betrag(a.summe_netto),
        "ust_fmt": ra.fmt_betrag(ra.ust_von(a.summe_netto)),
        "brutto_fmt": ra.fmt_betrag(a.summe_netto + ra.ust_von(a.summe_netto)),
    }


def hinweise_fuer_ui(hinweise: list[ra.Hinweis]) -> list[str]:
    # Standardwerte stehen bereits im Formular; CLI-Optionen interessieren in der Oberfläche nicht
    return [h.text for h in hinweise if not h.standard]


def oeffne(pfad: Path, im_finder: bool = False) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(pfad)] if im_finder else ["open", str(pfad)])
    elif sys.platform.startswith("win"):
        os.startfile(str(pfad.parent if im_finder else pfad))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(pfad.parent if im_finder else pfad)])


class Zustand:
    def __init__(self, token: str):
        self.token = token
        self.letzter_kontakt = None  # type: float | None
        self.start = time.monotonic()  # monotonic: Ruhezustand des Macs zählt nicht als Leerlauf
        self.uploads: dict[str, tuple[Path, ra.Auftrag]] = {}
        self.tmp = Path(tempfile.mkdtemp(prefix="cs-rechnung-"))
        self.erstellt: set[str] = set()  # nur diese Dateien darf /api/oeffnen öffnen
        self.stand = STAND                # Programmstand dieser Instanz (bei Serverstart festgelegt)
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "CSRechnung/1.0"
    zustand: Zustand  # wird gesetzt

    def log_message(self, fmt, *args):  # ruhig
        pass

    # -- Hilfen -----------------------------------------------------------
    def _antwort(self, code: int, daten, typ="application/json; charset=utf-8"):
        roh = daten if isinstance(daten, bytes) else json.dumps(daten, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(roh)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(roh)

    def _fehler(self, code: int, text: str):
        self._antwort(code, {"fehler": text})

    def _token_ok(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Token", ""), self.zustand.token)

    def _body(self) -> bytes:
        laenge = int(self.headers.get("Content-Length", "0"))
        if laenge > 50 * 1024 * 1024:
            raise ValueError("Datei zu groß")
        return self.rfile.read(laenge)

    def _json(self) -> dict:
        return json.loads(self._body() or b"{}")

    # -- Routen -----------------------------------------------------------
    def do_GET(self):
        z = self.zustand
        if self.path == f"/{z.token}/" or self.path == f"/{z.token}":
            html = UI_HTML
            html = html.replace("__TOKEN__", z.token)
            z.letzter_kontakt = time.monotonic()
            return self._antwort(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if self.path == "/api/ping-frei":  # für zweiten App-Start
            return self._antwort(200, {"ok": True, "stand": self.zustand.stand})
        return self._fehler(404, "nicht gefunden")

    def do_POST(self):
        z = self.zustand
        if not self._token_ok():
            return self._fehler(403, "Zugriff verweigert")
        z.letzter_kontakt = time.monotonic()
        try:
            if self.path == "/api/ping":
                return self._antwort(200, {"ok": True})
            if self.path == "/api/start":
                return self._start()
            if self.path == "/api/lesen":
                return self._lesen()
            if self.path == "/api/erstellen":
                return self._erstellen()
            if self.path == "/api/historie":
                st = lade_status()
                return self._antwort(200, {"eintraege": historie(st, Path(st.get("ordner") or STANDARD_ORDNER))})
            if self.path == "/api/oeffnen":
                d = self._json()
                pfad = Path(d["pfad"])
                if str(pfad) not in z.erstellt and str(pfad) not in self._historie_dateien():
                    return self._fehler(403, "Nur Rechnungen aus der Historie können geöffnet werden.")
                if not pfad.exists():
                    return self._fehler(404, "Datei nicht gefunden")
                oeffne(pfad, bool(d.get("finder")))
                return self._antwort(200, {"ok": True})
            if self.path == "/api/beenden":
                self._antwort(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return None
        except (ra.AbFehler, ra.wb.WoerterbuchFehler) as e:
            return self._fehler(422, str(e))
        except Exception as e:  # noqa: BLE001
            return self._fehler(500, f"{type(e).__name__}: {e}")
        return self._fehler(404, "nicht gefunden")

    def _historie_dateien(self) -> set[str]:
        st = lade_status()
        return {p for e in historie(st, Path(st.get("ordner") or STANDARD_ORDNER), HISTORIE_MAX)
                for p in (e["docx"], e["pdf"]) if p}

    def _start(self):
        st = lade_status()
        heute = dt.date.today()
        ordner = st.get("ordner") or str(STANDARD_ORDNER)
        return self._antwort(200, {
            "ordner": ordner,
            "nummer": naechste_nummer(st.get("letzte_nummer"), vergebene_nummern(Path(ordner)), heute),
            "heute": heute.isoformat(),
            "pdf_moeglich": ra.finde_soffice() is not None,
            "pdf": bool(st.get("pdf", False)),
        })

    def _lesen(self):
        daten = self._body()
        if not daten.startswith(b"%PDF"):
            return self._fehler(422, "Das ist keine PDF-Datei.")
        name = urllib.parse.unquote(self.headers.get("X-Dateiname", "ab.pdf"))
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[-80:]
        uid = secrets.token_hex(8)
        pfad = self.zustand.tmp / f"{uid}_{name}"
        pfad.write_bytes(daten)
        try:
            a = ra.lese_beleg(pfad)
        except Exception:
            pfad.unlink(missing_ok=True)
            raise
        with self.zustand.lock:
            # die letzten UPLOADS_MAX Belege vorhalten (mehrere Tabs), ältere Kunden-PDFs sofort löschen
            self.zustand.uploads[uid] = (pfad, a)
            while len(self.zustand.uploads) > UPLOADS_MAX:
                alt = next(iter(self.zustand.uploads))
                self.zustand.uploads.pop(alt)[0].unlink(missing_ok=True)
        von = a.leistung_von or a.bestell_datum or a.ab_datum
        bis = a.leistung_bis or a.liefertermin
        aus_ab = a.quelle == "Auftragsbestätigung"   # Bericht/Roadmap-Satz nur bei Projekten aus der AB
        return self._antwort(200, {
            "id": uid, "auftrag": auftrag_json(a),
            "vorschlag": {"von": iso(von), "bis": iso(bis), "uebergabe": iso(bis) if aus_ab else None},
            "hinweise": hinweise_fuer_ui(a.hinweise),
        })

    def _erstellen(self):
        d = self._json()
        eintrag = self.zustand.uploads.get(d.get("id", ""))
        if not eintrag:
            return self._fehler(400, "Beleg nicht mehr vorhanden – bitte erneut hineinziehen.")
        pfad, a = eintrag
        nr = (d.get("nummer") or "").strip()
        if not nr:
            return self._fehler(422, "Bitte eine Rechnungsnummer eingeben.")
        nr = ra.pruefe_rechnungsnr(nr)  # gleiche Regel wie Kommandozeile (AbFehler → 422)

        def datum(k):
            v = d.get(k)
            return dt.date.fromisoformat(v) if v else None

        ordner_text = (d.get("ordner") or str(STANDARD_ORDNER)).strip()
        ordner = Path(os.path.expanduser(ordner_text))
        if not ordner.is_absolute():
            return self._fehler(422, f"Ablageordner '{ordner_text}' ist kein vollständiger Pfad – "
                                     "z. B. ~/Documents/Rechnungen angeben.")
        ziel = ordner / ra.rechnungs_dateiname(nr, a.kunde)
        # gleiche Nummer schon für einen anderen Kunden vergeben? → nie überschreibbar
        praefix = ra._dateiname(nr) + "_Rechnung_"
        # auch Rechnungen, von denen nur noch das PDF existiert (DOCX nach Versand gelöscht)
        fremd = sorted(f.name for f in ordner.glob(praefix + "*")
                       if f.suffix.lower() in (".docx", ".pdf") and f.stem != ziel.stem) if ordner.is_dir() else []
        if fremd:
            return self._fehler(409, f"Rechnungsnummer {nr} ist bereits vergeben ({fremd[0]}).")
        try:
            erg = ra.erstelle_rechnung(
                pfad, nr, datum=datum("datum"), von=datum("von"), bis=datum("bis"),
                uebergabe=datum("uebergabe"), ust_id=(d.get("ust_id") or "").strip(),
                angebot=(d.get("angebot") or "").strip(), ausgabe=ziel, pdf=bool(d.get("pdf")),
                auftrag=a, bestell_nr=(d.get("bestell_nr") or "").strip(),
                bestell_datum=datum("bestell_datum"), mit_uebergabe=bool(d.get("uebergabe")),
                ueberschreiben=bool(d.get("ueberschreiben")))
        except ra.DateiExistiert as e:  # Identität und Rückfrage prüft der Kern (gilt auch für die CLI)
            return self._antwort(409, {"fehler": str(e), **({} if e.fremd else {"existiert": True})})
        except OSError as e:
            return self._fehler(422, f"Ablageordner nicht beschreibbar: {e.strerror or e} ({ordner})")
        r = erg.rechnung
        self.zustand.erstellt.update(str(x) for x in (erg.docx, erg.pdf) if x)
        st = lade_status()
        st.update({"ordner": str(ordner), "pdf": bool(d.get("pdf"))})
        if nr != a.rechnungsnr:  # fremd vergebene Nummern (SAP) bestimmen nicht das eigene Nummernschema
            st["letzte_nummer"] = hoehere_nummer(st.get("letzte_nummer"), nr)
        merke_in_historie(st, {
            "nummer": nr, "kunde": a.kunde, "brutto": ra.fmt_betrag(r.brutto),
            "rechnungsdatum": r.datum.isoformat(), "ab_nr": a.ab_nr or a.quelle, "projekt": a.projekt,
            "erstellt": dt.datetime.now().isoformat(timespec="seconds"), "docx": str(erg.docx),
        })
        speichere_status(st)
        return self._antwort(200, {
            "docx": str(erg.docx),
            "pdf": str(erg.pdf) if erg.pdf else None,
            "netto": ra.fmt_betrag(r.netto), "ust": ra.fmt_betrag(r.ust),
            "brutto": ra.fmt_betrag(r.brutto), "faellig": ra.fmt_datum(r.faellig),
            "zeitraum": r.zeitraum,
            "hinweise": hinweise_fuer_ui(erg.hinweise),
            "naechste_nummer": naechste_nummer(st.get("letzte_nummer"), vergebene_nummern(ordner),
                                               dt.date.today()),
        })


def waechter(server: ThreadingHTTPServer, z: Zustand):
    while True:
        time.sleep(3)
        jetzt = time.monotonic()
        if z.letzter_kontakt is None:
            if jetzt - z.start > ERSTKONTAKT_S:
                break
        elif jetzt - z.letzter_kontakt > LEERLAUF_S:
            break
    server.shutdown()


def laufender_server() -> str | None:
    """URL einer bereits laufenden Instanz gleichen Stands (dann nur Fenster öffnen).
    Läuft eine Instanz mit anderem Stand (altes Programm nach Update), wird sie beendet."""
    st = lade_status()
    url = st.get("laufend")
    if not url:
        return None
    teile = url.split("/")
    if len(teile) < 4:
        return None
    basis, token = f"{teile[0]}//{teile[2]}", teile[3]
    try:
        with urllib.request.urlopen(f"{basis}/api/ping-frei", timeout=1) as r:
            antwort = json.loads(r.read() or b"{}")
    except (OSError, ValueError):
        return None
    if antwort.get("stand") == STAND:
        return url
    try:  # anderer Stand: alte Instanz beenden (Token steht in den eigenen Einstellungen)
        req = urllib.request.Request(f"{basis}/api/beenden", data=b"{}", method="POST",
                                     headers={"X-Token": token})
        urllib.request.urlopen(req, timeout=2).close()
    except OSError:
        pass
    for _ in range(20):  # warten, bis der Port frei ist
        try:
            urllib.request.urlopen(f"{basis}/api/ping-frei", timeout=0.5).close()
            time.sleep(0.25)
        except OSError:
            break
    print(f"Ältere Instanz ({antwort.get('stand', 'alt')}) beendet, starte Stand {STAND}", flush=True)
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kein-browser", action="store_true")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args(argv)

    url = laufender_server()
    if url:  # gleiche Version läuft schon: wiederverwenden, nie eine zweite Instanz (gemeinsame Einstellungen)
        if not args.kein_browser:
            webbrowser.open(url)
        print(url, flush=True)
        return 0

    try:   # Wörterbuch beim Start laden: passt zu STAND und Code dieser Instanz (wie UI_HTML)
        ra.wb.laden()
    except ra.wb.WoerterbuchFehler as e:   # Instanz trotzdem starten – die Meldung erscheint beim Lesen
        print(e, file=sys.stderr, flush=True)
    z = Zustand(secrets.token_urlsafe(16))
    Handler.zustand = z
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/{z.token}/"
    st = lade_status()
    st["laufend"] = url
    speichere_status(st)
    print(url, flush=True)
    threading.Thread(target=waechter, args=(server, z), daemon=True).start()
    if not args.kein_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        shutil.rmtree(z.tmp, ignore_errors=True)  # hochgeladene Kunden-PDFs nicht liegen lassen
        st = lade_status()
        if st.get("laufend") == url:
            st.pop("laufend")
            speichere_status(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
