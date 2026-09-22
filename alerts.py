"""
Pulls job listings out of LinkedIn job-alert emails and decides which ones match.

LinkedIn job-alert emails lay each job out as a card like this:

    Scale AI                                   <- company logo (alt text)
    Lead Counsel, Product and IP               <- job title (linked to /jobs/view/<id>)
    Scale AI · San Francisco Bay Area (Hybrid) <- "company · location"
    $182K-$346K / year                         <- sometimes
    Actively recruiting                        <- sometimes

The "company · location" line is the anchor we look for. This works on the HTML
email, the plain-text email, and text copied out of an email and pasted into a file.
"""

from __future__ import annotations

import email
import email.policy
import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

JOB_ID_RE = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?\s\"']*?-)?(\d{6,})", re.I)
MARKER_RE = re.compile(r"^\[\[JOB:(\d+)\]\]$")
PAY_RE = re.compile(r"^\$[\d,.]+[KkMm]?\s*(?:-|–|to)\s*\$?[\d,.]+[KkMm]?\s*/\s*(?:year|yr|hour|hr)\b.*$|^\$[\d,.]+[KkMm]?\s*/\s*(?:year|yr|hour|hr)\b.*$")
SEP = " · "

NOISE = {
    "actively recruiting", "premium", "fast growing", "promoted", "easy apply", "be an early applicant",
    "see all jobs", "view job", "apply now", "new", "viewed", "top applicant", "reposted",
}


@dataclass
class Job:
    title: str
    company: str
    location: str
    pay: str = ""
    job_id: str = ""
    badges: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://www.linkedin.com/jobs/view/{self.job_id}/" if self.job_id else ""

    @property
    def key(self) -> str:
        if self.job_id:
            return f"li:{self.job_id}"
        return "t:" + re.sub(r"[^a-z0-9]+", "-", f"{self.company}|{self.title}|{self.location}".lower()).strip("-")


# ---------------------------------------------------------------------------
# Email -> lines of text (with [[JOB:id]] markers where job links were)
# ---------------------------------------------------------------------------

class _HtmlToLines(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "td", "th", "li", "table", "h1", "h2", "h3", "h4", "span", "a", "img"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("style", "script", "head", "title"):
            self.skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a" and a.get("href"):
            m = JOB_ID_RE.search(html.unescape(a["href"]))
            if m:
                self.parts.append(f"\n[[JOB:{m.group(1)}]]\n")
        if tag == "img" and a.get("alt"):
            self.parts.append(f"\n{a['alt']}\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self.skip = max(0, self.skip - 1)
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_lines(markup: str) -> list[str]:
    p = _HtmlToLines()
    p.feed(markup)
    return _clean_lines("".join(p.parts))


def text_to_lines(text: str) -> list[str]:
    # In plain-text emails the link is printed as a URL; turn it into a marker too.
    text = JOB_ID_RE.sub(lambda m: f"\n[[JOB:{m.group(1)}]]\n", text)
    return _clean_lines(text)


def _clean_lines(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = re.sub(r"[ \t ​‌ ]+", " ", raw).strip()
        line = re.sub(r"https?://\S+", "", line).strip() if not MARKER_RE.match(line) else line
        if line:
            out.append(line)
    return out


def email_to_lines(raw: bytes) -> list[str]:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    body = msg.get_body(preferencelist=("html", "plain"))
    if body is None:
        return []
    content = body.get_content()
    return html_to_lines(content) if body.get_content_type() == "text/html" else text_to_lines(content)


# ---------------------------------------------------------------------------
# Lines -> jobs
# ---------------------------------------------------------------------------

def _is_company_location(line: str) -> bool:
    return SEP in line and not line.startswith("[[") and len(line) < 160


FOOTER_STARTS = ("see all jobs", "this email was intended for", "you are receiving", "manage job alerts",
                 "unsubscribe", "© 20", "linkedin corporation")


def parse_lines(lines: list[str]) -> list[Job]:
    for k, l in enumerate(lines):
        if l.lower().startswith(FOOTER_STARTS):
            lines = lines[:k]
            break
    anchors = [i for i, l in enumerate(lines) if _is_company_location(l) and i > 0]
    markers = [(i, MARKER_RE.match(l).group(1)) for i, l in enumerate(lines) if MARKER_RE.match(l)]
    jobs: list[Job] = []
    links_before = bool(anchors and markers and markers[0][0] < anchors[0])

    for n, i in enumerate(anchors):
        company, _, location = lines[i].partition(SEP)
        company, location = company.strip(), location.strip()

        # Title = nearest real line above the anchor (skip link markers and repeated company name).
        j = i - 1
        while j >= 0 and (MARKER_RE.match(lines[j]) or lines[j].lower() in NOISE):
            j -= 1
        if j < 0:
            continue
        title = lines[j]
        if title.strip().lower() == company.lower() or _is_company_location(title):
            continue

        # Details below the anchor, up to the next card.
        end = anchors[n + 1] - 2 if n + 1 < len(anchors) else len(lines)
        pay, badges = "", []
        for line in lines[i + 1:max(i + 1, end)][:6]:
            if PAY_RE.match(line):
                pay = line
            elif line.lower() in NOISE and line.lower() not in ("see all jobs", "view job"):
                badges.append(line)

        # Job link. HTML emails put the link on the title/logo, i.e. before the "company · location"
        # line; plain-text emails print "View job: <url>" after it. Look on the side this email uses.
        if links_before:
            lo = anchors[n - 1] + 1 if n > 0 else 0
            ids = [jid for pos, jid in markers if lo <= pos < i]
            job_id = ids[-1] if ids else ""
        else:
            hi = anchors[n + 1] if n + 1 < len(anchors) else len(lines)
            ids = [jid for pos, jid in markers if i < pos < hi]
            job_id = ids[0] if ids else ""

        jobs.append(Job(title=title, company=company, location=location, pay=pay, job_id=job_id, badges=badges))

    # Same job can appear twice in one email (e.g. logo link + title link).
    seen, unique = set(), []
    for job in jobs:
        if job.key not in seen:
            seen.add(job.key)
            unique.append(job)
    return unique


# ---------------------------------------------------------------------------
# Matching against the title list
# ---------------------------------------------------------------------------

def normalize(title: str) -> str:
    t = title.lower().replace("intellectual property", " ip ").replace("&", " and ")
    return " " + re.sub(r"[^a-z0-9]+", " ", t).strip() + " "


def _has(norm: str, term: str, ip_terms: list[str]) -> bool:
    if term == "ip":
        return any(_has(norm, t, []) for t in ip_terms) if ip_terms else " ip " in norm
    term = term.lower()
    if term.endswith("*"):
        return re.search(r" " + re.escape(term[:-1]), norm) is not None
    return f" {term} " in norm


def classify(title: str, config: dict) -> tuple[str | None, str, str]:
    """Returns (matched_label or None, focus, reason)."""
    norm = normalize(title)
    ip_terms = config["ip_terms"]
    focus = "IP" if _has(norm, "ip", ip_terms) else "General in-house"
    for term in config["exclude_terms"]:
        if _has(norm, term, ip_terms):
            return None, focus, f"excluded: '{term}'"
    for rule in config["title_rules"]:
        if all(_has(norm, t, ip_terms) for t in rule.get("all", [])) \
           and (not rule.get("any") or any(_has(norm, t, ip_terms) for t in rule["any"])) \
           and (not rule.get("any2") or any(_has(norm, t, ip_terms) for t in rule["any2"])):
            return rule["label"], focus, ""
    return None, focus, "title didn't match the list"


def ip_terms_in(title: str, config: dict) -> list[str]:
    """Which IP words appear in the title (for the 'why included' note)."""
    norm = normalize(title)
    found = []
    for t in config["ip_terms"]:
        if _has(norm, t, []):
            found.append("IP" if t == "ip" else t.rstrip("*"))
    return found


def why_included(title: str, label: str, config: dict) -> str:
    terms = ip_terms_in(title, config)
    why = f"Title matches “{label}” on the list"
    if terms:
        why += f"; IP-focused ({', '.join(terms)})"
    else:
        why += "; general in-house role, not IP-specific"
    return why


# ---------------------------------------------------------------------------
# Fuzzy duplicate check (for jobs with no LinkedIn ID, e.g. pasted text)
# ---------------------------------------------------------------------------

def _simple(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()


def looks_like_same_job(a_company: str, a_title: str, a_loc: str, b_company: str, b_title: str, b_loc: str) -> bool:
    from difflib import SequenceMatcher
    if _simple(a_company) != _simple(b_company):
        return False
    title_sim = SequenceMatcher(None, _simple(a_title), _simple(b_title)).ratio()
    loc_sim = SequenceMatcher(None, _simple(a_loc), _simple(b_loc)).ratio()
    return title_sim >= 0.85 and loc_sim >= 0.75
