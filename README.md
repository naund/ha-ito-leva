# ha-ito-leva — Home Assistant Integration für ITO Leva! (Phase 1)

Custom Integration (HACS-fähig) + Stock-Dashboard für das ITO-Leva!-Modul
(`192.168.178.21:23`, MC-Protokoll über Telnet).

Umsetzungsstand: **Phase 1** aus `../HomeAssistant/home-assistant/Ranchilio.md`:

* Live-Telemetrie (Pressure / Flow / Temp / Weight / State / Timer), 1 Hz gedrosselt, `recorder`-exkludiert
* Start/Stop inkl. 3-s-Watchdog, Setpoint ±0,1 °C, Flush / Warm-Up / Pump PB, Backflush, Descale (**DE: Entkalken**)
* MCu-Setup als read-only Sensoren, Alarm-Token → `persistent_notification`
* Phase 2 (Profil-Editor, Target-Kurve) und Phase 3 (Settings-Write-back) sind Non-Goals und nur als Stubs markiert.

## Protokoll-Herkunft (verifiziert, nicht geraten)

Portiert aus der Referenz `https://github.com/vocian/Leva-Companion`
(`proxy.js`, Stand 2026-04):

| Was | Quelle | Hier |
|---|---|---|
| Handshake `\nMC?\r` → `XML=` → `MCr\r` → `{`-Frames, Keepalive `\n`/7 s, Reconnect 3 s | `proxy.js` TCP-Layer | `client.py` |
| Rich-Frame Positionen (Pos 3 `*`, 153–155 ml, 156–160 Ticks/100 Hz, 175–180 °C, 198–202 bar, 203–212 Band, 227–231 g, 232–236 ml/s, 237–281 Profilnamen) | `parseRichFrame()` | `parsers.py:parse_rich_frame()` |
| LCD-Frames `<BKL+4×16>`, `\x04`→`▶`, `\x05`→`✓`, `0xDF`→`°`, State-Regeln | `parseLevaDisplay()` | `parsers.py:parse_lcd_frame()` |
| Tastendruck `MC<byte>\r` + `MC\x00\r` nach 30 ms; Start-Sequenz (MCa-Wake ×3, CANCEL ×4, `\x05`); Stop `\x03` | `mcPress`/`startShot`/`stopShot` | `client.py` |
| Mutex nur für Langläufer (MCu/Nav/Upload), Kurzbefehle ohne Lock | `withItoLock()` | `client.py:ItoLevaClient.lock` |
| MCu-Dump: `MCu\r`, ~5 s sammeln, Pipe-Tiefe-Parser, danach `MCr\r` | `readMcuProfile`/`readMcuDoses` | `parsers.py:parse_mcu_tree()` |

## Installation (HACS)

1. HACS → Integrationen → `⋮` → Custom repositories → URL dieses Repos, Kategorie Integration.
2. Installieren, HA neu starten.
3. Einstellungen → Geräte & Dienste → Integration hinzufügen → **ITO Leva!** → Host (Default `192.168.178.21`), Port (Default `23`).
4. Dashboard aus `dashboards/ito-leva-phase1.yaml` übernehmen (Stock-Cards, keine Custom-Card nötig).

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

## Dienste

* `ito_leva.refresh_setup` — MCu-Dump neu einlesen (Setup-Sensoren aktualisieren).
* `ito_leva.start_shot` / `ito_leva.stop_shot` — inkl. Watchdog (3 s auf PULLING/BREWING, sonst Fehler + Notification).

## Begriffsklärung (DE)

* **Descale = Entkalken** — abgelagerten Kalk (limescale) aus dem Kessel entfernen.
* **Scale = Waage** — Bluetooth-Waage für Gewicht/Flow (g/s). Nicht mit Kalk verwechseln.
* Alarm `[SCALE]` → Waage fehlt; `[DSCALE]` → Entkalken fällig.

## Testen (ohne HA-Runtime)

Reine Protokoll-Module sind offline testbar:

```bash
python3 -m pytest tests/ -q
```

Plattform-Module (`sensor.py`, `button.py`, …) brauchen eine HA-Umgebung;
hier nur per `python3 -m py_compile` auf Syntax geprüft.

## Roadmap

* Phase 2: 10-Hz-Ringpuffer im Coordinator ist vorbereitet (`coordinator.py`), Profil-Editor + Target-Kurve via `buildProfileCurve`-Port (Python/TS).
* Phase 3: Settings-Write-back (MCu-Schema `SETTINGS_SECTIONS`), Wizards.
* TODO vor Release: `manifest.json` (codeowners, issue_tracker, Doku-URL) auf echtes Repo zeigen lassen.
