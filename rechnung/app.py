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
import json
import os
import re
import secrets
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


def naechste_nummer(letzte: str | None, heute: dt.date) -> str:
    m = re.fullmatch(r"(\d{4})-(\d+)", letzte or "")
    if not m:
        return f"{heute.year}-0001"
    jahr, nr = int(m.group(1)), m.group(2)
    if jahr != heute.year:
        return f"{heute.year}-{1:0{len(nr)}d}"
    return f"{jahr}-{int(nr) + 1:0{len(nr)}d}"


def iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


def auftrag_json(a: ra.Auftrag) -> dict:
    return {
        "ab_nr": a.ab_nr, "ab_datum": iso(a.ab_datum), "kunde": a.kunde, "strasse": a.strasse,
        "plz_ort": a.plz_ort, "kunden_nr": a.kunden_nr, "kontakt": a.kontakt, "projekt": a.projekt,
        "summe_netto": str(a.summe_netto), "zahlungsziel_tage": a.zahlungsziel_tage,
        "bestell_nr": a.bestell_nr, "bestell_datum": iso(a.bestell_datum),
        "liefertermin": iso(a.liefertermin), "rechnungs_mail": a.rechnungs_mail,
        "positionen": [{"titel": p.titel, "produkt": p.produkt, "einheit": p.einheit,
                        "menge": p.menge, "betrag": str(p.betrag),
                        "betrag_fmt": ra.fmt_betrag(p.betrag)} for p in a.positionen],
    }


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
        self.start = time.time()
        self.uploads: dict[str, Path] = {}
        self.tmp = Path(tempfile.mkdtemp(prefix="cs-rechnung-"))
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
            html = (HIER / "ui" / "index.html").read_text(encoding="utf-8")
            html = html.replace("__TOKEN__", z.token)
            z.letzter_kontakt = time.time()
            return self._antwort(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if self.path == "/api/ping-frei":  # für zweiten App-Start
            return self._antwort(200, {"ok": True})
        return self._fehler(404, "nicht gefunden")

    def do_POST(self):
        z = self.zustand
        if not self._token_ok():
            return self._fehler(403, "Zugriff verweigert")
        z.letzter_kontakt = time.time()
        try:
            if self.path == "/api/ping":
                return self._antwort(200, {"ok": True})
            if self.path == "/api/start":
                return self._start()
            if self.path == "/api/lesen":
                return self._lesen()
            if self.path == "/api/erstellen":
                return self._erstellen()
            if self.path == "/api/oeffnen":
                d = self._json()
                pfad = Path(d["pfad"])
                if not pfad.exists():
                    return self._fehler(404, "Datei nicht gefunden")
                oeffne(pfad, bool(d.get("finder")))
                return self._antwort(200, {"ok": True})
            if self.path == "/api/beenden":
                self._antwort(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return None
        except ra.AbFehler as e:
            return self._fehler(422, str(e))
        except Exception as e:  # noqa: BLE001
            return self._fehler(500, f"{type(e).__name__}: {e}")
        return self._fehler(404, "nicht gefunden")

    def _start(self):
        st = lade_status()
        heute = dt.date.today()
        return self._antwort(200, {
            "ordner": st.get("ordner") or str(STANDARD_ORDNER),
            "nummer": naechste_nummer(st.get("letzte_nummer"), heute),
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
        a = ra.lese_ab(pfad)
        with self.zustand.lock:
            self.zustand.uploads[uid] = pfad
        von = a.bestell_datum or a.ab_datum
        bis = a.liefertermin
        return self._antwort(200, {
            "id": uid, "auftrag": auftrag_json(a),
            "vorschlag": {"von": iso(von), "bis": iso(bis), "uebergabe": iso(bis)},
        })

    def _erstellen(self):
        d = self._json()
        pfad = self.zustand.uploads.get(d.get("id", ""))
        if not pfad:
            return self._fehler(400, "Bitte zuerst eine Auftragsbestätigung laden.")
        nr = (d.get("nummer") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,40}", nr):
            return self._fehler(422, "Rechnungsnummer fehlt oder enthält ungültige Zeichen.")

        def datum(k):
            v = d.get(k)
            return dt.date.fromisoformat(v) if v else None

        ordner = Path(os.path.expanduser((d.get("ordner") or str(STANDARD_ORDNER)).strip()))
        a = ra.lese_ab(pfad)
        ziel = ordner / f"{ra._dateiname(nr)}_Rechnung_{ra._dateiname(a.kunde)}.docx"
        if ziel.exists() and not d.get("ueberschreiben"):
            return self._antwort(409, {"fehler": f"{ziel.name} existiert bereits.", "existiert": True})
        ziel_pfad, r, hinweise = ra.erstelle_rechnung(
            pfad, nr, datum=datum("datum"), von=datum("von"), bis=datum("bis"),
            uebergabe=datum("uebergabe"), ust_id=(d.get("ust_id") or "").strip(),
            angebot=(d.get("angebot") or "").strip(), ausgabe=ziel, pdf=bool(d.get("pdf")))
        st = lade_status()
        st.update({"ordner": str(ordner), "letzte_nummer": nr, "pdf": bool(d.get("pdf"))})
        speichere_status(st)
        pdf_pfad = ziel_pfad.with_suffix(".pdf")
        return self._antwort(200, {
            "docx": str(ziel_pfad),
            "pdf": str(pdf_pfad) if d.get("pdf") and pdf_pfad.exists() else None,
            "netto": ra.fmt_betrag(r.netto), "ust": ra.fmt_betrag(r.ust),
            "brutto": ra.fmt_betrag(r.brutto), "faellig": ra.fmt_datum(r.faellig),
            "zeitraum": r.zeitraum,
            "hinweise": [re.sub(r"\s*\(--[\w-]+\)$", "", h) for h in hinweise if "überschreibbar" not in h],
            "naechste_nummer": naechste_nummer(nr, dt.date.today()),
        })


def waechter(server: ThreadingHTTPServer, z: Zustand):
    while True:
        time.sleep(3)
        jetzt = time.time()
        if z.letzter_kontakt is None:
            if jetzt - z.start > ERSTKONTAKT_S:
                break
        elif jetzt - z.letzter_kontakt > LEERLAUF_S:
            break
    server.shutdown()


def laufender_server() -> str | None:
    """URL einer bereits laufenden Instanz (dann nur Fenster öffnen)."""
    st = lade_status()
    url = st.get("laufend")
    if not url:
        return None
    try:
        basis = url.split("/", 3)
        with urllib.request.urlopen(f"{basis[0]}//{basis[2]}/api/ping-frei", timeout=1) as r:
            return url if r.status == 200 else None
    except OSError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kein-browser", action="store_true")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args(argv)

    url = laufender_server()
    if url and not args.kein_browser:
        webbrowser.open(url)
        return 0

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
        st = lade_status()
        if st.get("laufend") == url:
            st.pop("laufend")
            speichere_status(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
