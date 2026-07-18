/**
 * telemetry_sink.gs — de "server" voor SamFlow's anonieme heartbeat, zonder server.
 *
 * Dit is een Google Apps Script dat bij een Google Sheet hoort. Het ontvangt de
 * dagelijkse heartbeat van telemetry.py en schrijft één regel per ping. De data
 * blijft in JOUW eigen Google-account — geen third party, geen onderhoud.
 *
 * OPZETTEN (eenmalig, ~2 minuten):
 *   1. Maak een nieuwe Google Sheet (sheets.new). Noem 'm bv. "SamFlow heartbeat".
 *      Zet in rij 1 kopjes:  tijd | id | versie | os | dag
 *   2. Extensies → Apps Script. Plak deze hele inhoud, sla op.
 *   3. Implementeren (Deploy) → Nieuwe implementatie → type: Web-app.
 *        Uitvoeren als: ikzelf
 *        Wie heeft toegang: iedereen
 *      Implementeren → sta toegang toe aan je eigen account.
 *   4. Kopieer de web-app-URL (eindigt op /exec).
 *   5. Plak die URL in telemetry.py bij HEARTBEAT_URL, commit + push, herstart de app.
 *
 * TELLEN: in de Sheet geeft  =COUNTUNIQUE(B2:B)  het aantal unieke installaties.
 *   Voor "actief deze week": filter kolom "dag" op de laatste 7 dagen en tel unieke id's.
 *
 * TESTEN vóór je 'm uitrolt:
 *   curl -X POST -H "Content-Type: application/json" \
 *     -d '{"id":"test","version":"x","os":"15","day":"2026-07-17"}' <jouw /exec URL>
 *   -> er hoort een regel in de Sheet te verschijnen.
 *
 * LET OP: de /exec-URL staat straks in de publieke repo, dus iemand zou 'm kunnen
 * spammen. Dedupe op id (COUNTUNIQUE) vangt dubbele/rommel-pings op. Wordt het ooit
 * een probleem, stap dan over op een Cloudflare Worker met rate-limiting.
 */
function doPost(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var d = {};
  try { d = JSON.parse(e.postData.contents); } catch (err) {}
  sheet.appendRow([new Date(), d.id || "", d.version || "", d.os || "", d.day || ""]);
  return ContentService.createTextOutput("ok");
}
