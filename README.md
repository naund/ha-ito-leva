# ha-ito-leva — Home Assistant Integration für ITO Leva! (Phase 1)

Custom Integration (HACS-fähig) + Stock-Dashboard für das ITO-Leva!-Modul
(`192.168.178.21:23`, MC-Protokoll über Telnet).

Umsetzungsstand: **Phase 1** aus `../HomeAssistant/home-assistant/Ranchilio.md`:

* Live-Telemetrie (Pressure / Flow / Temp / Weight / State / Setpoint / Timer), 1 Hz gedrosselt, `recorder`-exkludiert
* **Status-Sensor** `sensor.ito_leva_status` (`READY` / `HEATING` / `COOLING` / `WARM-UP` / `STEAM` / `ECO` / `STANDBY` / `BREWING` / `WAIT` / `OFFLINE`) — abgeleitet aus Sollwert, Kesseltemperatur und Heizleistung (`derive_status()`), weil der Rich-Modus **keine** Displaydaten liefert
* **Sollwert-Schreiben ist closed-loop**: eine `MC+`/`MC-`-Taste pro Schritt, Read-back nach jedem Schritt, Abbruch mit Fehler statt Blinden-Burst. Die Schrittweite ist **nicht** fest (Firmware `MC#` = 0,1/1,0/10 K) und wird am ersten Read-back gemessen
* **`switch.ito_leva_connection`** — Verbindung zur Maschine pausieren, ohne die Integration zu deaktivieren (Zustand wird persistiert; Rest der Entities wird `unavailable`)
* Start/Stop inkl. 3-s-Watchdog, Flush / Warm-Up / Pump PB, Backflush, Descale (**DE: Entkalken**)
* Start nutzt die aktive Dosis (`MCcDOSE`) statt Shortcut 1 — Shortcut 1 ist auf dieser Maschine **FLUSH**
* Standby/Eco über die verifizierten Kurzbefehle `MCE` / `MCe` (die langen `MCc…`-Formen ignoriert die Firmware)
* MCu-Setup als read-only Sensoren (`setup_pid_control`, `setup_pid_setpoint`, `setup_temp_type`), Alarm-Token → `persistent_notification`
* Phase 2 (Profil-Editor, Target-Kurve) und Phase 3 (Settings-Write-back) sind Non-Goals und nur als Stubs markiert.

## Protokoll-Herkunft (verifiziert, nicht geraten)

Portiert aus der Referenz `https://github.com/vocian/Leva-Companion`
(`proxy.js`, Stand 2026-04):

| Was | Quelle | Hier |
|---|---|---|
| Handshake `\nMC?\r` → `XML=` → `MCr\r` → `{`-Frames, Keepalive `\n`/7 s, Reconnect 3 s | `proxy.js` TCP-Layer | `client.py` |
| Rich-Frame Positionen (Pos 3 `*`, Pos 5–7 PID-Control, Pos 14–18 Sollwert, 153–155 ml, 156–160 Ticks/100 Hz, 175–180 °C, 198–202 bar, 203–212 Band, 227–231 g, 232–236 ml/s, 237–281 Profilnamen) | `parseRichFrame()` + Vendor-XML `at=…` | `parsers.py:parse_rich_frame()` |
| LCD-Frames `<BKL+4×16>`, `\x04`→`▶`, `\x05`→`✓`, `0xDF`→`°`, State-Regeln | `parseLevaDisplay()` | `parsers.py:parse_lcd_frame()` |
| Tastendruck `MC<byte>\r` + `MC\x00\r` nach 30 ms; Start: MCa-Wake, CANCEL aus MENÜ, aktive Dosis `MCcDOSE`; Stop `\x03` nur bei laufendem Programm | `mcPress`/`startShot`/`stopShot` (Startbefehl korrigiert) | `client.py` |
| Mutex nur für Langläufer (MCu/Nav/Upload), Kurzbefehle ohne Lock | `withItoLock()` | `client.py:ItoLevaClient.lock` |
| MCu-Dump: `MCu\r`, ~5 s sammeln, Pipe-Tiefe-Parser, danach `MCr\r` | `readMcuProfile`/`readMcuDoses` | `parsers.py:parse_mcu_tree()` |
| **Rich- und Display-Modus schließen sich aus** (`MCr` → nur `{…}`, `MC@` → nur `<…>`, live gemessen) | Firmware-Manual p.227 (`MCr`/`MC@`) + Live-Test | `client.py`, `parsers.py` |
| `MC+`/`MC-` = „Change setpoint“, Schrittweite über `MC#` (0,1/1,0/10 K), im Vendor-Frame `at=163` | Manual p.227, Vendor-XML `increment` | `const.py`, `coordinator.py:async_set_setpoint()` |
| Ticks bleiben nach dem Shot stehen (live: `00758` bei LCD „0s“) → `WAIT` nur mit Grace-Fenster | Live-Vergleich Rich-Frame ↔ LCD | `coordinator.py:_merge()` |
| Sollwert wird während Programm-/Ramp&Soak-Override ignoriert | Manual p.7 (Fußnote 2) | `coordinator.py` (Read-back-Abbruch) |
| Status ECO = wach + `Setup > PID 1 > Eco` (live 81,3 °C vs. gespeichert 82,3 °C) | Manual p.196, MCu-Dump | `parsers.py:derive_status()` |
| Warm-Up-Override: `PID 1 > Ramp&Soak > Warm-Up > 1 > Target` = 110,0 °C; Schritt 2 (`Target PID`, 6m30s) gibt an die PID zurück | Live-Frame 110,0 °C bei gespeichertem 94,0 °C + MCu-Dump | `coordinator.py:override_reason` |
| Dampf: PID umgangen → Kessel über Sollwert **mit** Heizleistung (≥50 %), sonst COOLING | Live-Frame `at=18` (Heat PID) + Manual | `parsers.py:derive_status()` |

## Installation (HACS)

1. HACS → Integrationen → `⋮` → Custom repositories → URL dieses Repos, Kategorie Integration.
2. Installieren, HA neu starten.
3. Einstellungen → Geräte & Dienste → Integration hinzufügen → **ITO Leva!** → Host (Default `192.168.178.21`), Port (Default `23`).
4. Dashboard aus `dashboards/ito-leva.yaml` übernehmen (Stock-Cards, keine Custom-Card nötig). Die Entity-IDs sind fest verdrahtet (`sensor.ito_leva_temp`, `number.ito_leva_setpoint`, `text.ito_leva_lcd_mirror`, …).

## Recorder entlasten (Pflicht für Phase 1)

Schnelle Sensoren nicht aufzeichnen — in `configuration.yaml` der HA-Instanz:

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.ito_leva_pressure
      - sensor.ito_leva_flow*
      - sensor.ito_leva_weight
      - sensor.ito_leva_volume
```

Damit landen diese Sensoren **nicht** im Verlauf; sichtbar bleibt nur die
5-Minuten-Langzeitstatistik (More-Info → Statistik). Für den Vergleich
„Flowmeter vs. Waagen-Flow“ und für das Druckregelungs-Tuning gibt es drei
Wege:

1. **`ito_leva.export_recent`** — schreibt die Roh-Frames aus dem
   Coordinator-Speicher (voller Takt ~4 Hz, ca. 2–3 Minuten) als CSV nach
   `/config/ito_leva/recent_*.csv`, ohne die Recorder-Konfiguration zu ändern.
   Optional Feld `minutes`. Rückgabe: Pfad und Frame-Anzahl.
2. **`tools/record_shot.py`** — Rohdaten + Auswertung (Überschwingen,
   Phasen-Streuung, Flowmeter gegen Volumen-Steigung); dafür
   `switch.ito_leva_connection` ausschalten (das Modul bedient einen Client).
3. **Shot-Sensoren `sensor.ito_leva_shot_*`** (siehe unten) — diese sind nur
   während eines Shots verfügbar und werden deshalb vom Recorder nur dann
   gespeichert; Verlauf und 5-Minuten-Statistik pro Shot ohne dauerhaftes
   Datenbankwachstum.

### Shot-Aufzeichnung ohne Datenbank-Wachstum

Sechs Sensoren sind **nur während eines Programms verfügbar** (`PULLING`,
`BREWING`, `FLUSHING`, `WAIT`), sonst `unavailable`:

| Entity | Inhalt |
|---|---|
| `sensor.ito_leva_shot_flow` | Flowmeter, ml/s (das ist der Wert ohne Waage) |
| `sensor.ito_leva_shot_flow_scale` | Flow aus der Waage, g/s (nur mit Waage) |
| `sensor.ito_leva_shot_volume` | Volumen, ml |
| `sensor.ito_leva_shot_pressure` | Druck, bar |
| `sensor.ito_leva_shot_pressure_sp` | Druck-Sollwert, bar |
| `sensor.ito_leva_shot_phase` | Pumpenphase, ° — Attribute: `phase_p`, `phase_i`, `phase_bias`, `pid_output` |

Home Assistant schreibt einen State nur bei Änderung: außerhalb eines Shots
steht die Entität auf `unavailable` (eine Zeile), während des Shots werden die
1-Hz-Werte aufgezeichnet. Ein 30-s-Shot erzeugt so ca. 200 Zeilen für alle sechs
Sensoren zusammen. **Diese Entities nicht in `recorder.exclude` aufnehmen** —
sie sind genau für die Aufzeichnung gedacht. `sensor.ito_leva_flow*` matcht sie
nicht (Präfix `shot_`), die Live-Sensoren bleiben also weiter draußen.

Die 4-Hz-Rohdaten eines Shots liefert weiterhin `export_recent` bzw.
`tools/record_shot.py`; die Shot-Sensoren sind die bequeme 1-Hz-Spur für
Verlauf/Statistik und den Vergleich mehrerer Shots.

## Dienste

* `ito_leva.refresh_setup` — MCu-Dump neu einlesen (Setup-Sensoren aktualisieren).
* `ito_leva.start_shot` / `ito_leva.stop_shot` — inkl. Watchdog (3 s auf PULLING/BREWING, sonst Fehler + Notification).
* `ito_leva.export_recent` — Roh-Frames aus dem Speicher als CSV (`/config/ito_leva/`), Feld `minutes` optional; Rückgabe mit Pfad und Frame-Anzahl.

## Sollwert ändern (warum es jetzt anders funktioniert)

Die Firmware kennt nur **relative** Sollwert-Tasten (`MC+`/`MC-`). Die Größe
eines Schritts stellt `MC#` ein (`0,1`, `1,0` oder `10 K`); auf der
Referenzmaschine steht sie auf `1,0 K`. Ein absoluter Wunschwert („95,0 °C“)
wird deshalb nicht mehr in eine gezählte Tastenfoge umgerechnet, sondern
Schritt für Schritt gefahren und nach jedem Schritt aus dem Rich-Frame
zurückgelesen. Ist die Maschine nicht bereit (laufende Pumpe, Programm-/
Ramp&Soak-Override) bricht die Änderung mit einer Fehlermeldung ab, statt
weiterzudrücken.

Nicht änderbar ist der Sollwert, solange ein Programm ihn überschreibt — das
ist **kein Fehler**, sondern ein normaler Betriebszustand:

* **Aufwärmen (Warm-Up / Ramp & Soak):** Der Kessel heizt auf das konfigurierte
  Warm-Up-Ziel (`Setup > PID 1 > Ramp&Soak > Warm-Up > 1 > Target`, z. B.
  110 °C) und spült danach die Brühgruppe. In dieser Phase zeigt
  `number.ito_leva_setpoint` diesen Wert; ein Änderungswunsch wird abgelehnt,
  ohne Tasten zu drücken.
* **Aufheizen für Dampf:** Die PID-Regelung ist umgangen, der Kessel steigt
  über den Brew-Sollwert (hohe Heizleistung). Auch hier keine Sollwert-Änderung.

`sensor.ito_leva_status` zeigt dann `WARM-UP` bzw. `STEAM`; die Entität
`number.ito_leva_setpoint` hat die Attribute `locked` und `lock_reason`, damit
das Dashboard die Sperre erklären kann. Sobald das Programm den Sollwert wieder
an die PID-Regelung zurückgibt, ist die Änderung erneut möglich.

## Druckregelung (PI) vermessen

Die Druckregelung der Pumpe ist laut Firmware-Manual (p. 109) ein
PI-Regler mit Bias:

```
Output[°] = Bias + K · Fehler + Σ (I · Fehler)        Zyklus 200 ms
```

Getrennte Parametersätze gibt es für **< 2,5 bar** (Preinfusion) und
**> 2,5 bar** (dort zusätzlich `K>SP` / `K<SP`), außerdem P-Filter,
`Flow corr` und `Heat corr`. Der Rich-Frame liefert genau die Größen, die das
Status-Monitor-Diagramm zeichnet: `pressure`, `pressure_sp` (Feld `at=192`),
`phase` (212), `phase_bias` (215), `phase_p` (218), `phase_i` (222) — zusätzlich
`band_low`/`band_high` und `flow_ml`.

`tools/record_shot.py` schreibt diese Daten read-only in CSV (Handshake +
Keepalive, sonst nichts; `--pressure-test` nutzt nur den Lese-Dump `MCb`):

```bash
# vorher switch.ito_leva_connection ausschalten (das Modul bedient einen Client)
python3 tools/record_shot.py --out tuning/          # Shots ziehen, Ctrl-C beendet
python3 tools/record_shot.py --pressure-test --out tuning/   # statische Kennlinie
```

Pro Shot entsteht `tuning/shot_NNN.csv` (alle Frames), zusätzlich
`session.csv`. Die Zusammenfassung nennt Überschwingen, Regeldifferenz,
Phasen-Streuung (Hunting) und prüft `phase = bias + P + I`.

**Wie viele Shots?** Ein einzelner Shot reicht nicht:

1. Die **statische Kennlinie** (Phase → Druck ohne Flow) kommt aus dem
   Drucktest-Programm der Maschine (`MCb`-Dump), nicht aus einem Shot.
2. Es gibt **zwei Parametersätze** (< 2,5 bar und > 2,5 bar) — ohne Daten in
   beiden Bereichen lässt sich nur die Hälfte beurteilen.
3. Ein Shot ist eine Messung: Mahlgrad/Puck und Sensorrauschen überlagern die
   Regelantwort. Für „K zu groß“ vs. „Channeling“ braucht es Wiederholungen.

Praktischer Fahrplan: 1× Drucktest, dann **3 Shots** mit konstantem
Brew-Druck (z. B. 9 bar) als Basislinie, **3 Shots** mit ~1,5–2 bar
(Preinfusionsbereich), optional 1–2 Profile mit mehreren Druckstufen
(z. B. 2 → 6 → 9 → 6 bar). Nach jeder Parameteränderung (erst `K`, dann `I`)
**2 Shots** zur Kontrolle, am Ende **3 Shots** zur Validierung.

Der Rich-Stream läuft mit ~4 Hz (250 ms) — etwas langsamer als der 200-ms-
Regelzyklus. Für Hüllkurven (Überschwingen, Einschwingzeit, Phasen-Hunting)
reicht das; Reglerinterne Dynamik feiner als 250 ms ist so nicht sichtbar.

## Begriffsklärung (DE)

* **Descale = Entkalken** — abgelagerten Kalk (limescale) aus dem Kessel entfernen.
* **Scale = Waage** — Bluetooth-Waage für Gewicht/Flow (g/s). Nicht mit Kalk verwechseln.
* Alarm `[SCALE]` → Waage fehlt; `[DSCALE]` → Entkalken fällig.

## Testen

Die Testumgebung liegt projektlokal in `.venv` (gitignored) und ist auf
Home Assistant 2026.10.4 gepinnt:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt

# Offline-Parser + echte HA-Integrationstests (FakeIto)
.venv/bin/python -m pytest
```

Nur die reinen Protokoll-/Parser-Tests ohne HA-Runtime:

```bash
python3 -m pytest tests/test_parsers.py
```

Die HA-Tests in `tests/integration/` kopieren die Integration pro Test in ein
temporäres HA-Config-Verzeichnis und sprechen mit `FakeIto` über echtes TCP.
Details und Live-Geräte-Regeln stehen in `AGENTS.md`.

## Roadmap

* Phase 2: 10-Hz-Ringpuffer im Coordinator ist vorbereitet (`coordinator.py`), Profil-Editor + Target-Kurve via `buildProfileCurve`-Port (Python/TS).
* Phase 3: Settings-Write-back (MCu-Schema `SETTINGS_SECTIONS`), Wizards.
* Release-Metadaten sind gesetzt (`single_config_entry`, Codeowner-Liste, Doku-/Issue-URL, HACS-Mindestversion 2026.10).
