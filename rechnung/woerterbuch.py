"""Zugriff auf woerterbuch.json: gleichwertige Feldbezeichnungen in den Belegen.

Die Leser (rechnung_aus_ab.lese_ab, quelle_ariba) suchen Felder nie über festen Wortlaut,
sondern über die Bezeichnungslisten hier. Toleranz: Groß-/Kleinschreibung, optionaler
Doppelpunkt, beliebiger Leerraum zwischen den Wörtern; die längste passende Bezeichnung gewinnt.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DATEI = Path(__file__).resolve().parent / "woerterbuch.json"


@lru_cache(maxsize=1)
def laden() -> dict:
    return json.loads(DATEI.read_text(encoding="utf-8"))


def neu_laden() -> None:
    laden.cache_clear()


def liste(*pfad: str) -> list[str]:
    """Bezeichnungen zu einem Pfad, z. B. liste('ab', 'zahlungsziel')."""
    knoten = laden()
    for teil in pfad:
        knoten = knoten[teil]
    if not isinstance(knoten, list) or not knoten:
        raise KeyError(f"woerterbuch.json: {'/'.join(pfad)} ist keine Liste mit Einträgen")
    return knoten


def _muster(bezeichnung: str) -> str:
    """'Summe Auftrag (Netto)' → r'Summe\\s+Auftrag\\s*\\(\\s*Netto\\s*\\)' (Leerraum flexibel)."""
    teile = [re.escape(t) for t in bezeichnung.split()]
    m = r"\s+".join(teile)
    return m.replace(r"\(", r"\(\s*").replace(r"\)", r"\s*\)")


def nicht_gefunden(*pfad: str) -> str:
    """Fehlertext mit allen Bezeichnungen – zeigt, was im Wörterbuch ergänzt werden kann."""
    namen = liste(*pfad)
    return f"'{namen[0]}' nicht gefunden (Bezeichnungen laut woerterbuch.json: {', '.join(namen)})"


def rx(*pfad: str) -> str:
    """Regex-Alternative aller Bezeichnungen (längste zuerst), ohne Gruppe, case-insensitiv."""
    namen = sorted(liste(*pfad), key=len, reverse=True)
    return "(?i:" + "|".join(_muster(n) for n in namen) + ")"


def suche(text: str, pfad: tuple[str, ...], wert: str, vor: str = "", flags: int = 0,
          naechste_zeile: bool = False):
    """Bezeichnung + optionaler Doppelpunkt + Wert-Muster (mit Gruppe 1). Liefert re.Match oder None.
    Der Wert muss in derselben Zeile stehen (sonst träfe z. B. die Spaltenüberschrift 'Summe (Netto)'
    den Inhalt der Zeile darunter); naechste_zeile=True erlaubt einen Zeilenumbruch dazwischen."""
    zwischen = r"\s*:?\s*" if naechste_zeile else r"[ \t]*:?[ \t]*"
    return re.search(vor + rx(*pfad) + r"(?![A-Za-zÄÖÜäöüß])" + zwischen + wert, text, flags)


def beginnt_mit(text: str, *pfad: str) -> bool:
    return re.match(r"\s*" + rx(*pfad) + r"(?![A-Za-zÄÖÜäöüß])", text) is not None


def wort_passt(wort: str, *pfad: str) -> bool:
    """Einzelwort (Tabellenkopf) gegen die Liste – ohne Doppelpunkt, Groß-/Kleinschreibung egal."""
    w = wort.strip().rstrip(":").casefold()
    return any(w == n.rstrip(":").casefold() for n in liste(*pfad))


def erkannt(text: str, art: str) -> bool:
    """Belegart-Erkennung: alle Wörter aus 'alle' und mindestens eines aus 'eins_von' kommen vor."""
    regel = laden()["erkennung"][art]
    t = text.casefold()
    return all(a.casefold() in t for a in regel.get("alle", [])) and \
        any(e.casefold() in t for e in regel.get("eins_von", []))
