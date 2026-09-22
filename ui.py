"""
IP Job Board: the local web interface for the job bot.

Start it with:   streamlit run ui.py      (or double-click "Open Job Board.command")
It runs only on your own Mac at http://localhost:8501.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import html
import io
import json
import os

import streamlit as st
import streamlit.components.v1 as components

import alerts
import jobbot
import linkedin_search

st.set_page_config(page_title="IP Job Board", page_icon="⚖️", layout="wide")

# ---------------------------------------------------------------------------
# Look and feel
# ---------------------------------------------------------------------------

st.markdown("""
<style>
  :root { --ink:#1b2430; --muted:#667085; --line:#dfe5df; --accent:#12645c; --accent-soft:#e5f2ee; --warm:#c85b43; --paper:#fbfcf8; }
  #MainMenu, footer, header [data-testid="stToolbar"] { visibility: hidden; }
  .stApp { background:var(--paper); }
  .block-container { padding-top: 2.7rem; max-width: 1240px; }
  h1, h2, h3 { color:var(--ink); letter-spacing:0; }
  h2 { font-size:1.35rem; }
  h3 { font-size:1.05rem; }

  .hero { display:flex; justify-content:space-between; align-items:flex-end; gap:1rem; flex-wrap:wrap;
      border-bottom:2px solid var(--ink); padding-bottom:1.15rem; margin-bottom:1.15rem; }
  .hero h1 { font-family:Georgia, serif; font-size:2.35rem; font-weight:500; margin:0; padding:0; }
  .hero p { color:var(--muted); margin:.35rem 0 0 0; font-size:.96rem; max-width:620px; }
  .hero .stamp { color:var(--muted); font-size:.82rem; }

  .stats { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:.65rem; margin-bottom:1.35rem; }
  @media (max-width: 760px) { .stats { grid-template-columns:repeat(2, minmax(0,1fr)); } }
  .stat { border:1px solid var(--line); border-radius:8px; padding:.9rem 1rem; background:#fff; }
  .stat .n { font-size:1.8rem; font-weight:750; color:var(--ink); line-height:1.1; }
  .stat .l { font-size:.82rem; color:var(--muted); margin-top:.15rem; }
  .stat.hl { background:var(--accent-soft); border-color:#b8d9d0; }

  .job-title { font-size:1.06rem; font-weight:700; color:var(--ink); margin:0 0 .18rem 0; line-height:1.3; }
  .job-meta { color:#33415a; font-size:.92rem; margin-bottom:.35rem; }
  .job-meta b { color:var(--ink); }
  .job-why { color:var(--muted); font-size:.83rem; margin-top:.35rem; }
  .pill { display:inline-block; font-size:.72rem; font-weight:600; padding:.12rem .55rem; border-radius:999px;
          margin:0 .3rem .2rem 0; border:1px solid transparent; }
  .pill.new { background:#e3f6ea; color:#17663a; border-color:#bfe6cc; }
  .pill.active { background:#eef2f7; color:#3b4a63; border-color:#d9e0ea; }
  .pill.old { background:#fff4e0; color:#8a5a00; border-color:#f3dcae; }
    .pill.ip { background:var(--accent-soft); color:var(--accent); border-color:#b8d9d0; }
  .pill.gen { background:#f4f1fb; color:#5b3f99; border-color:#e0d7f3; }
  .pill.board { background:#fdecec; color:#a12b2b; border-color:#f4caca; }
  .pill.shared { background:#f2f2f2; color:#555; border-color:#e2e2e2; }
  .pill.hand { background:#fff8d6; color:#7a6200; border-color:#efe2a2; }

    .empty { text-align:center; color:var(--muted); padding:2.5rem 1rem; border:1px dashed var(--line); border-radius:8px; background:#fff; }
  .hint { color:var(--muted); font-size:.85rem; }
    div[data-testid="stVerticalBlockBorderWrapper"] { border-radius:8px; border-color:var(--line); }
    .stTabs [data-baseweb="tab-list"] { gap:.25rem; border-bottom:1px solid var(--line); }
  .stTabs [data-baseweb="tab"] { font-weight:600; }
    .stTabs [aria-selected="true"] { color:var(--accent); }
    .stButton > button[kind="primary"] { background:var(--accent); border-color:var(--accent); }
    .stButton > button[kind="primary"]:hover { background:#0c4f49; border-color:#0c4f49; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

jobbot.load_env_file()


def require_password() -> None:
    expected = os.environ.get("APP_PASSWORD", "").strip()
    if not expected or st.session_state.get("authenticated"):
        return
    st.title("IP Job Board")
    st.caption("Enter the shared password to continue.")
    with st.form("login"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Unlock", type="primary")
    if submitted:
        if hmac.compare_digest(password, expected):
            st.session_state["authenticated"] = True
            st.rerun()
        st.error("Incorrect password.")
    st.stop()


require_password()


def load():
    config = jobbot.load_config()
    db = jobbot.load_db()
    jobbot.refresh_classification(db, config)
    return config, db


def persist(db, config):
    jobbot.save_db(db)
    jobbot.write_xlsx(db, config)


def update_job(key: str, **changes):
    config, db = load()
    if key in db["jobs"]:
        db["jobs"][key].update(changes)
        persist(db, config)


def mark_shared(keys: list[str]):
    config, db = load()
    for k in keys:
        if k in db["jobs"]:
            db["jobs"][k]["shared_on"] = jobbot.today()
    db["last_draft"] = []
    persist(db, config)
    st.session_state["flash"] = f"Marked {len(keys)} job(s) as shared. They won't show up in the next post."


def run_ingest(batches):
    config, db = load()
    summary = jobbot.ingest(db, batches, config)
    persist(db, config)
    msg = f"Read {summary['emails']} email(s): **{summary['new_listed']} new matching job(s)**, {summary['new_other']} other."
    if summary["skipped"]:
        msg += f" Skipped {summary['skipped']} already read."
    if summary["empty"]:
        msg += f" {len(summary['empty'])} had no job listings (fine if they weren't job alerts)."
    st.session_state["flash"] = msg


def esc(s) -> str:
    return html.escape(str(s or ""))


config, db = load()
listed = jobbot.sorted_matches(db, config)
statuses = {r["key"]: jobbot.status_of(r, config) for r in db["jobs"].values()}
others = sorted((r for r in db["jobs"].values() if not jobbot.is_listed(r)), key=lambda r: r["first_seen"], reverse=True)
candidates = jobbot.post_candidates(db, config)

# ---------------------------------------------------------------------------
# Header + stats
# ---------------------------------------------------------------------------

last = dt.datetime.fromtimestamp(jobbot.DB_PATH.stat().st_mtime).strftime("%b %d, %I:%M %p").replace(" 0", " ") \
    if jobbot.DB_PATH.exists() else "never"
st.markdown(f"""
<div class="hero">
    <div><h1>IP Job Board</h1>
    <p>Search, review, and share the in-house intellectual property roles worth your attention.</p></div>
  <div class="stamp">Last updated {esc(last)}</div>
</div>
<div class="stats">
  <div class="stat hl"><div class="n">{sum(statuses[r['key']] == 'New' for r in listed)}</div><div class="l">New this week</div></div>
  <div class="stat"><div class="n">{len(listed)}</div><div class="l">On the list</div></div>
  <div class="stat"><div class="n">{len(candidates)}</div><div class="l">Ready to share</div></div>
  <div class="stat"><div class="n">{sum(1 for r in listed if r['shared_on'])}</div><div class="l">Already shared</div></div>
</div>
""", unsafe_allow_html=True)

if st.session_state.get("flash"):
    st.success(st.session_state.pop("flash"))

tab_jobs, tab_post, tab_add, tab_settings = st.tabs(["Jobs", "Build a post", "Add alerts", "Settings"])

# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

STATUS_PILL = {"New": "new", "Active": "active", "Check it's still open": "old"}


def job_card(r: dict, actions: str):
    status = statuses[r["key"]]
    pills = f'<span class="pill {STATUS_PILL[status]}">{esc(status)}</span>'
    if r["matched_as"]:
        pills += f'<span class="pill {"ip" if r["focus"] == "IP" else "gen"}">{esc(r["matched_as"])}</span>'
    elif r["manual_include"]:
        pills += '<span class="pill hand">Added by hand</span>'
    if r["via_job_board"]:
        pills += '<span class="pill board">Job board, check real employer</span>'
    if r["shared_on"]:
        pills += f'<span class="pill shared">Shared {esc(r["shared_on"])}</span>'
    meta = f"<b>{esc(r['company'])}</b> · {esc(r['location'])}" + (f" · {esc(r['pay'])}" if r["pay"] else "")
    why = r["why"] if r["matched_as"] else ("Added by hand" if r["manual_include"] else r["why_not"])
    first = dt.date.fromisoformat(r["first_seen"]).strftime("%b %d").replace(" 0", " ")

    with st.container(border=True):
        left, right = st.columns([5, 1.3], vertical_alignment="center")
        left.markdown(f"""<div class="job-title">{esc(r['title'])}</div>
<div class="job-meta">{meta}</div>{pills}
<div class="job-why">{esc(why)} · first seen {first}</div>""", unsafe_allow_html=True)
        with right:
            if r["url"]:
                st.link_button("Open posting ↗", r["url"], width="stretch")
            else:
                st.caption("No link (pasted text)")
            if actions == "listed":
                if r["shared_on"]:
                    st.button("Unmark shared", key=f"us_{r['key']}", width="stretch",
                              on_click=update_job, args=(r["key"],), kwargs={"shared_on": ""})
                else:
                    st.button("Mark shared", key=f"ms_{r['key']}", width="stretch",
                              on_click=update_job, args=(r["key"],), kwargs={"shared_on": jobbot.today()})
                st.button("Hide", key=f"hd_{r['key']}", width="stretch", type="tertiary",
                          on_click=update_job, args=(r["key"],), kwargs={"hidden": True, "manual_include": False})
            elif r["hidden"]:
                st.button("Unhide", key=f"uh_{r['key']}", width="stretch",
                          on_click=update_job, args=(r["key"],), kwargs={"hidden": False})
            else:
                st.button("Add to list", key=f"ai_{r['key']}", width="stretch",
                          on_click=update_job, args=(r["key"],), kwargs={"manual_include": True})


with tab_jobs:
    if not db["jobs"]:
        st.markdown('<div class="empty"><b>No jobs yet.</b><br>Go to <b>Add alerts</b> to check Gmail, '
                    'upload alert emails, or paste one in.</div>', unsafe_allow_html=True)
    else:
        f1, f2, f3, f4 = st.columns([2.4, 2, 1.6, 1.2], vertical_alignment="bottom")
        q = f1.text_input("Search", placeholder="Company, title or location", label_visibility="collapsed")
        pick = f2.multiselect("Status", list(STATUS_PILL), default=["New", "Active"], label_visibility="collapsed",
                              placeholder="Any status")
        focus = f3.segmented_control("Focus", ["All", "IP", "General"], default="All", label_visibility="collapsed")
        show_shared = f4.toggle("Show shared", value=True)

        with st.expander("More filters", expanded=False):
            companies = sorted({r["company"] for r in listed if r["company"]})
            locations = sorted({r["location"] for r in listed if r["location"]})
            labels = sorted({r["matched_as"] for r in listed if r["matched_as"]})
            sources = sorted({r["source"] for r in listed if r["source"]})
            af1, af2, af3 = st.columns(3)
            company_pick = af1.multiselect("Company", companies, placeholder="Any company")
            location_pick = af2.multiselect("Location", locations, placeholder="Any location")
            label_pick = af3.multiselect("Role match", labels, placeholder="Any role type")
            af4, af5, af6 = st.columns(3)
            source_pick = af4.multiselect("Source", sources, placeholder="Any source")
            age_limit = af5.selectbox("First seen", ["Any time", "Past 7 days", "Past 30 days", "Past 90 days"], index=0)
            sort_by = af6.selectbox("Sort by", ["Priority", "Newest", "Company", "Title"], index=0)
            bf1, bf2, bf3 = st.columns(3)
            link_filter = bf1.selectbox("Posting link", ["Any", "Has link", "Missing link"], index=0)
            board_filter = bf2.selectbox("Job-board listing", ["Any", "Employer listings", "Job boards only"], index=0)
            show_manual = bf3.toggle("Show added by hand", value=True)

        age_days = {"Past 7 days": 7, "Past 30 days": 30, "Past 90 days": 90}.get(age_limit)

        def keep(r):
            hay = f"{r['title']} {r['company']} {r['location']} {r['matched_as'] or ''} {r['why']}".lower()
            age = (dt.date.today() - dt.date.fromisoformat(r["first_seen"])).days
            return ((not q or q.lower() in hay)
                    and (not pick or statuses[r["key"]] in pick)
                    and (focus in (None, "All") or (focus == "IP") == (r["focus"] == "IP"))
                    and (show_shared or not r["shared_on"])
                    and (not company_pick or r["company"] in company_pick)
                    and (not location_pick or r["location"] in location_pick)
                    and (not label_pick or r["matched_as"] in label_pick)
                    and (not source_pick or r["source"] in source_pick)
                    and (age_days is None or age <= age_days)
                    and (link_filter == "Any" or (link_filter == "Has link") == bool(r["url"]))
                    and (board_filter == "Any"
                         or (board_filter == "Job boards only") == bool(r["via_job_board"]))
                    and (show_manual or not r["manual_include"]))

        shown = [r for r in listed if keep(r)]
        if sort_by == "Newest":
            shown.sort(key=lambda r: r["first_seen"], reverse=True)
        elif sort_by == "Company":
            shown.sort(key=lambda r: (r["company"].lower(), r["title"].lower()))
        elif sort_by == "Title":
            shown.sort(key=lambda r: r["title"].lower())
        sort_hint = {
            "Priority": "Priority first, IP-focused roles on top",
            "Newest": "Newest first",
            "Company": "Sorted by company",
            "Title": "Sorted by title",
        }[sort_by]
        st.markdown(f'<div class="hint">Showing {len(shown)} of {len(listed)} jobs. {sort_hint}.</div>',
                    unsafe_allow_html=True)
        if not shown:
            st.markdown('<div class="empty">Nothing matches these filters.</div>', unsafe_allow_html=True)
        for r in shown[:150]:
            job_card(r, "listed")
        if len(shown) > 150:
            st.caption(f"Showing the first 150. Narrow the filters to see the other {len(shown) - 150}.")

        with st.expander(f"Other jobs from your alerts ({len(others)}): left out by the title filter or hidden by you"):
            st.markdown('<div class="hint">Nothing gets dropped silently. If something here belongs on the list, '
                        'click <b>Add to list</b>. If a whole type of title keeps getting missed, change the rules in '
                        '<b>Settings</b>.</div>', unsafe_allow_html=True)
            for r in others[:100]:
                job_card(r, "other")

# ---------------------------------------------------------------------------
# Build a post
# ---------------------------------------------------------------------------

with tab_post:
    if not candidates:
        st.markdown('<div class="empty"><b>Nothing new to share.</b><br>Every current job has been shared already, '
                    'or is old enough that it may be closed.</div>', unsafe_allow_html=True)
    else:
        left, right = st.columns([1, 1.25], gap="large")
        with left:
            st.subheader("Pick the jobs")
            n0 = min(len(candidates), config["max_jobs_per_post"])
            st.caption(f"{len(candidates)} unshared job(s). "
                       + ("All of them are" if n0 == len(candidates) else f"The top {n0} are") + " picked to start.")
            chosen = []
            for i, r in enumerate(candidates):
                label = f"**{r['company']}**: {r['title']}"
                if r["via_job_board"]:
                    label += "  ⚠️ job board"
                if st.checkbox(label, value=i < config["max_jobs_per_post"], key=f"pick_{r['key']}"):
                    chosen.append(r["key"])
        with right:
            st.subheader("Draft")
            base, _ = jobbot.build_post(db, config, keys=chosen, notes=False)
            sig = hashlib.sha1(json.dumps(chosen).encode()).hexdigest()[:10]
            text = st.text_area("Edit freely. Changing the picks on the left rebuilds the draft.", base, height=430,
                                key=f"draft_{sig}")
            n = len(text)
            color = "#a12b2b" if n > 3000 else ("#8a5a00" if n > 2700 else "#5d6b82")
            st.markdown(f'<div class="hint" style="color:{color}">{n:,} / 3,000 characters (LinkedIn\'s limit)</div>',
                        unsafe_allow_html=True)
            boards = [db["jobs"][k] for k in chosen if db["jobs"][k]["via_job_board"]]
            if boards:
                st.warning(f"{len(boards)} of these came from a job board ({', '.join(sorted({b['company'] for b in boards}))}). "
                           "Open the link and put the real employer's name in the post.")
            components.html(f"""
              <style>body{{margin:0}}</style>
                            <button id="c" style="font:600 14px system-ui;padding:9px 16px;border-radius:8px;border:1px solid #1f3a5f;
                                background:#1f3a5f;color:#fff;cursor:pointer">Copy post</button>
                            <button id="l" style="font:600 14px system-ui;padding:9px 16px;border-radius:8px;border:1px solid #12645c;
                                background:#12645c;color:#fff;cursor:pointer;margin-left:8px">Post on LinkedIn</button>
              <span id="m" style="font:13px system-ui;color:#17663a;margin-left:10px"></span>
              <script>
                const t = {json.dumps(text)};
                                const copyDraft = async () => {{
                  try {{ await navigator.clipboard.writeText(t); }}
                  catch (e) {{ const a=document.createElement('textarea'); a.value=t; document.body.appendChild(a);
                              a.select(); document.execCommand('copy'); a.remove(); }}
                                }};
                                document.getElementById('c').onclick = async () => {{
                                    await copyDraft();
                                    document.getElementById('m').textContent = 'Copied. Paste it into LinkedIn.';
                                }};
                                document.getElementById('l').onclick = async () => {{
                                    const composer = window.open('https://www.linkedin.com/feed/?shareActive=true', '_blank');
                                    await copyDraft();
                                    document.getElementById('m').textContent = composer
                                        ? 'Draft copied. Paste it into the LinkedIn composer.'
                                        : 'Draft copied. Allow pop-ups, then open LinkedIn.';
                }};
              </script>""", height=48)
            b1, b2 = st.columns(2)
            b1.button(f"Mark these {len(chosen)} as shared", type="primary", disabled=not chosen,
                      width="stretch", on_click=mark_shared, args=(chosen,))
            b2.download_button("Download as .txt", text, file_name=f"ip_jobs_post_{jobbot.today()}.txt",
                               width="stretch")
            st.caption("After Sepp posts it, click **Mark these as shared** so they don't repeat next week.")

# ---------------------------------------------------------------------------
# Add alerts
# ---------------------------------------------------------------------------

with tab_add:
    c1, c2 = st.columns(2, gap="large")
    with c1:
        with st.container(border=True):
            st.subheader("Check Gmail")
            if jobbot.gmail_configured():
                st.caption(f"Connected as **{os.environ.get('GMAIL_ADDRESS')}** (read-only). "
                           "It only reads new LinkedIn job-alert emails.")
                if st.button("Check Gmail now", type="primary", width="stretch"):
                    with st.spinner("Reading new alert emails…"):
                        try:
                            run_ingest(jobbot.read_gmail(db, config))
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Couldn't read Gmail: {exc}")
            else:
                st.caption("Uses a Google **app password**, a 16-character password just for this. "
                           "Make one at myaccount.google.com/apppasswords (2-Step Verification has to be on).")
                with st.form("gmail"):
                    addr = st.text_input("Gmail address")
                    pw = st.text_input("App password", type="password")
                    if st.form_submit_button("Save", type="primary"):
                        if addr and pw:
                            env = jobbot.HERE / ".env"
                            env.write_text(f"GMAIL_ADDRESS={addr.strip()}\nGMAIL_APP_PASSWORD={pw.strip()}\n", encoding="utf-8")
                            os.chmod(env, 0o600)
                            os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"] = addr.strip(), pw.strip()
                            st.rerun()
                        else:
                            st.error("Fill in both fields.")

        with st.container(border=True):
            st.subheader("Upload alert emails")
            st.caption("Drag alert emails out of Apple Mail or Outlook (they save as .eml), or in Gmail use "
                       "⋮ → Download message. Works with any inbox.")
            files = st.file_uploader("Alert emails", type=["eml"], accept_multiple_files=True, label_visibility="collapsed")
            if st.button("Add these emails", disabled=not files, width="stretch"):
                run_ingest([jobbot.read_eml_bytes(f.getvalue(), f.name) for f in files])
                st.rerun()

    with c2:
        with st.container(border=True):
            st.subheader("Search LinkedIn Jobs")
            st.caption("Search public job results without signing in. Results are added to this board and classified by its title rules.")
            with st.form("linkedin_search"):
                search_query = st.text_input("Keywords", value="patent counsel")
                search_location = st.text_input("Location", value="United States")
                search_pages = st.number_input("Pages", min_value=1, max_value=4, value=1)
                search_delay = st.number_input("Seconds between pages", min_value=0.0, max_value=10.0, value=1.0, step=0.5)
                search_submit = st.form_submit_button("Search and add jobs", type="primary", width="stretch")
            if search_submit:
                if not search_query.strip():
                    st.error("Enter keywords to search for.")
                else:
                    with st.spinner("Searching public LinkedIn Jobs results…"):
                        try:
                            found = linkedin_search.fetch_jobs(
                                search_query.strip(), search_location.strip(), int(search_pages), search_delay
                            )
                            listed_count, other_count = jobbot.add_jobs(db, found, config, source="linkedin-search")
                            persist(db, config)
                            st.session_state["flash"] = (
                                f"Added {listed_count} matching job(s) and {other_count} other job(s) from LinkedIn search."
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(f"LinkedIn search failed: {exc}")

        with st.container(border=True):
            st.subheader("Paste an alert")
            st.caption("Open a job-alert email, select all, copy, and paste it here. Pasted text has no links, "
                       "but if the same job later arrives by email, the link gets filled in.")
            pasted = st.text_area("Alert text", height=300, label_visibility="collapsed",
                                  placeholder="Your job alert for …\n\nScale AI\nLead Counsel, Product and Intellectual Property\n"
                                              "Scale AI · San Francisco Bay Area (Hybrid)\n…")
            if pasted.strip():
                preview = alerts.parse_lines(alerts.text_to_lines(pasted))
                st.caption(f"Found {len(preview)} job(s) in this text.")
            if st.button("Add pasted alert", type="primary", disabled=not pasted.strip(), width="stretch"):
                run_ingest([jobbot.read_text_str(pasted)])
                st.rerun()

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

with tab_settings:
    with st.form("settings"):
        st.subheader("Post wording")
        intro = st.text_area("Opening line", config["post"]["intro"], height=80)
        outro = st.text_area("Closing line", config["post"]["outro"], height=80)

        st.subheader("Timing")
        t1, t2, t3 = st.columns(3)
        new_days = t1.number_input("Counts as “New” for (days)", 1, 60, config["new_days"])
        old_days = t2.number_input("Flag “check it's still open” after (days)", 7, 180, config["check_before_sharing_days"])
        max_post = t3.number_input("Jobs per post", 1, 25, config["max_jobs_per_post"])

        st.subheader("Job boards")
        boards = st.text_area("Companies that are really job boards, one per line. Their posts get flagged.",
                              "\n".join(config["job_boards"]), height=150)

        st.subheader("Title list")
        st.caption("A job makes the list if its title fits one of these rules. “IP” means any of: "
                   + ", ".join(t.rstrip("*") for t in config["ip_terms"]) + ".")
        st.dataframe([{"Shows as": r["label"],
                       "Title must include": " + ".join(r.get("all", [])),
                       "…and one of": " / ".join(r.get("any", [])) + (
                           (" + one of " + " / ".join(r["any2"])) if r.get("any2") else "")}
                      for r in config["title_rules"]], hide_index=True, width="stretch")
        excl = st.text_area("Never include titles containing (one per line)", "\n".join(config["exclude_terms"]), height=120)
        with st.expander("Advanced: edit the rules directly"):
            rules_json = st.text_area("Title rules (JSON)", json.dumps(config["title_rules"], indent=1), height=260)
            ip_json = st.text_input("IP words (JSON list)", json.dumps(config["ip_terms"]))

        if st.form_submit_button("Save settings", type="primary"):
            try:
                new = dict(config)
                new["post"] = {"intro": intro.strip(), "outro": outro.strip()}
                new["new_days"], new["check_before_sharing_days"], new["max_jobs_per_post"] = int(new_days), int(old_days), int(max_post)
                new["job_boards"] = [x.strip() for x in boards.splitlines() if x.strip()]
                new["exclude_terms"] = [x.strip() for x in excl.splitlines() if x.strip()]
                new["title_rules"] = json.loads(rules_json)
                new["ip_terms"] = json.loads(ip_json)
                assert all("label" in r for r in new["title_rules"]), "every rule needs a label"
                jobbot.CONFIG_PATH.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                _, db2 = load()  # re-applies the rules to every saved job
                persist(db2, new)
                st.session_state["flash"] = "Settings saved, and the rules were re-applied to every job."
                st.rerun()
            except Exception as exc:
                st.error(f"Didn't save: {exc}")

    st.divider()
    buf = io.BytesIO()
    jobbot.write_xlsx(db, config, buf)
    st.download_button("Download the full list as a spreadsheet (.xlsx)", buf.getvalue(),
                       file_name=f"ip_jobs_{jobbot.today()}.xlsx")
    st.caption(f"Data is saved on this Mac in {jobbot.DB_PATH.name} (a SQLite database). Back it up if you care about the history.")
