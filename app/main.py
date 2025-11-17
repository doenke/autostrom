from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os, csv, requests
import pandas as pd
from datetime import datetime, date
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
from datetime import datetime, date
import requests

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
    """Nimmt '0,32' oder 0.32 etc. und gibt '0.3200' zurück – oder '' bei Fehler."""
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
    Lädt die CSV von Nextcloud als raw bytes zurück.
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
    Schreibt raw bytes zurück in die Datei auf Nextcloud (PUT).
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
        raise RuntimeError("CSV leer (0 Bytes) – von Nextcloud/Datei kam kein Inhalt zurück.")

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

def pdf_payload(df: pd.DataFrame, new_record: dict):
    # last 24 rows from df + bold last line as in Node-RED
    tail = df.tail(24).to_dict(orient="records")
    rows = tail + [new_record]
    return rows

def render_pdf(output_path: str, rows, new_record):
    doc = SimpleDocTemplate(output_path, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)

    styles = getSampleStyleSheet()
    story = []

    # Header (right aligned)
    header_lines = [PDF_NAME, PDF_STREET, PDF_CITY]
    for i, text in enumerate(header_lines):
        if text:
            story.append(Paragraph(f"<para align='right'>{text}</para>", styles["Normal"]))
    story.append(Spacer(1, 0.3*cm))

    story.append(Paragraph(f"Autostromabrechnung {new_record['Datum']}", styles["Title"]))
    story.append(Spacer(1, 0.5*cm))

    # Table
    data = [["Datum", "Zählerstand", "Verbrauch", "Strompreis", "Abrechnung"]]
    for r in rows[:-1]:
        data.append([
            r["Datum"],
            f"{int(r['Zaehlerstand'])} kWh",
            f"{int(r['Verbrauch'])} kWh",
            f"{float(r['Strompreis']):.2f} €",
            f"{float(r['Abrechnung']):.2f} €"
        ])
    # bold last line
    r = rows[-1]
    data.append([
        Paragraph(f"<b>{r['Datum']}</b>", styles["Normal"]),
        Paragraph(f"<para align='right'><b>{int(r['Zaehlerstand'])} kWh</b></para>", styles["Normal"]),
        Paragraph(f"<para align='right'><b>{int(r['Verbrauch'])} kWh</b></para>", styles["Normal"]),
        Paragraph(f"<para align='right'><b>{float(r['Strompreis']):.2f} €</b></para>", styles["Normal"]),
        Paragraph(f"<para align='right'><b>{float(r['Abrechnung']):.2f} €</b></para>", styles["Normal"]),
    ])

    tbl = Table(data, hAlign="LEFT", colWidths=[3*cm, 3*cm, 3*cm, 3*cm, 3*cm])
    tbl.setStyle(TableStyle([
        ("GRID",(0,0),(-1,-1), 0.3, colors.grey),
        ("BACKGROUND",(0,0),(-1,0), colors.lightgrey),
        ("BOTTOMPADDING",(0,0),(-1,0),6),
        ("TOPPADDING",(0,0),(-1,0),6),
        ("ALIGN",(1,1),(-1,-1), "RIGHT"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 0.5*cm))

    # Summary sentence
    amt = float(new_record["Abrechnung"])
    story.append(Paragraph(f"Am {new_record['Datum']} stelle ich {amt:.2f} € für Autostrom in Rechnung.", styles["Normal"]))

    # Footer (page numbers) via onPage
    def on_page(canvas, doc):
        canvas.saveState()
        footer = f"Seite {doc.page}"
        canvas.setFont("Helvetica", 9)
        canvas.drawCentredString(A4[0]/2.0, 1.2*cm, footer)
        canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


def upload_paperless(new_record, pdf_path):
    """
    Upload to paperless-ngx via /api/documents/post_document/.
    Returns (ok: bool, message: str)
    """
    if not PAPERLESS_URL or not PAPERLESS_TOKEN:
        return False, "Paperless nicht konfiguriert (PAPERLESS_URL/PAPERLESS_TOKEN fehlen)."

    endpoint = f"{PAPERLESS_URL}/api/documents/post_document/"
    headers = {"Authorization": f"Token {PAPERLESS_TOKEN}"}

    files = {
        "document": (f"Autostrom {new_record['Datum']}.pdf", open(pdf_path, "rb"), "application/pdf")
    }
    data = {
        "title": f"Autostrom {new_record['Datum']}",
        "created": datetime.strptime(new_record["Datum"], "%d.%m.%Y").date().isoformat()
    }
    if PAPERLESS_TAGS:
        data["tags"] = PAPERLESS_TAGS
    if PAPERLESS_CORRESPONDENT:
        data["correspondent"] = PAPERLESS_CORRESPONDENT
    if PAPERLESS_DOCUMENT_TYPE:
        data["document_type"] = PAPERLESS_DOCUMENT_TYPE

    # Optional: falls dein Paperless ein selbst-signiertes Zertifikat nutzt, setze verify=False.
    # Achtung: unsicher — nur zum Testen. In Produktion sollte verify=True bleiben.
    verify_tls = True

    try:
        r = requests.post(endpoint, headers=headers, files=files, data=data, timeout=30, verify=verify_tls)
    except requests.exceptions.SSLError as e:
        return False, f"SSL-Fehler: {e}. Wenn Paperless ein selbst-signiertes Zertifikat nutzt, setze verify=False in der Funktion (nur zum Test)."
    except Exception as e:
        return False, f"Netzwerk-/Request-Fehler beim Upload: {e}"

    # debug: status + body (kurz)
    status = r.status_code
    body = r.text[:1000]  # begrenzen

    if 200 <= status < 300:
        return True, f"Hochgeladen (Status {status})."
    else:
        # Versuche, nützliche Fehlermeldung aus JSON zu ziehen
        msg = f"Fehler: HTTP {status}"
        try:
            j = r.json()
            # stringify relevant fields if present
            msg += " - " + (j.get("detail") or j.get("error") or str(j))
        except Exception:
            msg += " - " + (body or "keine Antwort")
        # Log in server logs
        try:
            print("[Paperless] POST", endpoint, "Status", status, "Body:", body)
        except Exception:
            pass
        return False, msg


def send_email(new_record, pdf_path):
    if not SMTP_HOST or not MAIL_TO:
        return False, "SMTP unkonfiguriert oder Empfänger fehlt."

    recipients = [addr.strip() for addr in MAIL_TO.split(",") if addr.strip()]
    subject = f"Autostrom Abrechnung {new_record['Datum']}"
    text = f"Es wurde ein neuer Autostrom Zählerstand erfasst. {new_record['Abrechnung']:.2f} € für {new_record['Verbrauch']} kWh Autostrom werden in Rechnung gestellt."

    msg = MIMEMultipart()
    msg["From"] = MAIL_FROM
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(text, "plain", "utf-8"))

    with open(pdf_path, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="Autostrom {new_record["Datum"]}.pdf"')
    msg.attach(part)

    if SMTP_SSL:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)

    return True, "OK"

from datetime import date  # ist schon drin

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
        }
    )


@app.post("/submit", response_class=HTMLResponse)
def submit(
    request: Request,
    ablesedatum: str = Form(...),
    zaehlerstand: float = Form(...),
    strompreis_eur: float = Form(...),
    send_mail: str | None = Form(None, alias="send_mail"),
    do_upload_paperless: str | None = Form(None, alias="upload_paperless"),
):
    # Append new record (and compute Verbrauch/Abrechnung)
    new_rec = append_row(ablesedatum, zaehlerstand, strompreis_eur)

    # Read again for PDF table
    df = load_df()
    rows = pdf_payload(df, new_rec)

    # Make PDF
    os.makedirs("/app/data/invoices", exist_ok=True)
    d = datetime.strptime(new_rec["Datum"], "%d.%m.%Y")
    pdf_path = f"/app/data/invoices/Autostrom-{d.strftime('%Y-%m-%d')}.pdf"
    render_pdf(pdf_path, rows, new_rec)

    # Mail: nur wenn checkbox gesetzt (HTML sendet "on" wenn angehakt)
    mail_ok, mail_msg = (False, "Übersprungen")
    if send_mail is not None and send_mail.lower() == "on":
        try:
            mail_ok, mail_msg = send_email(new_rec, pdf_path)
        except Exception as e:
            mail_ok, mail_msg = False, str(e)

    # Paperless: nur wenn checkbox gesetzt
    paper_ok, paper_msg = (False, "Übersprungen")
    if do_upload_paperless is not None and do_upload_paperless.lower() == "on":
        try:
            paper_ok, paper_msg = upload_paperless(new_rec, pdf_path)
        except Exception as e:
            paper_ok, paper_msg = False, str(e)

    return templates.TemplateResponse("summary.html", {
        "request": request,
        "record": new_rec,
        "pdf_path": f"/invoice/{d.strftime('%Y-%m-%d')}",
        "mail_ok": mail_ok,
        "mail_msg": mail_msg,
        "paper_ok": paper_ok,
        "paper_msg": paper_msg
    })



@app.get("/invoice/{datestr}", response_class=FileResponse)
def get_invoice(datestr: str):
    path = f"/app/data/invoices/Autostrom-{datestr}.pdf"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="PDF nicht gefunden")
    return FileResponse(path, media_type="application/pdf", filename=os.path.basename(path))


@app.post("/delete-last")
async def delete_last_entry(request: Request):
    """
    Löscht die letzte Zeile aus der CSV – lokal oder in Nextcloud.
    """

    use_nextcloud = nc_enabled()

    local_path = os.getenv("LOCAL_TSV", "/app/data/Autostrom.csv")

    try:
        # --- CSV Laden ---
        if use_nextcloud:
            content = nc_download_file()
            lines = content.decode("utf-8").splitlines()
        else:
            with open(local_path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()

        if len(lines) <= 1:
            return RedirectResponse(
                "/?error=CSV hat keine weitere Zeile zum Löschen",
                status_code=303
            )

        # --- letzte Zeile entfernen ---
        lines = lines[:-1]

        new_content = "\n".join(lines)

        # --- CSV zurückschreiben ---
        if use_nextcloud:
            nc_upload_file(new_content.encode("utf-8"))
        else:
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(new_content)

        return RedirectResponse(
            "/?success=Letzte Zeile erfolgreich gelöscht",
            status_code=303
        )

    except Exception as e:
        return RedirectResponse(
            f"/?error=Fehler beim Löschen: {e}",
            status_code=303
        )
