# Security-Review zur Vorbereitung von 2.0.0

Stand: 24.10.2026. Geprüfte Ausgangsbasis: `c469f2cb8d23dca364461f771a945f07f14c79ac` (Manifest 1.2.4). Der Review umfasst alle Python-Module der Integration, Manifest, Dienste, Tests und GitHub-Workflows. Zeilenangaben in der Befundtabelle beziehen sich auf diese Ausgangsbasis.

## Bestätigte Befunde

| ID | Repository | Datei / Zeile | Risiko | Schweregrad | Korrektur und Status |
|---|---|---|---|---|---|
| WS-01 | home-assistant-water-softener-refill-sensor | `custom_components/water_softener_refill_sensor/manager.py:215`, `logic.py:201` | Externe Sensorzustände `NaN`/`Infinity` wurden als Zahlen akzeptiert. Sie konnten Bezugswert und Fensterverbrauch vergiften, Regenerationen vortäuschen oder die weitere Erkennung verhindern. Auch Einheitenumrechnung und Addition endlicher Extremwerte konnten überlaufen. | Mittel | Behoben: Endlichkeitsprüfung vor der Verarbeitung und nach Umrechnung; negatives Zählwerk wird verworfen, ein neuer Bezugswert nach der Datenlücke verhindert Nachzählung. Das Modell verhindert nicht endliche Werte und Additionsüberläufe zusätzlich selbst. |
| WS-02 | home-assistant-water-softener-refill-sensor | `custom_components/water_softener_refill_sensor/__init__.py:37`, `manager.py:295`, `logic.py:277` | Dienste und manuelle Eingaben ließen nicht endliche Salzmengen durch; `Infinity` konnte einen vollen Behälter bestätigen, `NaN` fehlerhafte Bestände erzeugen. Numerische Formularfelder benötigten ebenfalls eine ausdrückliche Endlichkeitsprüfung. Der Aufruf erfordert bereits einen authentifizierten HA-Zugriff. | Niedrig | Behoben: Dienste, Manager, Modell und Formularnormalisierung lehnen nicht endliche Eingaben vor einer Zustandsänderung ab. Fehlermeldungen sind auf Deutsch und Englisch vorhanden. |
| WS-03 | home-assistant-water-softener-refill-sensor | `custom_components/water_softener_refill_sensor/logic.py:92`, `logic.py:99`, `logic.py:353` | Gespeicherte nicht endliche Werte wurden übernommen; `int(float("inf"))` verursachte einen nicht abgefangenen `OverflowError` beim Laden. Beschädigte lokale Daten konnten dadurch den Integrationsstart verhindern. | Niedrig | Behoben: numerische Speicherwerte werden auf Endlichkeit geprüft; ungültige Zähler fallen auf 0, der Bezugswert auf `None` zurück. Ein explizit ungültiger Salzbestand wird mit 0 kg statt vermeintlich vollem Behälter wiederhergestellt. |

Die Ausgangsbasis wurde reproduzierbar geprüft: `NaN` wurde in `last_value_l` gespeichert, ein unendlicher gespeicherter Zähler löste `OverflowError` aus und eine unendliche Nachfüllmenge wurde akzeptiert. Die Regressionstests prüfen diese Fälle nun mit `NaN`, positiver/negativer Unendlichkeit und numerischen Überlaufzeichenketten.

## Weitere geprüfte Bereiche

- **Dateien und Pfade:** Die Integration übergibt ausschließlich einen festen Domain-Präfix und die von HA erzeugte Config-Entry-ID an `homeassistant.helpers.storage.Store`. Der vom Dienst gewählte `config_entry` wird gegen bereits geladene Manager geprüft und nicht als Dateipfad verwendet. Keine benutzerdefinierten Dateipfade oder eigenen synchronen Dateioperationen im Laufzeitcode gefunden.
- **Geheimnisse:** Keine hartcodierten Zugangsdaten, eigenen Tokens oder geheimen Konfigurationsfelder im Laufzeitcode gefunden. Der Review hat keine Git-Historie auf bereits entfernte Geheimnisse durchsucht.
- **Netzwerk und Berechtigungen:** Keine eigenen HTTP-, WebSocket- oder sonstigen Netzwerkclients. Dienste laufen innerhalb der Home-Assistant-Authentifizierung. Manuelle Bestands- und Zählerkorrekturen durch berechtigte HA-Nutzer sind vorgesehene Funktionen; die Integration setzt keine eigene Administratorrolle voraus.
- **Externe Daten:** Zustände und Volumeneinheiten des ausgewählten HA-Sensors sind die externe Eingabe. Einheiten werden gegen die HA-Volumeneinheiten und feste Aliase geprüft. Die ergänzten Prüfungen verhindern nicht endliche Rechenwerte; fachlich falsche, aber gültige Sensorwerte können weiterhin falsche Regenerationen ergeben.
- **Event-Loop:** Speicheroperationen nutzen `Store.async_load`, `async_delay_save`, `async_save` und `async_remove`; keine eigenen blockierenden Netzwerk- oder Dateizugriffe gefunden. Zeitplanung nutzt HA-Callbacks.
- **HTML/XSS:** Dieses Repository liefert keine Dashboard-Karte und verwendet keine eigenen DOM-Senken. Meldungen werden an Home Assistants `persistent_notification` übergeben; deren Frontend-Verarbeitung liegt bei HA.
- **Abhängigkeiten:** `manifest.json` enthält `requirements: []`; es gibt keine direkt mitzuliefernden Drittanbieter-Pakete. Der HA-Test-Stack wird separat über `requirements_test.txt` gepflegt. Dies ist keine Zusicherung, dass Home Assistant und seine transitiven Abhängigkeiten frei von Schwachstellen sind.

## Prüfung der Änderungen

- `python -m unittest discover -s tests -q`: **75 Tests bestanden** (Ausgangsbasis: 68). Neue Prüfungen decken Sensordaten, Wiederaufnahme nach einer Lücke, Einheitenüberlauf, Additionsüberlauf, manuelle Eingaben, Formularnormalisierung und Wiederherstellung beschädigter Zahlen ab.
- Mit realem Home Assistant 2026.10.2 und dessen Validatoren: **8 direkte Schema-Prüfungen bestanden** (2 Salz-Dienste × `NaN`, `Infinity`, `-Infinity`, `"1e999"`). Die entsprechenden parametrisierten Tests stehen in `tests_ha/test_integration.py`.
- `python -m compileall -q custom_components tests tests_ha`: bestanden.
- `git diff --check`: bestanden.
- Der vollständige lokale `pytest tests_ha -q`-Lauf mit dem zusätzlichen HA 2026.10.2 wurde nach Hängen im Testaufbau/-abbau beendet; kein vollständiges Ergebnis. Die vom Projekt verlangte CI-Matrix mit HA 2024.12.0 und 2026.2.3 muss vor dem Merge erfolgreich sein. Ein physischer Test mit Wasserzähler und Enthärtungsanlage wurde nicht durchgeführt.

## Verbleibende Risiken und Release-Freigabe

Keine bestätigten offenen hohen oder kritischen Sicherheitsbefunde in diesem Prüfumfang. Die Integrationsfunktion bleibt eine rechnerische Schätzung; vertrauenswürdige Sensoren und die Kontrolle des realen Salzbehälters bleiben erforderlich. Beschädigte gespeicherte Werte können keine verlässliche historische Rekonstruktion liefern und müssen gegebenenfalls manuell korrigiert werden.

**Vor Merge erforderlich:** grüne vorhandene Validate-/Hassfest-/HACS-Prüfungen und die HA-Testmatrix sowie Review der Sicherheitskorrektur. Der vorhandene `release.yml` veröffentlicht nach erfolgreichem Validate-Push auf `main` automatisch einen fehlenden Release. Der vorbereitende Pull Request darf deshalb erst gemergt werden, wenn die Veröffentlichung ausdrücklich freigegeben oder dieser Automatismus separat kontrolliert ist. In diesem Auftrag werden weder Merge, Tag noch Release ausgelöst.

Die Major-Version 2.0.0 dokumentiert den offiziellen HACS-Katalog und seine Migration; sie bedeutet nicht automatisch Breaking Changes. `ConfigFlow.VERSION` und `STORAGE_VERSION` bleiben 1, da kein Formatwechsel erforderlich ist.
