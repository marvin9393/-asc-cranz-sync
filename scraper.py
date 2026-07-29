"""
Scraper für fußball.de – ASC Cranz Estebruegge
Scrapt Daten und generiert eine SQL-Datei zum Import auf dem Server.
"""

import re
import time
import logging
from datetime import datetime
from typing import Optional

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE_URL  = "https://www.fussball.de"
SQL_FILE  = "/tmp/asc_data.sql"


# ─── SQL-Datei Hilfsfunktionen ────────────────────────────────────────────────

def _esc(val) -> str:
    """Escaped einen String für MySQL."""
    if val is None:
        return "NULL"
    return "'" + str(val).replace("'", "''").replace("\\", "\\\\") + "'"


def _write_header(f):
    f.write("-- ASC Cranz Sync – generiert am {}\n".format(datetime.now()))
    f.write("SET NAMES utf8mb4;\n\n")
    f.write("""CREATE TABLE IF NOT EXISTS teams (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  fussball_id  VARCHAR(64) UNIQUE NOT NULL,
  name         VARCHAR(128),
  gender       VARCHAR(10),
  updated_at   DATETIME DEFAULT NOW() ON UPDATE NOW()
) CHARACTER SET utf8mb4;\n\n""")
    f.write("""CREATE TABLE IF NOT EXISTS matches (
  id                INT AUTO_INCREMENT PRIMARY KEY,
  fussball_match_id VARCHAR(64) UNIQUE NOT NULL,
  team_fussball_id  VARCHAR(64),
  competition       VARCHAR(128),
  match_day         SMALLINT,
  match_date        DATETIME,
  home_team         VARCHAR(128),
  away_team         VARCHAR(128),
  home_goals        SMALLINT,
  away_goals        SMALLINT,
  status            VARCHAR(20) DEFAULT 'scheduled',
  updated_at        DATETIME DEFAULT NOW() ON UPDATE NOW()
) CHARACTER SET utf8mb4;\n\n""")
    f.write("""CREATE TABLE IF NOT EXISTS standings (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  team_fussball_id VARCHAR(64),
  competition      VARCHAR(128),
  season           VARCHAR(10),
  rank             SMALLINT,
  team_name        VARCHAR(128),
  played           SMALLINT DEFAULT 0,
  wins             SMALLINT DEFAULT 0,
  draws            SMALLINT DEFAULT 0,
  losses           SMALLINT DEFAULT 0,
  goals_for        SMALLINT DEFAULT 0,
  goals_against    SMALLINT DEFAULT 0,
  points           SMALLINT DEFAULT 0,
  updated_at       DATETIME DEFAULT NOW() ON UPDATE NOW(),
  UNIQUE KEY uq_standing (team_fussball_id, competition, season)
) CHARACTER SET utf8mb4;\n\n""")


def _upsert_team(f, fussball_id: str, name: str, gender: str):
    f.write(
        "INSERT INTO teams (fussball_id, name, gender) VALUES "
        f"({_esc(fussball_id)}, {_esc(name)}, {_esc(gender)}) "
        "ON DUPLICATE KEY UPDATE name=VALUES(name), gender=VALUES(gender);\n"
    )


def _upsert_match(f, match_id: str, team_id: str, competition: str,
                  match_day: int, match_date, home: str, away: str,
                  hg, ag, status: str):
    md = f"'{match_date.strftime('%Y-%m-%d %H:%M:%S')}'" if match_date else "NULL"
    f.write(
        "INSERT INTO matches (fussball_match_id, team_fussball_id, competition, "
        "match_day, match_date, home_team, away_team, home_goals, away_goals, status) VALUES "
        f"({_esc(match_id)}, {_esc(team_id)}, {_esc(competition)}, "
        f"{match_day or 0}, {md}, {_esc(home)}, {_esc(away)}, "
        f"{'NULL' if hg is None else hg}, {'NULL' if ag is None else ag}, {_esc(status)}) "
        "ON DUPLICATE KEY UPDATE home_goals=VALUES(home_goals), "
        "away_goals=VALUES(away_goals), status=VALUES(status), "
        "match_date=COALESCE(VALUES(match_date), match_date);\n"
    )


def _upsert_standing(f, team_id: str, competition: str, season: str,
                     rank: int, team_name: str, played: int, wins: int,
                     draws: int, losses: int, gf: int, ga: int, points: int):
    f.write(
        "INSERT INTO standings (team_fussball_id, competition, season, rank, team_name, "
        "played, wins, draws, losses, goals_for, goals_against, points) VALUES "
        f"({_esc(team_id)}, {_esc(competition)}, {_esc(season)}, {rank}, {_esc(team_name)}, "
        f"{played}, {wins}, {draws}, {losses}, {gf}, {ga}, {points}) "
        "ON DUPLICATE KEY UPDATE rank=VALUES(rank), played=VALUES(played), "
        "wins=VALUES(wins), draws=VALUES(draws), losses=VALUES(losses), "
        "goals_for=VALUES(goals_for), goals_against=VALUES(goals_against), "
        "points=VALUES(points);\n"
    )


# ─── Cookie-Consent ───────────────────────────────────────────────────────────

def _accept_cookies(page: Page):
    try:
        page.wait_for_timeout(3000)
        page.evaluate("""() => {
            if (typeof UC_UI !== 'undefined') { UC_UI.acceptAllConsents(); return; }
            document.querySelectorAll('*').forEach(h => {
                if (h.shadowRoot) {
                    const btn = h.shadowRoot.querySelector(
                        'button[data-testid="uc-accept-all-button"], .uc-btn-accept-all');
                    if (btn) btn.click();
                }
            });
        }""")
        page.wait_for_timeout(4000)
        log.info("Cookie-Consent gesetzt")
    except Exception as e:
        log.warning("Cookie-Consent Fehler: %s", e)


# ─── Teams laden ─────────────────────────────────────────────────────────────

def scrape_teams(page: Page) -> list[dict]:
    log.info("Lade Vereinsseite: %s", config.VEREIN_URL)
    page.goto(config.VEREIN_URL, wait_until="domcontentloaded", timeout=60000)
    _accept_cookies(page)

    try:
        page.wait_for_selector("a[href*='/mannschaft/']", timeout=15000)
    except PWTimeout:
        log.warning("Mannschafts-Links Timeout")

    links = page.query_selector_all("a[href*='/mannschaft/']")
    log.info("%d Mannschafts-Links gefunden", len(links))

    teams, seen = [], set()
    for link in links:
        href = link.get_attribute("href") or ""
        text = (link.inner_text() or "").strip()
        if not href or href in seen:
            continue
        seen.add(href)

        if not any(k in text.lower() for k in ["herren", "damen", "frauen"]):
            continue
        if "asc-cranz" not in href.lower():
            continue

        m = re.search(r"team-id/([A-Za-z0-9]+)", href)
        if not m:
            continue
        mid = m.group(1)

        gender   = "Damen" if any(k in text.lower() for k in ["damen", "frauen"]) else "Herren"
        full_url = href if href.startswith("http") else BASE_URL + href
        teams.append({"fussball_id": mid, "name": text, "gender": gender, "url": full_url})
        log.info("  ✓ %s (%s)", text, gender)

    log.info("Gesamt %d Mannschaften", len(teams))
    return teams


# ─── Spielplan scrapen ───────────────────────────────────────────────────────

def _parse_date(raw: str) -> Optional[datetime]:
    for fmt in ("%d.%m.%y %H:%M", "%d.%m.%Y %H:%M", "%d.%m.%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            pass
    return None


def scrape_matches(f, page: Page, team: dict):
    """
    Spielplan via AJAX-Endpunkt: /ajax.team.matchplan/-/team-id/{id}
    HTML-Struktur:
      tr.row-competition  → Datum + Wettbewerbsname
      tr (Spielzeile)     → td.column-club .club-name (Heim/Gast)
    """
    log.info("Lade Spielplan: %s", team["name"])
    url = f"{BASE_URL}/ajax.team.matchplan/-/team-id/{team['fussball_id']}"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    _accept_cookies(page)

    try:
        page.wait_for_selector("table.table-striped tbody tr", timeout=15000)
    except PWTimeout:
        log.warning("  Spielplan-Tabelle nicht gefunden – speichere Debug-HTML")
        with open(f"/tmp/fussball_debug_{team['fussball_id']}.html", "w", encoding="utf-8") as dbg:
            dbg.write(page.content())
        return

    # Alle Spiele laden ("Mehr laden"-Pagination)
    while True:
        btn = page.query_selector("a.button-load-more, .load-more-button, a[data-ajax*='offset']")
        if not btn:
            break
        btn.click()
        page.wait_for_timeout(2000)

    current_date: Optional[datetime] = None
    current_competition = team["name"]
    match_count = 0

    for row in page.query_selector_all("table.table-striped tbody tr"):
        cls = row.get_attribute("class") or ""

        if "row-headline" in cls:
            continue

        # Wettbewerbs-Zeile: Datum + Staffelname
        if "row-competition" in cls:
            date_el = row.query_selector(".column-date")
            if date_el:
                raw = re.sub(r"[A-Za-zÄÖÜäöüß,\.]+\s*", "", date_el.inner_text()).strip()
                raw = re.sub(r"\s*\|\s*", " ", raw).strip()
                current_date = _parse_date(raw)
            comp_el = row.query_selector(".column-team a")
            if comp_el:
                current_competition = comp_el.inner_text().strip()
            continue

        # Spielzeile: Heim / Gast aus separaten club-Spalten
        home_el = row.query_selector("td.column-club:not(.no-border) .club-name")
        away_el = row.query_selector("td.column-club.no-border .club-name")
        if not home_el or not away_el:
            continue

        home = home_el.inner_text().strip()
        away = away_el.inner_text().strip()
        if not home or not away:
            continue

        score_link = row.query_selector(".column-score a[href*='/spiel/']")
        href = score_link.get_attribute("href") if score_link else ""
        mid_m = re.search(r"/spiel/([A-Za-z0-9]+)$", href or "")
        match_id = mid_m.group(1) if mid_m else f"{team['fussball_id']}_{match_count}_{home[:10]}"

        _upsert_match(f, match_id, team["fussball_id"], current_competition,
                      match_count + 1, current_date, home, away, None, None, "scheduled")
        match_count += 1

    log.info("  ✓ %d Spiele im Spielplan gespeichert", match_count)


# ─── Tabelle scrapen ─────────────────────────────────────────────────────────

def scrape_standing(f, page: Page, team: dict):
    log.info("Lade Tabelle: %s", team["name"])
    url = team["url"].rstrip("/") + "#tab=tabelle"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    _accept_cookies(page)
    page.wait_for_timeout(3000)

    tab = page.query_selector("a[href*='tabelle'], button:has-text('Tabelle')")
    if tab:
        tab.click()
        page.wait_for_timeout(2000)

    competition = team["name"]
    el = page.query_selector(".competition-name, h1")
    if el:
        competition = el.inner_text().strip()

    rows = page.query_selector_all("table.standard-tabelle tbody tr, .table-row, tr")
    for row in rows:
        cells = row.query_selector_all("td")
        texts = [c.inner_text().strip() for c in cells]
        if len(texts) < 8:
            continue
        try:
            rank      = int(re.sub(r"\D", "", texts[0]) or 0)
            team_name = texts[1]
            played    = int(texts[2] or 0)
            wins      = int(texts[3] or 0)
            draws     = int(texts[4] or 0)
            losses    = int(texts[5] or 0)
            gm        = re.match(r"(\d+):(\d+)", texts[6])
            gf, ga    = (int(gm.group(1)), int(gm.group(2))) if gm else (0, 0)
            points    = int(texts[7] or 0)
            _upsert_standing(f, team["fussball_id"], competition, config.SAISON,
                             rank, team_name, played, wins, draws, losses, gf, ga, points)
        except (ValueError, IndexError):
            continue

    log.info("  ✓ Tabelle gespeichert")


# ─── Hauptfunktion ────────────────────────────────────────────────────────────

def run_sync():
    log.info("=== Synchronisation gestartet ===")

    with open(SQL_FILE, "w", encoding="utf-8") as f:
        _write_header(f)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=config.HEADLESS)
            page    = browser.new_page()

            teams = scrape_teams(page)
            for team in teams:
                _upsert_team(f, team["fussball_id"], team["name"], team["gender"])

            for team in teams:
                try:
                    scrape_matches(f, page, team)
                    scrape_standing(f, page, team)
                except Exception as e:
                    log.error("Fehler bei %s: %s", team["name"], e)

            browser.close()

    log.info("SQL-Datei generiert: %s", SQL_FILE)
    log.info("=== Synchronisation abgeschlossen ===")


if __name__ == "__main__":
    run_sync()
