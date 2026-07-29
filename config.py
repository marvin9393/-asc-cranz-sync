"""
Konfiguration für den ASC Cranz Estebruegge Scraper
Werte werden aus Umgebungsvariablen gelesen (lokal: .env, CI: GitHub Secrets).
"""

import os

# ─── Datenbank ───────────────────────────────────────────────
DB_HOST     = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT     = int(os.environ.get("DB_PORT", "13306"))
DB_NAME     = os.environ.get("DB_NAME", "asc_scraper")
DB_USER     = os.environ.get("DB_USER", "dein_user")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "dein_passwort")

# ─── fußball.de ──────────────────────────────────────────────
VEREIN_ID   = "00ES8GN7SS00004PVV0AG08LVUPGND5I"
VEREIN_NAME = "asc-cranz-estebruegge-niedersachsen"
SAISON      = "2526"

VEREIN_URL  = (
    f"https://www.fussball.de/verein/{VEREIN_NAME}/-/"
    f"id/{VEREIN_ID}"
)

# ─── Scheduler ───────────────────────────────────────────────
SYNC_HOUR = 3      # täglich um 03:00 Uhr
SYNC_MINUTE = 0

# ─── Scraping ────────────────────────────────────────────────
HEADLESS = True             # False = Browser sichtbar (zum Debuggen)
REQUEST_DELAY_SEC = 2       # Pause zwischen Requests (Server schonen)
