# EV Invoice App (Nextcloud TSV)

Diese Variante nutzt deine bestehende TSV-Datei auf der Nextcloud:
`Datum\tZaehlerstand\tStrompreis\tVerbrauch\tAbrechnung`

## Quickstart
```bash
cp .env.example .env
# .env mit Nextcloud + SMTP + Auth füllen
docker compose up --build -d
# http://localhost:8080
```

## Was entspricht deinem Node-RED-Flow?
- Datei lesen/schreiben via WebDAV (NC_BASE_URL/NC_USERNAME/NC_PASSWORD/NC_FILEPATH)
- TSV-Format beibehalten (Tab getrennt, deutsche Datumsform "DD.MM.YYYY")
- Verbrauch/Abrechnung werden vor dem Append berechnet
- PDF: Kopfzeile mit Adresse, Tabelle (letzte 24 Zeilen) + neue Zeile fett, Fußzeile mit Seitenzahlen
- E-Mail: Betreff/Body wie bisher, mehrere Empfänger (Komma-getrennt)

## Fallback
Wenn NC_* nicht gesetzt ist, nutzt die App `LOCAL_TSV` aus `./data`.
