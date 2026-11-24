"""
IP job bot — turns LinkedIn job searches and job-alert emails into a running list of
in-house IP roles and a draft LinkedIn post.

  python jobbot.py --gmail                 read new alert emails from Gmail (read-only)
  python jobbot.py --eml-folder alerts/    read emails saved as .eml files (any mail app)
  python jobbot.py --text pasted.txt       read an alert copied and pasted into a text file
  python jobbot.py --mark-shared           after posting: mark the jobs in the last draft as shared

Every run updates jobs.xlsx (the running list) and post_draft.txt (the next post).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import imaplib
import json
import io
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

import alerts

HERE = Path(__file__).parent
DB_PATH = HERE / "jobs.db"
XLSX_PATH = HERE / "jobs.xlsx"
POST_PATH = HERE / "post_draft.txt"
CONFIG_PATH = HERE / "config.json"

# Every load -> change -> save of jobs.db goes through this lock, so the automatic
# background check and clicks in the app can't overwrite each other's changes.
DB_LOCK = threading.RLock()


def load_env_file(path: Path | None = None) -> None:
    """Read GMAIL_ADDRESS / GMAIL_APP_PASSWORD from a local .env file if present."""
    path = path or HERE / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def today() -> str:
    return dt.date.today().isoformat()


# ---------------------------------------------------------------------------
# Storage (SQLite: jobs.db)
# ---------------------------------------------------------------------------

FIELDS = {  # column -> default
    "key": "", "title": "", "company": "", "location": "", "pay": "", "url": "",
    "matched_as": None, "focus": "", "why": "", "why_not": "", "via_job_board": False,
    "first_seen": "", "last_seen": "", "times_seen": 1, "source": "", "shared_on": "",
    "hidden": False, "manual_include": False,
}
BOOL_FIELDS = {"via_job_board", "hidden", "manual_include"}


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    cols = ", ".join(f"{k} {'TEXT PRIMARY KEY' if k == 'key' else ''}".strip() for k in FIELDS)
    con.execute(f"CREATE TABLE IF NOT EXISTS jobs ({cols})")
    con.execute("CREATE TABLE IF NOT EXISTS processed_emails (mail_id TEXT PRIMARY KEY)")
    con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    existing = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    for k in FIELDS:  # add columns introduced by newer versions
        if k not in existing:
            con.execute(f"ALTER TABLE jobs ADD COLUMN {k}")
    return con


def load_db(path: Path | None = None) -> dict:
    path = path or DB_PATH
    legacy = path.with_name("jobs_db.json")
    if not path.exists() and legacy.exists():  # one-time move from the old JSON file
        db = json.loads(legacy.read_text(encoding="utf-8"))
        for rec in db["jobs"].values():
            for k, v in FIELDS.items():
                rec.setdefault(k, v)
        save_db(db, path)
        legacy.rename(legacy.with_suffix(".json.migrated"))
    con = _connect(path)
    con.row_factory = sqlite3.Row
    jobs = {}
    for row in con.execute("SELECT * FROM jobs"):
        rec = {k: row[k] for k in FIELDS}
        for k in BOOL_FIELDS:
            rec[k] = bool(rec[k])
        jobs[rec["key"]] = rec
    processed = [r[0] for r in con.execute("SELECT mail_id FROM processed_emails")]
    row = con.execute("SELECT v FROM meta WHERE k='last_draft'").fetchone()
    con.close()
    return {"jobs": jobs, "processed_emails": processed, "last_draft": json.loads(row[0]) if row else []}


def save_db(db: dict, path: Path | None = None) -> None:
    path = path or DB_PATH
    con = _connect(path)
    with con:
        con.execute("DELETE FROM jobs")
        cols = list(FIELDS)
        con.executemany(
            f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [[int(r.get(k, FIELDS[k])) if k in BOOL_FIELDS else r.get(k, FIELDS[k]) for k in cols]
             for r in db["jobs"].values()])
        con.executemany("INSERT OR IGNORE INTO processed_emails VALUES (?)", [(m,) for m in db["processed_emails"]])
        con.execute("INSERT OR REPLACE INTO meta VALUES ('last_draft', ?)", (json.dumps(db["last_draft"]),))
    con.close()


def is_listed(rec: dict) -> bool:
    """On the main list: matched the title rules (or added by hand), and not hidden."""
    return bool(rec["matched_as"] or rec["manual_include"]) and not rec["hidden"]


def _classify_into(rec: dict, config: dict) -> None:
    label, focus, reason = alerts.classify(rec["title"], config)
    rec["matched_as"], rec["focus"], rec["why_not"] = label, focus, reason
    rec["why"] = alerts.why_included(rec["title"], label, config) if label else ""
    rec["via_job_board"] = rec["company"].lower() in {b.lower() for b in config.get("job_boards", [])}


def refresh_classification(db: dict, config: dict) -> None:
    """Re-run the title rules on every stored job (after config.json changes)."""
    for rec in db["jobs"].values():
        _classify_into(rec, config)


def _find_existing(db: dict, job: alerts.Job) -> str | None:
    if job.key in db["jobs"]:
        return job.key
    # Fuzzy fallback, only when at least one side has no LinkedIn job ID
    # (two different IDs mean two different postings).
    for key, rec in db["jobs"].items():
        if job.job_id and key.startswith("li:"):
            continue
        if alerts.looks_like_same_job(rec["company"], rec["title"], rec["location"],
                                      job.company, job.title, job.location):
            return key
    return None


def add_jobs(db: dict, jobs: list[alerts.Job], config: dict, source: str, seen_on: str | None = None) -> tuple[int, int]:
    """Merge parsed jobs into the database. Returns (new listed jobs, new other jobs)."""
    seen_on = seen_on or today()
    new_match = new_other = 0
    for job in jobs:
        key = _find_existing(db, job)
        if key:
            rec = db["jobs"][key]
            rec["last_seen"] = max(rec["last_seen"], seen_on)
            rec["first_seen"] = min(rec["first_seen"], seen_on)
            rec["times_seen"] += 1
            rec["pay"] = rec["pay"] or job.pay
            if job.job_id and not key.startswith("li:"):  # upgrade a text-only row once we learn its ID
                rec["url"], rec["key"] = job.url, job.key
                db["jobs"][job.key] = db["jobs"].pop(key)
            continue
        rec = {**FIELDS, "key": job.key, "title": job.title, "company": job.company, "location": job.location,
               "pay": job.pay, "url": job.url, "first_seen": seen_on, "last_seen": seen_on, "source": source}
        _classify_into(rec, config)
        db["jobs"][job.key] = rec
        if rec["matched_as"]:
            new_match += 1
        else:
            new_other += 1
    return new_match, new_other


def ingest(db: dict, batches: list, config: dict) -> dict:
    """Add a list of (mail_id, date, jobs) batches, skipping emails already read."""
    already = set(db["processed_emails"])
    fresh = [b for b in batches if b[0] not in already]
    summary = {"emails": len(fresh), "skipped": len(batches) - len(fresh), "new_listed": 0, "new_other": 0, "empty": []}
    for mail_id, date, jobs in fresh:
        m, o = add_jobs(db, jobs, config, source=mail_id.split(":")[0], seen_on=date)
        summary["new_listed"] += m
        summary["new_other"] += o
        db["processed_emails"].append(mail_id)
        if not jobs:
            summary["empty"].append(mail_id)
    return summary


def status_of(rec: dict, config: dict, now: dt.date | None = None) -> str:
    now = now or dt.date.today()
    age = (now - dt.date.fromisoformat(rec["first_seen"])).days
    if age < config["new_days"]:
        return "New"
    if age < config["check_before_sharing_days"]:
        return "Active"
    return "Check it's still open"


def get_meta(key: str, path: Path | None = None) -> str | None:
    path = path or DB_PATH
    if not path.exists():
        return None
    con = _connect(path)
    row = con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    con.close()
    return row[0] if row else None


def set_meta(key: str, value: str, path: Path | None = None) -> None:
    con = _connect(path or DB_PATH)
    with con:
        con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
    con.close()


# ---------------------------------------------------------------------------
# Automatic checks (LinkedIn search for every title + Gmail if it's set up)
# ---------------------------------------------------------------------------

_AUTO_LOCK = threading.Lock()
_auto = {"thread": None, "message": ""}


def last_auto_check() -> dt.datetime | None:
    v = get_meta("last_auto_check")
    try:
        return dt.datetime.fromisoformat(v) if v else None
    except ValueError:
        return None


def auto_check_due(config: dict, now: dt.datetime | None = None) -> bool:
    last = last_auto_check()
    hours = float(config.get("auto_check_hours", 6))
    return last is None or (now or dt.datetime.now()) - last >= dt.timedelta(hours=hours)


def run_auto_check(config: dict | None = None, delay: float = 2.0) -> str:
    """Search LinkedIn for every title in search_keywords, read new Gmail alerts if Gmail
    is connected, and save everything. Network work happens outside DB_LOCK so the app
    stays usable while it runs."""
    import linkedin_search
    config = config or load_config()
    location = config.get("auto_check_location", "United States")
    terms = config.get("search_keywords", [])
    found, failed = [], []
    for i, term in enumerate(terms):
        try:
            found.extend(linkedin_search.fetch_jobs(term, location, 1, 0))
        except Exception as exc:
            failed.append(term)
        if i < len(terms) - 1 and delay:
            time.sleep(delay)
    unique = list({(j.job_id or f"{j.title}|{j.company}"): j for j in found}.values())

    batches, gmail_note = [], ""
    if gmail_configured():
        with DB_LOCK:
            snapshot = load_db()
        try:
            batches = read_gmail(snapshot, config)
        except Exception as exc:
            gmail_note = f" Gmail check failed: {exc}"

    with DB_LOCK:
        db = load_db()
        refresh_classification(db, config)
        new_listed, new_other = add_jobs(db, unique, config, source="linkedin-search")
        summary = ingest(db, batches, config)
        save_db(db)
        write_xlsx(db, config)
        set_meta("last_auto_check", dt.datetime.now().isoformat(timespec="seconds"))
    new_listed += summary["new_listed"]
    msg = f"Automatic check done: **{new_listed} new matching job(s)**."
    if failed:
        msg += f" {len(failed)} of {len(terms)} LinkedIn searches failed (they'll be retried next time)."
    return msg + gmail_note


def _auto_worker(config: dict | None) -> None:
    try:
        _auto["message"] = run_auto_check(config)
    except Exception as exc:
        _auto["message"] = f"Automatic check failed: {exc}"


def start_auto_check(force: bool = False) -> bool:
    """Start a background check if one is due (or force=True). Returns True if one is running."""
    with _AUTO_LOCK:
        t = _auto["thread"]
        if t is not None and t.is_alive():
            return True
        config = load_config()
        if not force and not auto_check_due(config):
            return False
        _auto["message"] = ""
        t = threading.Thread(target=_auto_worker, args=(config,), daemon=True, name="ip-job-auto-check")
        _auto["thread"] = t
        t.start()
        return True


def auto_check_running() -> bool:
    t = _auto["thread"]
    return t is not None and t.is_alive()


def pop_auto_message() -> str:
    msg, _auto["message"] = _auto["message"], ""
    return msg


def start_scheduler(every_minutes: float = 15) -> threading.Thread:
    """While the app is running, look every few minutes and start a check when one is due."""
    def loop():
        while True:
            try:
                start_auto_check()
            except Exception:
                pass
            time.sleep(every_minutes * 60)
    t = threading.Thread(target=loop, daemon=True, name="ip-job-scheduler")
    t.start()
    return t


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def read_gmail(db: dict, config: dict) -> list[tuple[str, str, list[alerts.Job]]]:
    """Read-only IMAP. Needs GMAIL_ADDRESS and GMAIL_APP_PASSWORD (a Google app password)."""
    address, password = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not (address and password):
        raise RuntimeError("Gmail isn't set up yet. Add GMAIL_ADDRESS and GMAIL_APP_PASSWORD (see README, step 3).")
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(address, password)
    # readonly: the bot never marks, moves or deletes anything in the mailbox.
    typ, _ = imap.select('"[Gmail]/All Mail"', readonly=True)
    if typ != "OK":
        imap.select("INBOX", readonly=True)
    typ, data = imap.uid("SEARCH", "X-GM-RAW", _imap_quote(config["gmail"]["search"]))
    uids = data[0].split()[-config["gmail"]["max_emails_per_run"]:] if typ == "OK" and data[0] else []
    done = set(db["processed_emails"])
    results = []
    for uid in uids:
        mail_id = f"gmail:{uid.decode()}"
        if mail_id in done:
            continue
        typ, msg = imap.uid("FETCH", uid, "(INTERNALDATE RFC822)")
        if typ != "OK" or not msg or not isinstance(msg[0], tuple):
            continue
        raw = msg[0][1]
        date = _internal_date(msg[0][0]) or today()
        results.append((mail_id, date, alerts.parse_lines(alerts.email_to_lines(raw))))
    imap.logout()
    return results


def _imap_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _internal_date(header: bytes) -> str | None:
    t = imaplib.Internaldate2tuple(header)
    return dt.date(t.tm_year, t.tm_mon, t.tm_mday).isoformat() if t else None


def read_eml_bytes(raw: bytes, name: str = "") -> tuple[str, str, list[alerts.Job]]:
    import email.utils
    msg = alerts.email.message_from_bytes(raw, policy=alerts.email.policy.default)
    mail_id = f"eml:{msg.get('Message-ID') or name or hashlib.sha1(raw).hexdigest()[:12]}"
    try:
        date = email.utils.parsedate_to_datetime(msg["Date"]).date().isoformat()
    except Exception:
        date = today()
    return mail_id, date, alerts.parse_lines(alerts.email_to_lines(raw))


def read_eml_folder(folder: Path, db: dict | None = None) -> list[tuple[str, str, list[alerts.Job]]]:
    return [read_eml_bytes(p.read_bytes(), p.name) for p in sorted(folder.glob("*.eml"))]


def read_text_str(text: str) -> tuple[str, str, list[alerts.Job]]:
    digest = hashlib.sha1(text.encode()).hexdigest()[:12]
    return f"text:{digest}", today(), alerts.parse_lines(alerts.text_to_lines(text))


def read_text(path: Path) -> list[tuple[str, str, list[alerts.Job]]]:
    return [read_text_str(path.read_text(errors="replace"))]


def gmail_configured() -> bool:
    return bool(os.environ.get("GMAIL_ADDRESS") and os.environ.get("GMAIL_APP_PASSWORD"))


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

def sorted_matches(db: dict, config: dict) -> list[dict]:
    order = {"New": 0, "Active": 1, "Check it's still open": 2}
    rows = [r for r in db["jobs"].values() if is_listed(r)]
    return sorted(rows, key=lambda r: (order[status_of(r, config)], r["focus"] != "IP", r["via_job_board"],
                                       -dt.date.fromisoformat(r["first_seen"]).toordinal(), r["company"].lower()))


def write_xlsx(db: dict, config: dict, path=None) -> None:
    """Write the spreadsheet to a path (default jobs.xlsx) or a file-like object."""
    path = path or XLSX_PATH
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    header_fill = PatternFill("solid", fgColor="1F3A5F")
    header_font = Font(bold=True, color="FFFFFF")

    def sheet(ws, headers, rows, widths, link_col):
        ws.append(headers)
        for c in ws[1]:
            c.fill, c.font = header_fill, header_font
        for row in rows:
            ws.append(row)
            if link_col is not None and row[link_col]:
                cell = ws.cell(row=ws.max_row, column=link_col + 1)
                cell.hyperlink, cell.style = row[link_col], "Hyperlink"
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
        for r in ws.iter_rows(min_row=2):
            for c in r:
                c.alignment = Alignment(vertical="top", wrap_text=True)
        ws.freeze_panes = "A2"
        ws.page_setup.orientation = "landscape"
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.auto_filter.ref = ws.dimensions

    ws = wb.active
    ws.title = "Matching jobs"
    sheet(ws,
          ["Status", "Company", "Title", "Matched as", "Why included", "Location", "Pay", "Posted via job board?",
           "First seen", "Shared on", "Link"],
          [[status_of(r, config), r["company"], r["title"], r["matched_as"] or "Added by hand",
            r["why"] or "Added by hand", r["location"], r["pay"],
            "Yes, find the real employer in the listing" if r["via_job_board"] else "", r["first_seen"],
            r["shared_on"], r["url"]] for r in sorted_matches(db, config)],
          [20, 20, 44, 22, 36, 24, 18, 22, 12, 12, 44], link_col=10)

    others = sorted((r for r in db["jobs"].values() if not is_listed(r)), key=lambda r: r["first_seen"], reverse=True)
    sheet(wb.create_sheet("Other jobs from alerts"),
          ["Company", "Title", "Location", "Why it was left out", "First seen", "Link"],
          [[r["company"], r["title"], r["location"], "Hidden by you" if r["hidden"] else r["why_not"],
            r["first_seen"], r["url"]] for r in others],
          [20, 52, 26, 30, 12, 44], link_col=5)

    about = wb.create_sheet("About")
    for line in [
        "Built from LinkedIn Jobs searches and job-alert emails.",
        "Status: New = first seen in the last %d days; Active = under %d days; after that, check it's still open before sharing."
        % (config["new_days"], config["check_before_sharing_days"]),
        "'Other jobs from alerts' shows everything the filter left out, so nothing disappears silently. "
        "If something there should count, edit config.json and run the bot again.",
        f"Last updated {dt.datetime.now():%Y-%m-%d %H:%M}.",
    ]:
        about.append([line])
    about.column_dimensions["A"].width = 120
    wb.save(path)


def post_candidates(db: dict, config: dict) -> list[dict]:
    return [r for r in sorted_matches(db, config)
            if not r["shared_on"] and status_of(r, config) != "Check it's still open"]


def build_post(db: dict, config: dict, keys: list[str] | None = None, notes: bool = True) -> tuple[str, list[str]]:
    if keys is None:
        picked = post_candidates(db, config)[: config["max_jobs_per_post"]]
    else:
        picked = [db["jobs"][k] for k in keys if k in db["jobs"]]
    if not picked:
        return "No new matching jobs since the last post.", []

    lines = [config["post"]["intro"], ""]
    for r in picked:
        company = r["company"] + (" [job board]" if r["via_job_board"] else "")
        title = re.sub(r"\s*\|\s*", ", ", r["title"]).strip(" :;,-–")
        lines.append(f"• {company} — {title}")
        lines.append("  " + " · ".join(x for x in [r["location"], r["pay"]] if x))
        if r["url"]:
            lines.append(f"  {r['url']}")
        lines.append("")
    lines.pop()
    lines += ["", config["post"]["outro"]]
    text = "\n".join(lines)

    if not notes:
        return text, [r["key"] for r in picked]
    note = ""
    if len(text) > 2900:  # LinkedIn posts max out around 3,000 characters
        note = "\n\n[Note to self: this is over LinkedIn's ~3,000 character limit. Cut a few jobs or lower max_jobs_per_post.]"
    boards = [r for r in picked if r["via_job_board"]]
    if boards:
        note += ("\n\n[Note to self: %s posted by a job board, not the employer. Open the link and swap in the "
                 "real company name before posting.]" % ("1 job was" if len(boards) == 1 else f"{len(boards)} jobs were"))
    return text + note, [r["key"] for r in picked]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gmail", action="store_true", help="read new LinkedIn alert emails from Gmail")
    ap.add_argument("--eml-folder", type=Path, help="folder of saved .eml alert emails")
    ap.add_argument("--text", type=Path, action="append", help="a pasted alert saved as a .txt file (repeatable)")
    ap.add_argument("--auto", action="store_true", help="search LinkedIn for every title and read Gmail (what the app does on its own)")
    ap.add_argument("--mark-shared", action="store_true", help="mark the jobs in the last draft as shared today")
    args = ap.parse_args(argv)

    load_env_file()
    config = load_config()
    if args.auto:
        print(run_auto_check(config).replace("**", ""))
        return
    db = load_db()
    refresh_classification(db, config)

    if args.mark_shared:
        for key in db["last_draft"]:
            if key in db["jobs"]:
                db["jobs"][key]["shared_on"] = today()
        print(f"Marked {len(db['last_draft'])} job(s) as shared. They won't be in the next draft.")
        db["last_draft"] = []
        save_db(db)
        write_xlsx(db, config)
        return

    batches = []
    if args.gmail:
        try:
            batches += read_gmail(db, config)
        except RuntimeError as exc:
            sys.exit(str(exc))
    if args.eml_folder:
        batches += read_eml_folder(args.eml_folder)
    for t in args.text or []:
        batches += read_text(t)
    if not batches and not (args.gmail or args.eml_folder or args.text):
        ap.print_help()
        return

    summary = ingest(db, batches, config)
    for mail_id in summary["empty"]:
        print(f"  note: no job listings found in {mail_id} (fine if it wasn't a job alert).")

    post, keys = build_post(db, config)
    db["last_draft"] = keys
    save_db(db)
    write_xlsx(db, config)
    POST_PATH.write_text(post + "\n", encoding="utf-8")

    matches = sum(1 for r in db["jobs"].values() if is_listed(r))
    print(f"Read {summary['emails']} email(s): {summary['new_listed']} new matching job(s), {summary['new_other']} new non-matching.")
    print(f"Running list: {matches} matching job(s) in {XLSX_PATH.name}")
    print(f"Draft post with {len(keys)} job(s) in {POST_PATH.name}. After posting it, run: python jobbot.py --mark-shared")


if __name__ == "__main__":
    main()
