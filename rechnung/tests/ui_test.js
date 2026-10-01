// Browser-Test der Oberfläche (Funde 2, 6, 7 + Grundablauf).
// Aufruf: node tests/ui_test.js <URL der laufenden App> <AB-A.pdf> <AB-B.pdf> <Arbeitsordner>
// Voraussetzung: npm-Paket playwright.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const [url, abA, abB, arbeit] = process.argv.slice(2);
  const fehler = [];
  const pruefe = (bed, text) => { console.log((bed ? 'OK   ' : 'FEHL ') + text); if (!bed) fehler.push(text); };
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1100, height: 1500 }, locale: 'de-DE' });
  p.on('pageerror', e => fehler.push('JS: ' + e.message));
  await p.goto(url);
  await p.waitForFunction(() => document.getElementById('f-nummer').value !== '');

  // Fund 7: präparierter Dateiname darf kein HTML/Script werden
  const boese = path.join(arbeit, '<img src=x onerror="window.__xss=1">.pdf');
  fs.copyFileSync(abA, boese);
  await p.setInputFiles('#datei', boese);
  await p.waitForSelector('#auftrag:not(.versteckt)');
  pruefe(await p.evaluate(() => window.__xss === undefined), 'Dateiname wird nicht als HTML ausgeführt');
  pruefe((await p.textContent('#ablage')).includes('<img'), 'Dateiname erscheint als Text');

  // Grundablauf A (OHB-AB: Bestellnummer 45104423 vorbelegt, hier überschrieben)
  pruefe(await p.inputValue('#f-bestellnr') === '45104423', 'Bestellnummer der OHB-AB vorbelegt');
  await p.fill('#f-bestellnr', 'PO-4711');
  await p.fill('#f-nummer', '2026-0500');
  await p.fill('#f-ordner', path.join(arbeit, 'rechnungen'));
  await p.fill('#f-ustid', 'DE123456789');
  await p.fill('#f-angebot', '2026-014');
  await p.click('#erstellen');
  await p.waitForSelector('#ergebnis.meldung');
  pruefe((await p.textContent('#ergebnis')).includes('Rechnung erstellt'), 'Rechnung A erstellt');
  pruefe(await p.inputValue('#f-nummer') === '2026-0501', 'nächste Nummer vorgeschlagen');
  // Historie
  await p.waitForSelector('#historie-liste tr.neu');
  const zeile = await p.textContent('#historie-liste tr.neu');
  pruefe(zeile.includes('2026-0500') && zeile.includes('OHB SE') && zeile.includes('5.355,00 €'),
         'neue Rechnung oben in der Historie markiert');
  await p.fill('#h-suche', 'gibtsnicht');
  pruefe((await p.textContent('#historie-liste')).includes('Keine Treffer'), 'Suche filtert');
  await p.fill('#h-suche', 'ohb');
  const treffer = await p.locator('#historie-liste tbody tr').allTextContents();
  pruefe(treffer.length >= 1 && treffer.every(t => t.includes('OHB')), 'Suche findet nur passende Kunden');
  await p.fill('#h-suche', '');
  await p.screenshot({ path: path.join(arbeit, 'ui_historie.png'), fullPage: true });

  // Fund 2: neue AB → USt-ID/Angebot geleert
  await p.setInputFiles('#datei', abB);
  await p.waitForFunction(() => document.getElementById('ablage').textContent.includes('✓'));
  pruefe(await p.inputValue('#f-ustid') === '' && await p.inputValue('#f-angebot') === '',
         'USt-IdNr./Angebot nach AB-Wechsel geleert');
  pruefe(await p.isHidden('#ergebnis'), 'altes Ergebnis ausgeblendet');
  pruefe(await p.inputValue('#f-bestellnr') !== '', 'Bestellnummer aus der AB vorbelegt');

  // Fund 6: Server weg → Meldung, Knopf wieder bedienbar
  await p.evaluate(() => fetch('/api/beenden', { method: 'POST', headers: { 'X-Token': TOKEN } }));
  await p.waitForTimeout(1500);
  await p.click('#erstellen');
  await p.waitForFunction(() => !document.getElementById('erstellen').disabled);
  pruefe(await p.isVisible('#getrennt'), 'Hinweis "keine Verbindung" sichtbar');
  pruefe(await p.textContent('#erstellen') === 'Rechnung erstellen', 'Knopf zurückgesetzt');
  await p.screenshot({ path: path.join(arbeit, 'ui_getrennt.png') });

  await b.close();
  console.log(fehler.length ? `FEHLER: ${fehler.length}` : 'ALLE UI-PRÜFUNGEN OK');
  process.exit(fehler.length ? 1 : 0);
})();
