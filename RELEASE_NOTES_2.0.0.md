# Water Softener Refill Sensor 2.0.0

- Aufnahme in den offiziellen HACS-Katalog: Installation über die HACS-Suche ist jetzt der Standardweg.
- Migration vorhandener Custom-Repository-Installationen dokumentiert: erst die Katalogverfügbarkeit prüfen, nur den Eintrag unter „Benutzerdefinierte Repositories“ entfernen, die Installation nicht deinstallieren, HACS aktualisieren und bestehende Einrichtung sowie Dashboards prüfen.
- Sicherheitskorrektur: nicht endliche und negative Wasserzählerwerte sowie Überläufe bei der Einheitenumrechnung werden verworfen; nach einer Datenlücke wird ein neuer Bezugswert gesetzt.
- Nicht endliche Salzangaben werden vor einer Zustandsänderung abgelehnt. Beschädigte numerische Speicherwerte verhindern den Start nicht mehr; ein ungültiger gespeicherter Salzbestand wird vorsichtig mit 0 kg wiederhergestellt.
- Sicherheits-Regressionstests für Sensorwerte, Dienste, Formulare und gespeicherte Werte ergänzt. Prüfumfang und Grenzen sind in `SECURITY_REVIEW.md` dokumentiert.

Die Major-Version markiert den HACS-Katalog-Meilenstein und bedeutet **nicht automatisch Breaking Changes**. Bestehende Konfigurationen, Dienste, Entity-IDs und Dashboard-Karten können weiterverwendet werden. Konfigurations- und Speicherformat bleiben auf Version 1.

Für bestehende Installationen siehe die Migrationsanleitung in der README. Dieses Dokument bereitet das Release vor; es wurde noch kein Release veröffentlicht.
