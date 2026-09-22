"""Offline tests. Run:  pytest -q"""

import datetime as dt
import json
import sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import alerts  # noqa: E402
import jobbot  # noqa: E402

CONFIG = json.loads((ROOT / "config.json").read_text())
FIX = Path(__file__).parent / "fixtures"


def card(job_id, company, title, location, extra=""):
    link = f"https://www.linkedin.com/comm/jobs/view/{job_id}/?trackingId=abc%3D&amp;refId=xyz"
    return f"""
    <table><tr><td><a href="{link}"><img alt="{company}" src="logo.png"></a></td>
    <td><a href="{link}" style="color:#0a66c2">{title}</a>
    <p>{company} &middot; {location}</p>{extra}</td></tr></table>"""


HTML = f"""<html><head><style>.x{{color:red}}</style></head><body>
<h2>Your job alert for patent counsel</h2>
{card(4011111111, "Acme Robotics", "Senior Patent Counsel", "Irvine, CA (Hybrid)", "<p>$210K-$260K / year</p><p>Actively recruiting</p>")}
{card(4022222222, "Globex", "Director, Intellectual Property", "United States (Remote)")}
{card(4033333333, "Initech", "Patent Paralegal", "Austin, TX")}
{card(4044444444, "Ladders", "VP, IP Strategy", "New York, NY")}
<p><a href="https://www.linkedin.com/comm/jobs/search">See all jobs</a></p>
<p>This email was intended for Test User</p>
<p>Unsubscribe &middot; Help</p>
</body></html>"""


def make_eml(html, msg_id="<a1@test>", date="Mon, 21 Sep 2026 09:00:00 -0700"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "x@example.com", "Job alert"
    m["Message-ID"], m["Date"] = msg_id, date
    m.set_content("plain part")
    m.add_alternative(html, subtype="html")
    return m.as_bytes()


# ---------------------------------------------------------------- parsing

def test_pasted_alert_from_sepp():
    jobs = alerts.parse_lines(alerts.text_to_lines((FIX / "pasted_alert.txt").read_text()))
    assert [j.company for j in jobs] == ["Scale AI", "NantHealth", "AbbVie", "BioSpace", "On", "Ladders"]
    abbvie = jobs[2]
    assert abbvie.title == "Associate General Counsel, Patent Prosecution - India"
    assert abbvie.location == "North Chicago, IL" and abbvie.pay == "$182K-$346K / year"


def test_html_email_with_links():
    jobs = alerts.parse_lines(alerts.email_to_lines(make_eml(HTML)))
    assert [(j.company, j.job_id) for j in jobs] == [
        ("Acme Robotics", "4011111111"), ("Globex", "4022222222"),
        ("Initech", "4033333333"), ("Ladders", "4044444444")]
    assert jobs[0].title == "Senior Patent Counsel" and jobs[0].pay == "$210K-$260K / year"
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4011111111/"  # tracking junk stripped


def test_plain_text_email_with_urls():
    text = """Senior Patent Counsel
Acme Robotics · Irvine, CA
View job: https://www.linkedin.com/comm/jobs/view/4011111111/?trackingId=abc

IP Counsel
Globex · Remote
View job: https://www.linkedin.com/comm/jobs/view/4022222222/?trackingId=def
"""
    jobs = alerts.parse_lines(alerts.text_to_lines(text))
    assert [(j.title, j.job_id) for j in jobs] == [("Senior Patent Counsel", "4011111111"), ("IP Counsel", "4022222222")]


# ---------------------------------------------------------------- matching

def test_title_matching_on_seps_list():
    c = lambda t: alerts.classify(t, CONFIG)[0]
    assert c("Lead Counsel, Product and Intellectual Property") == "Lead IP Counsel"
    assert c("Assistant General Counsel | Remote | AirStrip, Shared Services") == "Assistant General Counsel"
    assert c("Associate General Counsel, Patent Prosecution - India") == "Associate General Counsel"
    assert c("Senior Lead - Legal Counsel, Trademarks and Brand Protection") == "Lead IP Counsel"
    assert c("Vice President, Intellectual Property") == "Vice President of IP"
    assert c("Senior Director, IP") == "Senior Director of IP"
    assert c("Director of Patents") == "Director of IP"
    assert c("Sr. Patent Counsel") == "Senior Patent Counsel"
    assert c("Managing IP Counsel") == "Managing IP Counsel"
    assert c("Patent Attorney") == "Patent Counsel"
    assert c("Intellectual Property Counsel") == "IP Counsel"
    assert c("Senior IP Counsel") == "Senior IP Counsel"
    assert c("Trademark Attorney") == "Trademark / Brand Counsel"
    assert c("IP Licensing Counsel") == "IP Counsel"
    assert c("IP Transactions Counsel") == "IP Transactions Counsel"
    assert c("Intellectual Property Lead, Biology") == "IP Lead / Manager"
    assert c("IP Portfolio Manager") == "IP Lead / Manager"
    assert c("IP Operations Specialist") == "IP Lead / Manager"


def test_non_matches_are_kept_with_a_reason():
    assert alerts.classify("Patent Paralegal", CONFIG)[:1] == (None,)
    assert "paralegal" in alerts.classify("Patent Paralegal", CONFIG)[2]
    assert alerts.classify("Senior Director - Counsel- Alliance Management", CONFIG)[0] is None
    assert alerts.classify("Software Engineer, IP Networking", CONFIG)[0] is None
    # "ip" must be a whole word: no false hits on words that merely contain it
    assert alerts.classify("Director of Partnerships and Shipping Counsel", CONFIG)[0] is None


def test_focus_flag():
    assert alerts.classify("Assistant General Counsel, Employment", CONFIG)[1] == "General in-house"
    assert alerts.classify("Associate General Counsel, Patents", CONFIG)[1] == "IP"


# ---------------------------------------------------------------- end to end

def run(tmp_path, monkeypatch, *args):
    monkeypatch.setattr(jobbot, "DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(jobbot, "XLSX_PATH", tmp_path / "jobs.xlsx")
    monkeypatch.setattr(jobbot, "POST_PATH", tmp_path / "post.txt")
    jobbot.main(list(args))
    return jobbot.load_db(tmp_path / "jobs.db")


def test_eml_folder_run_dedupes_and_builds_outputs(tmp_path, monkeypatch):
    folder = tmp_path / "alerts"
    folder.mkdir()
    (folder / "a.eml").write_bytes(make_eml(HTML, "<a1@test>", dt.datetime.now().strftime("%a, %d %b %Y 09:00:00 -0700")))
    # Same jobs arriving in a second email should not create duplicates.
    (folder / "b.eml").write_bytes(make_eml(HTML, "<a2@test>", dt.datetime.now().strftime("%a, %d %b %Y 10:00:00 -0700")))

    db = run(tmp_path, monkeypatch, "--eml-folder", str(folder))
    assert len(db["jobs"]) == 4
    assert db["jobs"]["li:4011111111"]["times_seen"] == 2
    matched = {k for k, r in db["jobs"].items() if r["matched_as"]}
    assert matched == {"li:4011111111", "li:4022222222", "li:4044444444"}  # paralegal left out
    assert db["jobs"]["li:4044444444"]["via_job_board"] is True

    post = (tmp_path / "post.txt").read_text()
    assert "Acme Robotics — Senior Patent Counsel" in post
    assert "https://www.linkedin.com/jobs/view/4011111111/" in post
    assert "Paralegal" not in post
    assert "[job board]" in post and "swap in the real company name" in post

    from openpyxl import load_workbook
    wb = load_workbook(tmp_path / "jobs.xlsx")
    assert wb.sheetnames == ["Matching jobs", "Other jobs from alerts", "About"]
    assert wb["Matching jobs"].max_row == 4 and wb["Other jobs from alerts"].max_row == 2

    # Running again reads nothing new.
    db2 = run(tmp_path, monkeypatch, "--eml-folder", str(folder))
    assert db2["jobs"]["li:4011111111"]["times_seen"] == 2

    # After marking shared, those jobs drop out of the next draft.
    run(tmp_path, monkeypatch, "--mark-shared")
    run(tmp_path, monkeypatch, "--text", str(FIX / "pasted_alert.txt"))
    post2 = (tmp_path / "post.txt").read_text()
    assert "Acme Robotics" not in post2 and "Scale AI" in post2


def test_old_jobs_are_flagged_and_left_out_of_posts(tmp_path, monkeypatch):
    folder = tmp_path / "alerts"
    folder.mkdir()
    (folder / "old.eml").write_bytes(make_eml(HTML, "<old@test>", "Mon, 03 Aug 2026 09:00:00 -0700"))
    db = run(tmp_path, monkeypatch, "--eml-folder", str(folder))
    rec = db["jobs"]["li:4011111111"]
    assert jobbot.status_of(rec, CONFIG, now=dt.date(2026, 9, 22)) == "Check it's still open"
    assert "No new matching jobs" in (tmp_path / "post.txt").read_text()


def test_gmail_reader_is_read_only_and_skips_seen(monkeypatch):
    calls = []

    class FakeIMAP:
        def __init__(self, host): calls.append(("connect", host))
        def login(self, u, p): calls.append(("login", u))
        def select(self, box, readonly=False):
            calls.append(("select", box, readonly)); return "OK", [b"2"]
        def uid(self, cmd, *args):
            calls.append((cmd, *args))
            if cmd == "SEARCH":
                return "OK", [b"101 102"]
            return "OK", [(b'102 (UID 102 INTERNALDATE "21-Sep-2026 09:00:00 -0700" RFC822 {10}', make_eml(HTML)), b")"]
        def logout(self): calls.append(("logout",))

    monkeypatch.setattr(jobbot.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setenv("GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
    db = {"jobs": {}, "processed_emails": ["gmail:101"], "last_draft": []}
    out = jobbot.read_gmail(db, CONFIG)
    assert [m for m, _, _ in out] == ["gmail:102"]          # 101 already read
    assert out[0][1] == "2026-09-21" and len(out[0][2]) == 4
    assert all(c[2] is True for c in calls if c[0] == "select")  # opened read-only
    assert not any(c[0] in ("STORE", "COPY", "MOVE", "EXPUNGE") for c in calls)
    search = next(c for c in calls if c[0] == "SEARCH")
    assert search[1] == "X-GM-RAW" and search[2].startswith('"from:jobalerts-noreply@linkedin.com')



# ---------------------------------------------------------------- merged-in features

def test_why_included_note():
    db = {"jobs": {}, "processed_emails": [], "last_draft": []}
    jobbot.add_jobs(db, alerts.parse_lines(alerts.text_to_lines((FIX / "pasted_alert.txt").read_text())), CONFIG, "text")
    by_co = {r["company"]: r for r in db["jobs"].values()}
    assert by_co["AbbVie"]["why"] == "Title matches “Associate General Counsel” on the list; IP-focused (patent)"
    assert "not IP-specific" in by_co["NantHealth"]["why"]
    assert by_co["Ladders"]["why"] == "" and by_co["Ladders"]["why_not"]


def test_fuzzy_dedupe_links_pasted_job_to_real_email():
    db = {"jobs": {}, "processed_emails": [], "last_draft": []}
    # Pasted first (no link), slightly different wording...
    pasted = "Senior Patent Counsel.\nAcme Robotics · Irvine, CA (Hybrid)\n"
    jobbot.add_jobs(db, alerts.parse_lines(alerts.text_to_lines(pasted)), CONFIG, "text")
    assert len(db["jobs"]) == 1 and next(iter(db["jobs"])).startswith("t:")
    # ...then the real email arrives with the job ID: same job, now with a link.
    jobbot.add_jobs(db, alerts.parse_lines(alerts.email_to_lines(make_eml(HTML))), CONFIG, "eml")
    acme = [r for r in db["jobs"].values() if r["company"] == "Acme Robotics"]
    assert len(acme) == 1 and acme[0]["key"] == "li:4011111111" and acme[0]["url"] and acme[0]["times_seen"] == 2


def test_two_different_job_ids_are_never_merged():
    db = {"jobs": {}, "processed_emails": [], "last_draft": []}
    j1 = alerts.Job("Senior Patent Counsel", "Acme", "Irvine, CA", job_id="4000000001")
    j2 = alerts.Job("Senior Patent Counsel", "Acme", "Irvine, CA", job_id="4000000002")
    jobbot.add_jobs(db, [j1, j2], CONFIG, "eml")
    assert len(db["jobs"]) == 2


def test_hide_and_include_by_hand_survive_save(tmp_path):
    db = {"jobs": {}, "processed_emails": [], "last_draft": []}
    jobbot.add_jobs(db, alerts.parse_lines(alerts.text_to_lines((FIX / "pasted_alert.txt").read_text())), CONFIG, "text")
    by_co = {r["company"]: r for r in db["jobs"].values()}
    by_co["Ladders"]["manual_include"] = True
    by_co["NantHealth"]["hidden"] = True
    jobbot.save_db(db, tmp_path / "jobs.db")
    db2 = jobbot.load_db(tmp_path / "jobs.db")
    jobbot.refresh_classification(db2, CONFIG)  # config re-run must not undo manual choices
    listed = {r["company"] for r in jobbot.sorted_matches(db2, CONFIG)}
    assert "Ladders" in listed and "NantHealth" not in listed


def test_old_json_file_is_migrated(tmp_path):
    old = {"jobs": {"li:1234567": {"key": "li:1234567", "title": "Patent Counsel", "company": "X", "location": "CA",
                                   "pay": "", "url": "https://www.linkedin.com/jobs/view/1234567/", "matched_as": "Patent Counsel",
                                   "focus": "IP", "why_not": "", "via_job_board": False, "first_seen": "2026-09-20",
                                   "last_seen": "2026-09-20", "times_seen": 1, "source": "eml", "shared_on": ""}},
           "processed_emails": ["eml:<x@y>"], "last_draft": ["li:1234567"]}
    (tmp_path / "jobs_db.json").write_text(json.dumps(old))
    db = jobbot.load_db(tmp_path / "jobs.db")
    assert db["jobs"]["li:1234567"]["title"] == "Patent Counsel"
    assert db["processed_emails"] == ["eml:<x@y>"] and db["last_draft"] == ["li:1234567"]
    assert not (tmp_path / "jobs_db.json").exists()
