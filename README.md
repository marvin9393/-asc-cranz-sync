# ASC Cranz Estebruegge – fußball.de Scraper

Synchronisiert Spielplan, Ergebnisse und Tabellen aller Herren- und Damen-Mannschaften
täglich von fußball.de per **GitHub Actions** in die MySQL/MariaDB-Datenbank auf dem Webserver.

## Funktionsweise

```
[GitHub Actions, tägl. 03:00 Uhr]
        ↓
[Playwright scrapt fußball.de]
        ↓
[SSH-Tunnel → Webserver DB (localhost:3306)]
        ↓
[MySQL: teams / matches / standings]
```

---

## 1. GitHub Repository anlegen

```bash
cd asc-scraper
git init
git add .
git commit -m "Initial commit"
# Repository auf GitHub anlegen, dann:
git remote add origin https://github.com/DEIN_USERNAME/asc-scraper.git
git push -u origin main
```

---

## 2. SSH-Schlüssel erstellen (einmalig)

```bash
# Neues Schlüsselpaar erstellen (KEIN Passwort setzen)
ssh-keygen -t ed25519 -C "github-actions-asc-scraper" -f ~/.ssh/asc_actions

# Public Key auf den Webserver kopieren
ssh-copy-id -i ~/.ssh/asc_actions.pub user@dein-webserver.de
```

---

## 3. GitHub Secrets setzen

Unter: **GitHub Repo → Settings → Secrets and variables → Actions → New repository secret**

| Secret | Wert |
|--------|------|
| `SSH_PRIVATE_KEY` | Inhalt von `~/.ssh/asc_actions` (privater Schlüssel) |
| `SSH_HOST` | `dein-webserver.de` |
| `SSH_PORT` | `22` (oder dein SSH-Port) |
| `SSH_USER` | dein SSH-Benutzername |
| `DB_NAME` | `asc_scraper` |
| `DB_USER` | dein Datenbankbenutzer |
| `DB_PASSWORD` | dein Datenbankpasswort |

---

## 4. Datenbank anlegen (MySQL auf dem Webserver)

```bash
ssh user@dein-webserver.de
mysql -u root -p
```
```sql
CREATE DATABASE asc_scraper CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON asc_scraper.* TO 'dein_user'@'localhost';
FLUSH PRIVILEGES;
```

Die Tabellen werden beim ersten Lauf automatisch erstellt.

---

## 5. Workflow manuell testen

GitHub Repo → **Actions** → **Tägliche Synchronisation** → **Run workflow**

---

## Datenbankstruktur

| Tabelle    | Inhalt                              |
|------------|-------------------------------------|
| `teams`    | Alle Mannschaften (Herren/Damen)    |
| `matches`  | Spielplan + Ergebnisse              |
| `standings`| Tabellen je Staffel und Saison      |

---

## Lokal debuggen

```bash
pip install -r requirements.txt
playwright install chromium

# SSH-Tunnel öffnen
ssh -N -L 13306:localhost:3306 user@dein-webserver.de &

# Scraper starten
DB_PORT=13306 DB_PASSWORD=... python scraper.py
```

Browser sichtbar machen (Debugging):
```python
# config.py
HEADLESS = False
```
