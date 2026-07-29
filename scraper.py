"""
Scraper für fußball.de – ASC Cranz Estebruegge
Verwendet Playwright (Headless-Browser) da fußball.de JavaScript benötigt.
"""

import re
import time
import logging
from datetime import datetime
from typing import Optional

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

import config
from database import SessionLocal, Team, Match, Standing

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE_URL = "https://www.fussball.de"


# ─── Hilfsfunktionen ─────────────────────────────────────────────────────────

def _wait(page: Page, selector: str, timeout: int = 10_000):
    """Wartet auf ein Element, gibt None zurück wenn Timeout."""
    try:
        page.wait_for_selector(selector, timeout=timeout)
        return page.query_selector(selector)
    except PWTimeout:
        return None


def _parse_date(raw: str) -> Optional[datetime]:
    """Parst Datums-Strings von fußball.de (z.B. '26.07.26 15:00')."""
    raw = raw.strip()
    for fmt in ("%d.%m.%y %H:%M", "%d.%m.%Y %H:%M", "%d.%m.%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _upsert_team(db, fussball_id: str, name: str, gender: str) -> Team:
    """Legt ein Team an oder aktualisiert es."""
    team = db.query(Team).filter_by(fussball_id=fussball_id).first()
    if not team:
        team = Team(fussball_id=fussball_id, name=name, gender=gender)
        db.add(team)
        db.flush()
    else:
        team.name = name
        team.gender = gender
    return team


# ─── Schritt 1: Alle Mannschaften des Vereins laden ──────────────────────────

def scrape_teams(page: Page) -> list[dict]:
    """
    Ruft die Vereinsseite auf und sammelt alle Mannschafts-Links.
    Gibt Liste mit {fussball_id, name, gender, url} zurück.
    """
    log.info("Lade Vereinsseite: %s", config.VEREIN_URL)
    page.goto(config.VEREIN_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    # ── Cookie-Consent wegklicken (Usercentrics Shadow DOM) ─────────────
    try:
        # Usercentrics lädt asynchron – kurz warten
        page.wait_for_timeout(3000)

        # Über JavaScript direkt die Usercentrics API aufrufen
        page.evaluate("""
            () => {
                // Methode 1: Usercentrics API
                if (typeof UC_UI !== 'undefined') {
                    UC_UI.acceptAllConsents();
                    return;
                }
                // Methode 2: Button im Shadow DOM suchen
                const hosts = document.querySelectorAll('*');
                for (const host of hosts) {
                    if (host.shadowRoot) {
                        const btn = host.shadowRoot.querySelector(
                            'button[data-testid="uc-accept-all-button"], ' +
                            '.uc-btn-accept-all, ' +
                            'button:last-child'
                        );
                        if (btn) { btn.click(); return; }
                    }
                }
            }
        """)
        log.info("Cookie-Consent über JS gesetzt")
        page.wait_for_timeout(4000)
    except Exception as e:
        log.warning("Cookie-Consent konnte nicht gesetzt werden: %s", e)

    # ── Warten bis Mannschafts-Content geladen ist ───────────────────────
    try:
        page.wait_for_selector("a[href*='/mannschaft/']", timeout=15000)
    except PWTimeout:
        log.warning("Mannschafts-Links nicht gefunden – prüfe Debug-HTML")

    # Debug: Seite als HTML speichern damit wir Selektoren prüfen können
    html_snippet = page.content()
    with open("/tmp/fussball_debug.html", "w") as f:
        f.write(html_snippet)
    log.info("DEBUG: Seiteninhalt gespeichert (%d Zeichen)", len(html_snippet))

    links = page.query_selector_all("a[href*='/mannschaft/']")
    log.info("DEBUG: %d Mannschafts-Links gefunden", len(links))

    if not links:
        all_links = page.query_selector_all("a[href]")
        log.info("DEBUG: Insgesamt %d Links auf der Seite", len(all_links))
        for l in all_links[:30]:
            href = l.get_attribute("href") or ""
            if any(k in href for k in ["mannschaft", "verein", "team"]):
                log.info("  Link: %s | Text: %s", href, l.inner_text()[:50])

    teams = []

    seen = set()
    for link in links:
        href = link.get_attribute("href") or ""
        text = (link.inner_text() or "").strip()
        if not href or href in seen:
            continue
        seen.add(href)

        # Nur Herren / Damen / Frauen – keine Junioren, keine Jugend
        if not any(k in text.lower() for k in ["herren", "damen", "frauen"]):
            continue

        # Nur ASC Cranz Mannschaften (keine JSG / FSG / andere Vereine)
        if "asc-cranz" not in href.lower():
            continue

        # fußball.de: team-id/XXXX
        m = re.search(r"team-id/([A-Za-z0-9]+)", href)
        if not m:
            log.warning("  → Keine ID gefunden in: %s", href[:120])
            continue
        mid = m.group(1)

        gender = "Damen" if any(k in text.lower() for k in ["damen", "frauen"]) else "Herren"
        full_url = href if href.startswith("http") else BASE_URL + href

        teams.append({"fussball_id": mid, "name": text, "gender": gender, "url": full_url})
        log.info("  Mannschaft gefunden: %s (%s) [%s]", text, gender, mid)

        # Geschlecht anhand des Namens bestimmen
        gender = "Damen" if "damen" in text.lower() or "frauen" in text.lower() else "Herren"
        full_url = href if href.startswith("http") else BASE_URL + href

        teams.append({"fussball_id": mid, "name": text, "gender": gender, "url": full_url})
        log.info("  Mannschaft gefunden: %s (%s)", text, gender)

    log.info("Gesamt %d Mannschaften gefunden.", len(teams))
    return teams


# ─── Schritt 2: Spielplan & Ergebnisse einer Mannschaft ──────────────────────

def scrape_matches(page: Page, team: Team, team_url: str):
    """Scrapt Spielplan und Ergebnisse für eine Mannschaft."""
    log.info("Lade Spielplan für: %s", team.name)
    page.goto(team_url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(config.REQUEST_DELAY_SEC)

    db = SessionLocal()
    try:
        rows = page.query_selector_all("table.standard-liste tbody tr, .match-row")
        competition = _get_competition_name(page)
        match_day = 0

        for row in rows:
            cells = row.query_selector_all("td")
            if len(cells) < 4:
                continue

            texts = [c.inner_text().strip() for c in cells]
            date_str = texts[0] if texts else ""
            match_date = _parse_date(date_str)

            # Heimteam / Gastteam
            home_team, away_team = _extract_teams(row, texts)
            if not home_team:
                continue

            # Ergebnis
            home_goals, away_goals, status = _extract_result(row, texts)

            # Eindeutige Match-ID aus URL oder Fallback
            match_id = _extract_match_id(row) or f"{team.fussball_id}_{date_str}_{home_team}"
            match_day += 1

            existing = db.query(Match).filter_by(fussball_match_id=match_id).first()
            if existing:
                existing.home_goals = home_goals
                existing.away_goals = away_goals
                existing.status = status
                existing.match_date = match_date or existing.match_date
            else:
                db.add(Match(
                    fussball_match_id=match_id,
                    team_id=team.id,
                    competition=competition,
                    match_day=match_day,
                    match_date=match_date,
                    home_team=home_team,
                    away_team=away_team,
                    home_goals=home_goals,
                    away_goals=away_goals,
                    status=status,
                ))
        db.commit()
        log.info("  ✓ Spiele gespeichert für %s", team.name)
    except Exception as e:
        db.rollback()
        log.error("Fehler bei Spielplan %s: %s", team.name, e)
    finally:
        db.close()


def _get_competition_name(page: Page) -> str:
    el = page.query_selector(".competition-name, .headline-wrapper h2, h1")
    return el.inner_text().strip() if el else "Unbekannte Staffel"


def _extract_teams(row, texts: list[str]) -> tuple[str, str]:
    """Versucht Heim- und Gastteam aus einer Zeile zu lesen."""
    # fußball.de zeigt Teams oft in separaten Spalten oder mit 'vs'
    for i, t in enumerate(texts):
        if " - " in t:
            parts = t.split(" - ", 1)
            return parts[0].strip(), parts[1].strip()
        if " : " in t and i > 0:
            # Ergebnisspalte – Teams davor/danach
            home = texts[i - 1] if i > 0 else ""
            away = texts[i + 1] if i + 1 < len(texts) else ""
            return home, away
    # Fallback: zweite und letzte Spalte
    if len(texts) >= 3:
        return texts[1], texts[-1]
    return "", ""


def _extract_result(row, texts: list[str]) -> tuple[Optional[int], Optional[int], str]:
    """Liest Tore und Status aus einer Zeile."""
    for t in texts:
        m = re.match(r"^(\d+)\s*:\s*(\d+)$", t)
        if m:
            return int(m.group(1)), int(m.group(2)), "finished"
    return None, None, "scheduled"


def _extract_match_id(row) -> Optional[str]:
    """Versucht die Match-ID aus einem Link in der Zeile zu lesen."""
    link = row.query_selector("a[href*='spiel']")
    if link:
        href = link.get_attribute("href") or ""
        m = re.search(r"spielId/([A-Z0-9]+)", href)
        if m:
            return m.group(1)
    return None


# ─── Schritt 3: Tabelle einer Mannschaft ─────────────────────────────────────

def scrape_standing(page: Page, team: Team, team_url: str):
    """Scrapt die Tabelle für eine Mannschaft."""
    # Tabellen-Tab auf fußball.de
    tabelle_url = team_url.rstrip("/") + "#tab=tabelle"
    log.info("Lade Tabelle für: %s", team.name)
    page.goto(tabelle_url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(config.REQUEST_DELAY_SEC)

    # Tabellen-Tab klicken falls vorhanden
    tab = page.query_selector("a[href*='tabelle'], button:has-text('Tabelle')")
    if tab:
        tab.click()
        time.sleep(1)

    db = SessionLocal()
    try:
        rows = page.query_selector_all("table.standard-tabelle tbody tr, .table-row")
        competition = _get_competition_name(page)

        for row in rows:
            cells = row.query_selector_all("td")
            texts = [c.inner_text().strip() for c in cells]
            if len(texts) < 8:
                continue

            # Typisches Format: Rang | Team | Sp | S | U | N | Tore | Pkt
            try:
                rank         = int(re.sub(r"\D", "", texts[0]) or 0)
                team_name    = texts[1]
                played       = int(texts[2] or 0)
                wins         = int(texts[3] or 0)
                draws        = int(texts[4] or 0)
                losses       = int(texts[5] or 0)
                goals        = texts[6]  # z.B. "12:8"
                points       = int(texts[7] or 0)

                gf, ga = 0, 0
                gm = re.match(r"(\d+):(\d+)", goals)
                if gm:
                    gf, ga = int(gm.group(1)), int(gm.group(2))

                existing = db.query(Standing).filter_by(
                    team_id=team.id,
                    competition=competition,
                    season=config.SAISON,
                ).first()

                if existing:
                    existing.rank = rank; existing.team_name = team_name
                    existing.played = played; existing.wins = wins
                    existing.draws = draws; existing.losses = losses
                    existing.goals_for = gf; existing.goals_against = ga
                    existing.points = points
                else:
                    db.add(Standing(
                        team_id=team.id, competition=competition, season=config.SAISON,
                        rank=rank, team_name=team_name, played=played,
                        wins=wins, draws=draws, losses=losses,
                        goals_for=gf, goals_against=ga, points=points,
                    ))
            except (ValueError, IndexError):
                continue

        db.commit()
        log.info("  ✓ Tabelle gespeichert für %s", team.name)
    except Exception as e:
        db.rollback()
        log.error("Fehler bei Tabelle %s: %s", team.name, e)
    finally:
        db.close()


# ─── Hauptfunktion ───────────────────────────────────────────────────────────

def run_sync():
    """Führt die komplette Synchronisation durch."""
    log.info("=== Synchronisation gestartet ===")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.HEADLESS)
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (compatible; ASC-Scraper/1.0)"
        ))

        # 1. Alle Mannschaften ermitteln
        raw_teams = scrape_teams(page)

        db = SessionLocal()
        db_teams = []
        try:
            for t in raw_teams:
                team = _upsert_team(db, t["fussball_id"], t["name"], t["gender"])
                db_teams.append((team, t["url"]))
            db.commit()
        finally:
            db.close()

        # 2. Für jede Mannschaft: Spielplan + Tabelle
        for team, url in db_teams:
            try:
                scrape_matches(page, team, url)
                scrape_standing(page, team, url)
            except Exception as e:
                log.error("Fehler bei Mannschaft %s: %s", team.name, e)

        browser.close()

    log.info("=== Synchronisation abgeschlossen ===")


if __name__ == "__main__":
    run_sync()
