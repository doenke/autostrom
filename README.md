# Autostrom – EV Invoice App

Ein kleines FastAPI-Tool, das deinen Node-RED-Flow für die Autostrom-Abrechnung ablöst. Die App liest und schreibt deine bestehende TSV-Datei (lokal oder per Nextcloud-WebDAV), berechnet Verbrauch und Kosten, erstellt ein PDF mit den letzten Einträgen und versendet optional die Rechnung per E-Mail. Über OIDC kann die Oberfläche abgesichert werden, und fertige PDFs lassen sich auf Wunsch an Paperless-ngx hochladen.

## Features
- **TSV-kompatibel:** Behält das Format `Datum\tZaehlerstand\tStrompreis\tVerbrauch\tAbrechnung` bei und nutzt Nextcloud-WebDAV oder eine lokale Datei.
- **Automatische Berechnungen:** Verbrauch und Abrechnung werden aus der Zählerstandsdifferenz ermittelt und validiert.
- **PDF-Erzeugung:** Erstellt ein übersichtliches PDF mit Kopfzeile, Tabelle (letzte 24 Zeilen + aktuelle Zeile fett) und Seitennummern.
- **E-Mail-Versand:** Versand an mehrere Empfänger mit eigenem Betreff/Text und PDF-Anhang.
- **Paperless-Integration:** Optionaler Upload des PDFs inklusive Tags, Correspondent und Document Type.
- **OIDC-Schutz:** Login via OpenID Connect; ohne OIDC ist die Oberfläche offen erreichbar.

## Schnellstart (Docker Compose)
1. `.env` anlegen oder bestehende Datei anpassen (siehe Variablen unten).
2. Container starten:
   ```bash
   docker compose up --build -d
   ```
3. Oberfläche im Browser öffnen: <http://localhost:8089>
4. Daten und erzeugte PDFs liegen im Host-Ordner `./data`.

> Hinweis: Der bereitgestellte `docker-compose.yml` baut direkt aus dem Git-Repo und mappt Port `8089` auf den internen FastAPI-Port `8000`.

## Konfiguration
Alle Einstellungen erfolgen über Umgebungsvariablen (z. B. in `.env`).

### Nextcloud / Dateien
- `NC_BASE_URL` – Basis-URL deiner Nextcloud (inkl. `https://`).
- `NC_USERNAME` / `NC_PASSWORD` – Zugangsdaten für WebDAV.
- `NC_FILEPATH` – Pfad zur TSV-Datei (Standard: `Zaehlerstaende/Autostrom.csv`).
- `LOCAL_TSV` – Fallback-Pfad, wenn keine Nextcloud-Daten gesetzt sind (Standard: `/app/data/Autostrom.csv`).

### PDF-Inhalte
- `PDF_NAME` – Name/Absender in der Kopfzeile.
- `PDF_STREET` – Straße/Hausnummer.
- `PDF_CITY` – PLZ + Ort.

### E-Mail
- `SMTP_HOST` / `SMTP_PORT` – SMTP-Server und Port (Standard 587).
- `SMTP_USER` / `SMTP_PASSWORD` – Authentifizierung.
- `SMTP_SSL` – `true` für SSL, sonst STARTTLS.
- `MAIL_FROM` – Absenderadresse (Default: `SMTP_USER`).
- `MAIL_TO` – Kommagetrennte Empfängerliste.

### Paperless-ngx (optional)
- `PAPERLESS_URL` – Basis-URL, z. B. `https://paperless.example.com`.
- `PAPERLESS_TOKEN` – API-Token.
- `PAPERLESS_TAGS` – Kommagetrennte Tag-Namen.
- `PAPERLESS_CORRESPONDENT` – Name des Correspondent.
- `PAPERLESS_DOCUMENT_TYPE` – Name des Document Type.

### Authentifizierung (OIDC)
- `OIDC_ISSUER` – Issuer-URL (`https://…`).
- `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` – Client-Credentials.
- `OIDC_SCOPE` – Standard: `openid profile email`.
- `SESSION_SECRET` – Pflichtwert zum Signieren der Session-Cookies.

## Lokaler App-Start (ohne Docker)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # falls vorhanden, sonst Werte manuell setzen
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Die Oberfläche ist anschließend unter <http://localhost:8000> erreichbar.

## How it works
1. Beim Aufruf lädt die App die TSV aus Nextcloud oder vom lokalen Pfad und prüft das Spaltenformat.
2. Neue Einträge werden über das Formular erfasst; Verbrauch und Abrechnung entstehen aus der Differenz zum letzten Zählerstand und werden plausibilisiert.
3. Das aktualisierte TSV wird gespeichert, ein PDF erzeugt und auf Wunsch per E-Mail verschickt oder an Paperless übergeben.
4. Mit aktivierter OIDC-Konfiguration schützt ein Login-Screen die Oberfläche; ohne OIDC ist kein Login nötig.

## Lizenz
MIT-Lizenz (siehe `LICENSE`).
