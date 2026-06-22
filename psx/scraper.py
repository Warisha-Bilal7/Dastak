"""
psx/scraper.py  —  Dastak PSX data layer
-----------------------------------------
Endpoints used:
  dps.psx.com.pk/market-watch              → live quotes (server-rendered HTML table)
  dps.psx.com.pk/timeseries/eod/{SYMBOL}   → EOD history JSON
  dps.psx.com.pk/timeseries/int/{SYMBOL}   → intraday JSON
  dps.psx.com.pk/circuit-breakers          → halted stocks
  dps.psx.com.pk/payouts?symbol=X          → dividends / bonus
  psxterminal.com/api/announcements        → company announcements (server-side)

PSX EOD JSON format: {"status":1, "data": [[timestamp_s, close, volume, open], ...]}
  - 4 fields per row: [timestamp_seconds, close, volume, open]
  - NO separate high/low in this endpoint (intraday has full OHLCV)
"""

import sys
# Force stdout to use UTF-8 on Windows to prevent UnicodeEncodeError
if sys.stdout and sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

import httpx
import sqlite3
import json
import time
import os
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table

console = Console()

BASE_URL     = "https://dps.psx.com.pk"
TERMINAL_URL = "https://psxterminal.com"
DB_PATH      = Path(__file__).parent.parent / "data" / "dastak.db"
DELAY_MS     = int(os.environ.get("PSX_SCRAPE_DELAY_MS", "1200"))

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer":         "https://dps.psx.com.pk/",
    "Accept":          "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


# ── helpers ───────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _sleep():
    time.sleep(DELAY_MS / 1000)

def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS price_history (
            symbol TEXT, date TEXT, open REAL, high REAL,
            low REAL, close REAL, volume INTEGER,
            PRIMARY KEY (symbol, date)
        );
        CREATE TABLE IF NOT EXISTS live_quotes (
            symbol TEXT PRIMARY KEY, sector TEXT,
            ldcp REAL, open REAL, high REAL, low REAL,
            current REAL, change REAL, change_pct TEXT,
            volume INTEGER, fetched_at TEXT
        );
        CREATE TABLE IF NOT EXISTS announcements (
            id TEXT PRIMARY KEY, symbol TEXT, company TEXT,
            title TEXT, category TEXT, published TEXT,
            url TEXT, fetched_at TEXT
        );
        CREATE TABLE IF NOT EXISTS payouts (
            symbol TEXT, company TEXT, payout_type TEXT,
            amount TEXT, book_closure TEXT, announced TEXT,
            PRIMARY KEY (symbol, announced, payout_type)
        );
        CREATE TABLE IF NOT EXISTS circuit_breakers (
            symbol TEXT PRIMARY KEY, company TEXT,
            reason TEXT, since TEXT, updated_at TEXT
        );
    """)
    conn.commit()
    return conn

def _fetch(url: str, params: dict = None, as_json: bool = False):
    for attempt in range(3):
        try:
            with httpx.Client(headers=HEADERS, timeout=25, follow_redirects=True) as c:
                r = c.get(url, params=params)
                r.raise_for_status()
                _sleep()
                return r.json() if as_json else r.text
        except Exception as e:
            wait = 2 ** attempt
            console.print(f"[yellow]⚠ Attempt {attempt+1} failed: {e}. Retry in {wait}s…[/yellow]")
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {url} after 3 attempts.")


# ── EOD price history ─────────────────────────────────────────────────────────
def fetch_price_history(symbol: str, start: str = None, end: str = None, use_cache: bool = True) -> list[dict]:
    """
    EOD OHLCV for a symbol.
    PSX format: {"status":1, "data": [[timestamp_s, close, volume, open], ...]}
    Fields: [0]=timestamp(s), [1]=close, [2]=volume, [3]=open  — no separate high/low
    """
    symbol = symbol.upper()
    conn   = _get_db()

    if use_cache:
        q, args = "SELECT * FROM price_history WHERE symbol=?", [symbol]
        if start: q += " AND date>=?"; args.append(start)
        if end:   q += " AND date<=?"; args.append(end)
        rows = conn.execute(q + " ORDER BY date", args).fetchall()
        if rows:
            console.print(f"[green]✓ {len(rows)} cached price rows for {symbol}[/green]")
            return [dict(r) for r in rows]

    console.print(f"[cyan]Fetching EOD history for {symbol}…[/cyan]")
    data = _fetch(f"{BASE_URL}/timeseries/eod/{symbol}", as_json=True)

    # Unwrap envelope: {"status":1, "message":"", "data":[...]}
    rows_raw = data if isinstance(data, list) else data.get("data", data)

    records = []
    for row in (rows_raw or []):
        try:
            # PSX sends [timestamp_seconds, close, volume, open]
            ts    = int(row[0])
            close = float(row[1]) if row[1] is not None else None
            vol   = int(row[2])   if row[2] is not None else None
            open_ = float(row[3]) if len(row) > 3 and row[3] is not None else None

            d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            records.append({
                "symbol": symbol, "date": d,
                "open":   open_,  "high": None,
                "low":    None,   "close": close,
                "volume": vol,
            })
        except (IndexError, TypeError, ValueError):
            continue

    # Sort chronologically (oldest to newest) to ensure consistency with cached data
    records.sort(key=lambda x: x["date"])

    if start: records = [r for r in records if r["date"] >= start]
    if end:   records = [r for r in records if r["date"] <= end]

    if records:
        conn.executemany("""
            INSERT OR REPLACE INTO price_history
            (symbol,date,open,high,low,close,volume)
            VALUES (:symbol,:date,:open,:high,:low,:close,:volume)
        """, records)
        conn.commit()
        console.print(f"[green]✓ Saved {len(records)} EOD rows for {symbol}[/green]")
    else:
        console.print(f"[red]⚠ No rows parsed. Raw sample: {str(data)[:200]}[/red]")

    return records


# ── Live market watch ─────────────────────────────────────────────────────────
def fetch_market_watch(cache_minutes: int = 15) -> list[dict]:
    """
    Live quotes for ALL ~478 PSX symbols from /market-watch HTML table.
    Symbol links like <a href="/company/ENGRO">ENGRO</a> — we extract text from the <a> tag.
    """
    conn = _get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=cache_minutes)).isoformat()
    if conn.execute("SELECT 1 FROM live_quotes WHERE fetched_at>? LIMIT 1", (cutoff,)).fetchone():
        rows = conn.execute("SELECT * FROM live_quotes").fetchall()
        console.print(f"[green]✓ {len(rows)} live quotes from cache[/green]")
        return [dict(r) for r in rows]

    console.print("[cyan]Fetching live market watch…[/cyan]")
    html  = _fetch(f"{BASE_URL}/market-watch")
    soup  = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        console.print("[red]⚠ No table in market-watch response.[/red]")
        return []

    headers = [th.get_text(strip=True).lower().replace(" ", "_").replace("(%)", "pct")
               for th in table.find("thead").find_all("th")]
    col = {h: i for i, h in enumerate(headers)}

    def cell_text(cells, key):
        idx = col.get(key)
        if idx is None or idx >= len(cells): return ""
        # get_text strips link tags too, then remove commas from numbers
        return cells[idx].get_text(strip=True).replace(",", "")

    def to_f(v):
        try: return float(v)
        except: return None

    def to_i(v):
        try: return int(float(v))
        except: return None

    records, now = [], _now()
    for tr in table.find("tbody").find_all("tr"):
        cells = tr.find_all("td")
        if not cells: continue

        # Symbol column may be wrapped in <a> — get_text handles this correctly
        sym = cell_text(cells, "symbol")
        if not sym: continue

        records.append({
            "symbol":     sym,
            "sector":     cell_text(cells, "sector"),
            "ldcp":       to_f(cell_text(cells, "ldcp")),
            "open":       to_f(cell_text(cells, "open")),
            "high":       to_f(cell_text(cells, "high")),
            "low":        to_f(cell_text(cells, "low")),
            "current":    to_f(cell_text(cells, "current")),
            "change":     to_f(cell_text(cells, "change")),
            "change_pct": cell_text(cells, "change_pct") or cell_text(cells, "change_(%)"),
            "volume":     to_i(cell_text(cells, "volume")),
            "fetched_at": now,
        })

    if records:
        conn.executemany("""
            INSERT OR REPLACE INTO live_quotes
            (symbol,sector,ldcp,open,high,low,current,change,change_pct,volume,fetched_at)
            VALUES (:symbol,:sector,:ldcp,:open,:high,:low,:current,:change,:change_pct,:volume,:fetched_at)
        """, records)
        conn.commit()
        console.print(f"[green]✓ {len(records)} live quotes saved.[/green]")

    return records


def get_quote(symbol: str) -> dict | None:
    """Live quote for a single symbol."""
    symbol = symbol.upper()
    quotes = fetch_market_watch()
    return next((q for q in quotes if q["symbol"] == symbol), None)


# ── Announcements (via psxterminal.com) ───────────────────────────────────────
def fetch_announcements(symbol: str = None, limit: int = 20) -> list[dict]:
    """
    PSX company announcements.
    Uses psxterminal.com which renders announcements server-side (no JS needed).
    Falls back to dps.psx.com.pk/downloads page for PDFs.
    """
    symbol = symbol.upper() if symbol else None
    url    = f"{TERMINAL_URL}/announcements"
    params = {"symbol": symbol} if symbol else {}

    console.print(f"[cyan]Fetching announcements{' for ' + symbol if symbol else ''}…[/cyan]")
    try:
        html = _fetch(url, params=params)
    except RuntimeError as e:
        console.print(f"[yellow]⚠ psxterminal fetch failed: {e}. Trying PSX downloads…[/yellow]")
        return _fetch_announcements_psx_fallback(symbol, limit)

    soup  = BeautifulSoup(html, "html.parser")
    table = soup.find("table")

    if not table:
        console.print("[yellow]⚠ No table at psxterminal, trying PSX fallback…[/yellow]")
        return _fetch_announcements_psx_fallback(symbol, limit)

    headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
    col     = {h: i for i, h in enumerate(headers)}

    results, conn, now = [], _get_db(), _now()

    for tr in table.find("tbody").find_all("tr")[:limit]:
        cells = tr.find_all("td")
        if not cells: continue

        def c(key, default=""):
            idx = col.get(key)
            if idx is None or idx >= len(cells): return default
            return cells[idx].get_text(strip=True)

        href = ""
        for cell in cells:
            a = cell.find("a", href=True)
            if a:
                h = a["href"]
                href = h if h.startswith("http") else TERMINAL_URL + h
                break

        sym   = c("symbol") or (symbol or "")
        title = c("title") or c("subject") or c("description") or c("announcement")
        pub   = c("date") or c("published") or c("time")

        record = {
            "id":         f"{sym}_{pub}_{abs(hash(title))}",
            "symbol":     sym,
            "company":    c("company") or c("company name"),
            "title":      title,
            "category":   c("category") or c("type"),
            "published":  pub,
            "url":        href,
            "fetched_at": now,
        }
        results.append(record)

    if results:
        conn.executemany("""
            INSERT OR REPLACE INTO announcements
            (id,symbol,company,title,category,published,url,fetched_at)
            VALUES (:id,:symbol,:company,:title,:category,:published,:url,:fetched_at)
        """, results)
        conn.commit()

    console.print(f"[green]✓ {len(results)} announcements.[/green]")
    return results


def _fetch_announcements_psx_fallback(symbol: str = None, limit: int = 20) -> list[dict]:
    """
    Fallback: check cached announcements in SQLite, or return empty.
    The PSX announcements page is JS-rendered — we can't scrape it directly.
    Future: add Playwright/Selenium for JS rendering if needed.
    """
    conn = _get_db()
    q = "SELECT * FROM announcements"
    args = []
    if symbol:
        q += " WHERE symbol=?"
        args.append(symbol)
    q += " ORDER BY published DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(q, args).fetchall()
    if rows:
        console.print(f"[green]✓ {len(rows)} cached announcements.[/green]")
        return [dict(r) for r in rows]
    console.print("[yellow]  No cached announcements. JS-rendered page — Playwright needed for live fetch.[/yellow]")
    return []


# ── Payouts ───────────────────────────────────────────────────────────────────
def fetch_payouts(symbol: str = None) -> list[dict]:
    """Dividend / bonus / rights payout history from /payouts."""
    url    = f"{BASE_URL}/payouts"
    params = {"symbol": symbol.upper()} if symbol else {}

    console.print(f"[cyan]Fetching payouts{' for ' + symbol.upper() if symbol else ''}…[/cyan]")
    html  = _fetch(url, params=params)
    soup  = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table: return []

    headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
    col     = {h: i for i, h in enumerate(headers)}
    results, conn = [], _get_db()

    for tr in table.find("tbody").find_all("tr"):
        cells = tr.find_all("td")
        if not cells: continue

        def c(key, d=""):
            idx = col.get(key)
            return cells[idx].get_text(strip=True) if idx is not None and idx < len(cells) else d

        sym = c("symbol") or (symbol or "")
        results.append({
            "symbol":       sym,
            "company":      c("company") or c("company name"),
            "payout_type":  c("type")    or c("payout type"),
            "amount":       c("amount")  or c("rate"),
            "book_closure": c("book closure") or c("book closure date"),
            "announced":    c("date")    or c("announced"),
        })

    if results:
        conn.executemany("""
            INSERT OR REPLACE INTO payouts
            (symbol,company,payout_type,amount,book_closure,announced)
            VALUES (:symbol,:company,:payout_type,:amount,:book_closure,:announced)
        """, results)
        conn.commit()

    console.print(f"[green]✓ {len(results)} payout records.[/green]")
    return results


# ── Circuit breakers ──────────────────────────────────────────────────────────
def fetch_circuit_breakers() -> list[dict]:
    """All currently halted symbols."""
    console.print("[cyan]Fetching circuit breakers…[/cyan]")
    html  = _fetch(f"{BASE_URL}/circuit-breakers")
    soup  = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table: return []

    headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
    col     = {h: i for i, h in enumerate(headers)}
    results, conn, now = [], _get_db(), _now()

    for tr in table.find("tbody").find_all("tr"):
        cells = tr.find_all("td")
        if not cells: continue

        def c(key, d=""):
            idx = col.get(key)
            return cells[idx].get_text(strip=True) if idx is not None and idx < len(cells) else d

        sym = c("symbol")
        if not sym: continue
        results.append({
            "symbol":     sym,
            "company":    c("company") or c("company name"),
            "reason":     c("reason")  or c("type"),
            "since":      c("date")    or c("since"),
            "updated_at": now,
        })

    if results:
        conn.executemany("""
            INSERT OR REPLACE INTO circuit_breakers (symbol,company,reason,since,updated_at)
            VALUES (:symbol,:company,:reason,:since,:updated_at)
        """, results)
        conn.commit()

    console.print(f"[green]✓ {len(results)} circuit breaker records.[/green]")
    return results


# ── Company snapshot ──────────────────────────────────────────────────────────
def company_snapshot(symbol: str) -> dict:
    symbol    = symbol.upper()
    start     = (date.today() - timedelta(days=90)).isoformat()
    prices    = fetch_price_history(symbol, start=start)
    quote     = get_quote(symbol)
    ann       = fetch_announcements(symbol, limit=10)
    payouts   = fetch_payouts(symbol)
    cbs       = fetch_circuit_breakers()
    is_halted = any(cb["symbol"] == symbol for cb in cbs)

    return {
        "symbol":        symbol,
        "is_halted":     is_halted,
        "live_quote":    quote,
        "price_history": prices,
        "announcements": ann,
        "payouts":       payouts,
        "snapshot_at":   _now(),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "ENGRO"

    console.rule(f"[bold cyan]Dastak PSX Scraper — {ticker}[/bold cyan]")

    # Live quote
    console.print("\n[bold]── Live Quote ──[/bold]")
    quote = get_quote(ticker)
    if quote:
        vol_str = f"{quote['volume']:,}" if quote['volume'] else "—"
        console.print(
            f"  {ticker}  |  Current: [green]{quote['current']}[/green]  "
            f"|  Change: {quote['change']} ({quote['change_pct']})  "
            f"|  Volume: {vol_str}"
        )
    else:
        console.print(f"[yellow]  No live quote for {ticker}. Symbols available — check ticker spelling.[/yellow]")

    # Price history
    console.print("\n[bold]── Last 5 EOD Rows ──[/bold]")
    prices = fetch_price_history(ticker)
    if prices:
        t = Table()
        for col in ["Date", "Open", "Close", "Volume"]:
            t.add_column(col)
        for row in prices[-5:]:
            t.add_row(
                str(row["date"]), str(row["open"]),
                str(row["close"]),
                f"{row['volume']:,}" if row["volume"] else "—"
            )
        console.print(t)
    else:
        console.print("[yellow]  No price rows returned.[/yellow]")

    # Announcements
    console.print("\n[bold]── Announcements ──[/bold]")
    anns = fetch_announcements(ticker, limit=5)
    if anns:
        for a in anns[:3]:
            console.print(f"  • [{a['published']}] {a['title'][:80]}")
    else:
        console.print("[yellow]  None found.[/yellow]")

    console.print("\n[green]Done.[/green]")