from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os, csv, requests
import pandas as pd
from datetime import datetime, date, timedelta
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import cm
import smtplib, ssl
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders

REQUEST_TIMEOUT = 15  # Sekunden

NC_BASE_URL = os.getenv("NC_BASE_URL", "")
NC_USERNAME = os.getenv("NC_USERNAME", "")
NC_PASSWORD = os.getenv("NC_PASSWORD", "")
NC_FILEPATH = os.getenv("NC_FILEPATH", "Zaehlerstaende/Autostrom.csv")
LOCAL_TSV = os.getenv("LOCAL_TSV", "/app/data/Autostrom.csv")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_SSL = os.getenv("SMTP_SSL", "false").lower() == "true"
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER)
MAIL_TO = os.getenv("MAIL_TO", "")  # comma-separated

PDF_NAME = os.getenv("PDF_NAME", "")
PDF_STREET = os.getenv("PDF_STREET", "")
PDF_CITY = os.getenv("PDF_CITY", "")

PAPERLESS_URL = os.getenv("PAPERLESS_URL", "").rstrip("/")
PAPERLESS_TOKEN = os.getenv("PAPERLESS_TOKEN", "")
PAPERLESS_TAGS = os.getenv("PAPERLESS_TAGS", "")
PAPERLESS_CORRESPONDENT = os.getenv("PAPERLESS_CORRESPONDENT", "")
PAPERLESS_DOCUMENT_TYPE = os.getenv("PAPERLESS_DOCUMENT_TYPE", "")

app = FastAPI(title="EV Invoice App")
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

def parse_price_to_str(value) -> str:
    """Nimmt '0,32' oder 0.32 etc. und gibt '0.3200' zurück 	6 oder '' bei Fehler."""
    try:
        if isinstance(value, str):
            value = value.replace(",", ".")
        return f"{float(value):.4f}"
    except Exception:
        return ""

def nc_enabled():
    return bool(NC_BASE_URL and NC_USERNAME and NC_PASSWORD)

def nc_url():
    base = NC_BASE_URL.rstrip("/")
    path = NC_FILEPATH.lstrip("/")
    return f"{base}/{path}"

def read_tsv_text():
    if nc_enabled():
        r = requests.get(nc_url(), auth=(NC_USERNAME, NC_PASSWORD), timeout=REQUEST_TIMEOUT)
        if r.status_code == 404:
            header = "Datum\tZaehlerstand\tStrompreis\tVerbrauch\tAbrechnung\n"
            requests.put(nc_url(), data=header.encode("utf-8"),
                         auth=(NC_USERNAME, NC_PASSWORD), timeout=REQUEST_TIMEOUT)
            return header
        r.raise_for_status()
        return r.text
    else:
        os.makedirs(os.path.dirname(LOCAL_TSV), exist_ok=True)
        if not os.path.exists(LOCAL_TSV):
            with open(LOCAL_TSV, "w", encoding="utf-8") as f:
                f.write("Datum\tZaehlerstand\tStrompreis\tVerbrauch\tAbrechnung\n")
        with open(LOCAL_TSV, "r", encoding="utf-8") as f:
            return f.read()

def write_tsv_text(text: str):
    if nc_enabled():
        r = requests.put(nc_url(), data=text.encode("utf-8"),
                         auth=(NC_USERNAME, NC_PASSWORD), timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
    else:
        with open(LOCAL_TSV, "w", encoding="utf-8") as f:
            f.write(text)

def nc_download_file() -> bytes:
    """
    Ldt die CSV von Nextcloud als raw bytes zurck.
    Wir verwenden dieselben Timeout/Auth-Settings wie read_tsv_text().
    """
    if not nc_enabled():
        raise RuntimeError("Nextcloud nicht konfiguriert.")
    r = requests.get(nc_url(), auth=(NC_USERNAME, NC_PASSWORD), timeout=REQUEST_TIMEOUT)
    # Wenn 404, behandeln wir wie read_tsv_text (Header anlegen)
    if r.status_code == 404:
        header = "Datum\tZaehlerstand\tStrompreis\tVerbrauch\tAbrechnung\n"
        requests.put(nc_url(), data=header.encode("utf-8"),
                     auth=(NC_USERNAME, NC_PASSWORD), timeout=REQUEST_TIMEOUT)
        return header.encode("utf-8")
    r.raise_for_status()
    return r.content

def nc_upload_file(content_bytes: bytes):
    """
    Schreibt raw bytes zurck in die Datei auf Nextcloud (PUT).
    Wir rufen raise_for_status() um Fehler nach oben zu reichen.
    """
    if not nc_enabled():
        raise RuntimeError("Nextcloud nicht konfiguriert.")
    r = requests.put(nc_url(), data=content_bytes, auth=(NC_USERNAME, NC_PASSWORD), timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return True

def load_df():
    txt = read_tsv_text()
    if not txt or not txt.strip():
        raise RuntimeError("CSV leer (0 Bytes) 	6 von Nextcloud/Datei kam kein Inhalt zurck.")

    from io import StringIO
    try:
        df = pd.read_csv(StringIO(txt), sep="\t")
    except Exception as e:
        preview = txt[:200].replace("\n", "\\n")
        raise RuntimeError(f"CSV konnte nicht geparst werden: {e}. Vorschau: '{preview}'")

    required = ["Datum", "Zaehlerstand", "Strompreis", "Verbrauch", "Abrechnung"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"CSV-Spalten fehlen: {missing}. Gefunden: {list(df.columns)}")

    return df


def append_row(form_date_iso: str, zaehlerstand: float, strompreis: float):
    # Build fields in the same format as Node-RED
    df = load_df()
    last_zaehler = int(df.iloc[-1]["Zaehlerstand"]) if not df.empty else 0
    verbrauch = max(0, round(zaehlerstand - last_zaehler))
    abrechnung = round(verbrauch * strompreis, 2)

    # Convert date to DD.MM.YYYY
    d = datetime.fromisoformat(form_date_iso).date()
    datum_str = d.strftime("%d.%m.%Y")

    # Append line
    new_line = f"{datum_str}\t{int(zaehlerstand)}\t{strompreis:.6f}\t{verbrauch}\t{abrechnung:.6f}"
    old = read_tsv_text().rstrip("\n")
    new_text = old + "\n" + new_line + "\n"
    write_tsv_text(new_text)

    return {
        "Datum": datum_str,
        "Zaehlerstand": int(zaehlerstand),
        "Strompreis": strompreis,
        "Verbrauch": verbrauch,
        "Abrechnung": abrechnung
    }

# ... (Der restliche Code bleibt unverändert) ...

from datetime import timedelta  # Import für Datumsdifferenz

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    error_msg = None
    info_msg = None
    last = None
    rows = []
    last_price = ""

    # availability flags
    mail_available = bool(SMTP_HOST and MAIL_TO)
    paperless_available = bool(PAPERLESS_URL and PAPERLESS_TOKEN)

    # default checked if configured
    default_mail_checked = mail_available
    default_paperless_checked = paperless_available

    # Nextcloud check (unchanged behavior)
    if not nc_enabled():
        error_msg = (
            "Nextcloud ist nicht konfiguriert. "
            "Bitte setze die Umgebungsvariablen <code>NC_BASE_URL</code>, "
            "<code>NC_USERNAME</code> und <code>NC_PASSWORD</code>."
        )
        return templates.TemplateResponse(
            "form.html",
            {
                "request": request,
                "last": None,
                "rows": [],
                "last_price": "",
                "today_iso": date.today().isoformat(),
                "error_msg": error_msg,
                "info_msg": None,
                "mail_available": mail_available,
                "paperless_available": paperless_available,
                "default_mail_checked": default_mail_checked,
                "default_paperless_checked": default_paperless_checked,
                "show_delete_button": False  # Delete button bei nicht konfigurierte Nextcloud aus
            },
        )

    try:
        df = load_df()  # CSV wird beim Seitenaufruf geladen
        if df.empty or len(df) == 0:
            info_msg = ("CSV geladen, aber keine Datenzeilen gefunden. "
                        "Bitte erste Ablesung erfassen oder Datei prüfen.")
        else:
            last = df.iloc[-1].to_dict()
            rows = df.tail(24).to_dict(orient="records")
            last_price = parse_price_to_str(last.get("Strompreis", ""))
    except Exception as e:
        error_msg = str(e)

    show_delete_button = False
    if last and "Datum" in last:
        last_date = datetime.strptime(last["Datum"], "%d.%m.%Y").date()
        days_diff = (date.today() - last_date).days
        show_delete_button = days_diff <= 10

    today_iso = date.today().isoformat()

    return templates.TemplateResponse(
        "form.html",
        {
            "request": request,
            "last": last,
            "rows": rows,
            "last_price": last_price,
            "today_iso": today_iso,
            "error_msg": error_msg,
            "info_msg": info_msg,
            "mail_available": mail_available,
            "paperless_available": paperless_available,
            "default_mail_checked": default_mail_checked,
            "default_paperless_checked": default_paperless_checked,
            "show_delete_button": show_delete_button
        }
    )

# Der übrige Code bleibt unverändert...

@app.post("/delete-last")
async def delete_last_entry(request: Request):
    # (unverändert)
    ...
