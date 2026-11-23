"""The abilities added on top of browsing: tables, forms, files (upload, download, read), saved logins, search
fallback, helper agents (delegate) and the safety rules around them. Real browser, local test website."""
import json
import threading
import time

import pytest

from conftest import FakeLLM, ref_for
from webagent import files as F
from webagent.agent import tools as T
from webagent.browser.session import BrowserSession, Snapshot, load_sessions
from webagent.config import settings
from webagent.runner import run_task
from webagent.store import db


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    b = BrowserSession("t-abilities", headless=True, channel=settings.browser_channel,
                       downloads_dir=F.downloads_dir("t-abilities"), sessions_dir=F.SESSIONS).start()
    yield b
    b.close()


# ---------------------------------------------------------------- tables

def test_read_table_keeps_values_in_their_columns(browser, site_url):
    browser.goto(site_url + "/table.html")
    out = browser.tables("price memory")
    assert "Table: Laptop comparison" in out
    assert "| Price | 999.00 JOD | 1,249.00 JOD | 749.00 JOD |" in out
    assert "| Memory | 16 GB | 32 GB | 8 GB |" in out
    assert "2 table(s)" in out  # the one-cell layout table is not a data table
    hours = browser.tables(index=2)
    assert "Friday | Closed" in hours and "999.00 JOD" not in hours


def test_read_table_on_a_page_without_tables(browser, site_url):
    browser.goto(site_url + "/index.html")
    assert "no data table" in browser.tables()


# ---------------------------------------------------------------- forms

def test_fill_form_fills_every_kind_of_field_without_sending(browser, site_url):
    browser.goto(site_url + "/forms.html")
    page = browser.snapshot().text
    out = browser.fill_form([
        {"ref": ref_for(page, "Full name"), "value": "Omar Haddad"},
        {"ref": ref_for(page, "Email"), "value": "omar@example.com"},
        {"ref": ref_for(page, "Position"), "value": "Driver"},
        {"ref": ref_for(page, "Start date"), "value": "2026-12-01"},
        {"ref": ref_for(page, "Evening"), "value": "true"},
        {"ref": ref_for(page, "About you"), "value": "I like driving."},
    ])
    assert "Filled 6 field(s)" in out and "Nothing was sent" in out
    values = browser.page.evaluate("""() => ({n: fullname.value, e: email.value, r: role.value, s: start.value,
        ev: evening.checked, m: morning.checked, a: about.value, done: document.getElementById('done').textContent})""")
    assert values == {"n": "Omar Haddad", "e": "omar@example.com", "r": "Driver", "s": "2026-12-01", "ev": True,
                      "m": False, "a": "I like driving.", "done": ""}


def _snap(browser) -> Snapshot:
    return browser.snapshot()


def test_form_safety_rules(browser, site_url):
    browser.goto(site_url + "/forms.html")
    snap = _snap(browser)
    page = snap.text
    form = lambda *fields: T.validate("fill_form", {"fields": [{"ref": r, "value": v} for r, v in fields]})
    # passwords are refused, even inside a bigger form
    g = T.gate("fill_form", form((ref_for(page, "Full name"), "Omar"), (ref_for(page, "Account password"), "x1")), snap)
    assert g.kind == "refuse" and "password" in g.reason
    # a card number typed into any field is refused
    assert T.gate("fill_form", form((ref_for(page, "About you"), "4111 1111 1111 1111")), snap).kind == "refuse"
    # ticking "I agree to the terms" or a newsletter needs the person
    g = T.gate("fill_form", form((ref_for(page, "I agree"), "true")), snap)
    assert g.kind == "approval" and "agree" in g.reason
    assert T.gate("fill_form", form((ref_for(page, "Subscribe"), "true")), snap).kind == "approval"
    # plain fields are fine; the Submit button still needs approval
    assert T.gate("fill_form", form((ref_for(page, "Full name"), "Omar")), snap).kind == "ok"
    submit = T.validate("click", {"ref": ref_for(page, "Submit application")})
    assert T.gate("click", submit, snap).kind == "approval"


# ---------------------------------------------------------------- files

def test_upload_attaches_a_file_from_the_inbox_and_needs_approval(browser, site_url):
    cv = F.unique_path(F.INBOX, "cv-omar.pdf")
    cv.write_bytes(b"%PDF-1.4 test cv")
    browser.goto(site_url + "/forms.html")
    snap = _snap(browser)
    field = ref_for(snap.text, "CV (PDF)")
    args = T.validate("upload_file", {"ref": field, "file": cv.name})
    g = T.gate("upload_file", args, snap)
    assert g.kind == "approval" and cv.name in g.reason
    res = T.run("upload_file", args, browser, None, snap, env=T.Env(task_id="t-abilities"))
    assert res.ok and "Attached" in res.text
    assert browser.page.evaluate("() => cv.files[0].name") == cv.name
    # only files from the inbox / this task's downloads: no other paths
    bad = T.run("upload_file", T.validate("upload_file", {"ref": field, "file": "../../webagent.db"}), browser, None,
                snap, env=T.Env(task_id="t-abilities"))
    assert not bad.ok and "no file named" in bad.text


def test_click_download_saves_the_file_and_blocks_programs(browser, site_url):
    browser.goto(site_url + "/downloads.html")
    page = browser.snapshot().text
    out = browser.click(ref_for(page, "Price list"))
    assert "Downloaded the file price-list.csv" in out
    browser.snapshot()
    blocked = browser.click(ref_for(browser.last.text, "Shop app for Windows"))
    assert "blocked" in blocked
    names = [f["name"] for f in F.list_files("t-abilities")]
    assert "price-list.csv" in names and "setup.exe" not in names
    text = T.run("read_file", T.validate("read_file", {"file": "price-list.csv"}), browser, None, None,
                 env=T.Env(task_id="t-abilities")).text
    assert "Clicky Keyboard | 39.00 JOD | 7" in text


def test_read_file_from_a_web_address_reads_the_pdf(browser, site_url):
    res = T.run("read_file", T.validate("read_file", {"url": site_url + "/files/guide.pdf"}), browser, None, None,
                env=T.Env(task_id="t-abilities"))
    assert res.ok, res.text
    assert "Returns are accepted within 14 days" in res.text and "3 JOD" in res.text
    exe = T.run("read_file", T.validate("read_file", {"url": site_url + "/files/setup.exe"}), browser, None, None,
                env=T.Env(task_id="t-abilities"))
    assert not exe.ok and "program file" in exe.text


def test_read_word_and_excel_files(tmp_path):
    import docx
    import openpyxl

    d = docx.Document()
    d.add_paragraph("Meeting notes: launch on 5 January.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text, t.cell(1, 0).text, t.cell(1, 1).text = "Owner", "Task", "Sara", "Slides"
    d.save(tmp_path / "notes.docx")
    wb = openpyxl.Workbook()
    wb.active.append(["City", "Sales"])
    wb.active.append(["Amman", 1200])
    wb.save(tmp_path / "sales.xlsx")
    assert "launch on 5 January" in F.read_part(tmp_path / "notes.docx") and "Sara | Slides" in F.read_part(tmp_path / "notes.docx")
    assert "Amman | 1200" in F.read_part(tmp_path / "sales.xlsx")
    long = tmp_path / "long.txt"
    long.write_text("word " * 3000)
    assert "part 1 of 3" in F.read_part(long) and "part=2" in F.read_part(long)


# ---------------------------------------------------------------- saved logins

def test_saved_logins_only_go_to_their_own_website(tmp_path):
    (tmp_path / "shop.example.com.json").write_text(json.dumps({
        "cookies": [{"name": "sid", "value": "1", "domain": ".shop.example.com", "path": "/"},
                    {"name": "tracker", "value": "2", "domain": ".ads.example.net", "path": "/"}],
        "origins": [{"origin": "https://shop.example.com", "localStorage": [{"name": "t", "value": "x"}]},
                    {"origin": "https://evil.example.org", "localStorage": []}]}))
    st = load_sessions(tmp_path)
    assert [c["name"] for c in st["cookies"]] == ["sid"]
    assert [o["origin"] for o in st["origins"]] == ["https://shop.example.com"]
    assert load_sessions(tmp_path, only="other.com") is None
    assert load_sessions(tmp_path, only="shop.example.com")["cookies"][0]["name"] == "sid"


def test_agent_never_asks_for_login_details_in_chat():
    ask = lambda q: T.gate("ask_user", T.validate("ask_user", {"question": q}), None)
    bad = ["To sign into Google Forms, please provide the email address (or phone) for your Google account:",
           "What is your password?", "Please give me the verification code you received.",
           "Which username do you use to log in?"]
    for q in bad:
        g = ask(q)
        assert g.kind == "refuse" and "Call login" in g.reason, q
    assert ask("Which of the 3 laptops do you want me to compare?").kind == "question"
    assert ask("What name should I put on the form?").kind == "question"


def test_login_tool_asks_the_person_and_loads_their_saved_login(browser, site_url):
    g = T.gate("login", T.validate("login", {"site": "127.0.0.1"}), None)
    assert g.kind == "question" and "never sees your password" in g.reason
    env = T.Env(task_id="t-abilities")
    missing = T.run("login", T.validate("login", {"site": "127.0.0.1"}), browser, None, None, env=env)
    assert not missing.ok and "no saved login" in missing.text
    F.SESSIONS.mkdir(parents=True, exist_ok=True)
    (F.SESSIONS / "127.0.0.1.json").write_text(json.dumps({
        "cookies": [{"name": "member", "value": "yes", "domain": "127.0.0.1", "path": "/"}], "origins": []}))
    try:
        ok = T.run("login", T.validate("login", {"site": "127.0.0.1"}), browser, None, None, env=env)
        assert ok.ok and "now used" in ok.text
        browser.goto(site_url + "/index.html")
        assert "member=yes" in browser.page.evaluate("() => document.cookie")
    finally:
        (F.SESSIONS / "127.0.0.1.json").unlink()


# ---------------------------------------------------------------- search fallback

class _FakeSearchBrowser:
    def __init__(self, captcha_hosts):
        self.captcha_hosts, self.opened = captcha_hosts, []

    def goto(self, url):
        self.opened.append(url)
        return f"Opened {url}"

    def snapshot(self):
        url = self.opened[-1]
        if any(h in url for h in self.captcha_hosts):
            return Snapshot(url=url, title="", text="", captcha=True)
        if any(h in url for h in self.empty_hosts):  # what Bing shows a headless browser: a page with no results
            return Snapshot(url=url, title="search", text="", refs={"e1": {"role": "link", "name": "Images"}})
        refs = {f"e{i}": {"role": "link", "name": f"Result number {i} about the query"} for i in range(4)}
        return Snapshot(url=url, title="results", text="", refs=refs)

    empty_hosts: tuple = ()


def test_search_moves_to_the_next_engine_when_one_shows_a_bot_check(monkeypatch):
    monkeypatch.setattr(T.settings.__class__, "brave_search_key", property(lambda self: ""), raising=False)
    monkeypatch.setattr(T, "BLOCK_MEMORY", 0)
    b = _FakeSearchBrowser(["duckduckgo"])
    out = T._search(b, "petra jordan", T.SEARCH_URL)
    assert "bing.com" in out and "duckduckgo.com showed a bot check" in out
    b = _FakeSearchBrowser([])
    assert "duckduckgo" in T._search(b, "petra", T.SEARCH_URL) and len(b.opened) == 1
    b = _FakeSearchBrowser([])  # tests and custom setups use exactly their own search page
    T._search(b, "x", "http://127.0.0.1:1/search.html?q={q}")
    assert b.opened == ["http://127.0.0.1:1/search.html?q=x"]


def test_search_skips_empty_results_and_ends_with_wikipedia(monkeypatch):
    monkeypatch.setattr(T.settings.__class__, "brave_search_key", property(lambda self: ""), raising=False)
    monkeypatch.setattr(T, "BLOCK_MEMORY", 0)
    b = _FakeSearchBrowser(["duckduckgo", "brave.com"])
    b.empty_hosts = ("bing.com",)
    out = T._search(b, "petra", T.SEARCH_URL)
    assert "wikipedia.org" in b.opened[-1] and "bing.com showed no results" in out
    b = _FakeSearchBrowser(["duckduckgo", "brave.com", "wikipedia"])
    b.empty_hosts = ("bing.com",)
    with pytest.raises(T.BrowserError, match="Do not call web_search again"):
        T._search(b, "petra", T.SEARCH_URL)


def test_search_api_is_used_first_when_a_key_is_set(monkeypatch):
    monkeypatch.setattr(T.settings.__class__, "brave_search_key", property(lambda self: "test-key"), raising=False)
    monkeypatch.setattr(T, "BLOCK_MEMORY", 0)
    monkeypatch.setattr(T, "_api_search", lambda q, key: f"Search results for {q} (Brave Search API)")
    b = _FakeSearchBrowser([])
    assert "Brave Search API" in T._search(b, "petra", T.SEARCH_URL) and b.opened == []
    monkeypatch.setattr(T, "_api_search", lambda q, key: (_ for _ in ()).throw(RuntimeError("down")))
    out = T._search(b, "petra", T.SEARCH_URL)
    assert "duckduckgo" in b.opened[0] and "Brave Search API failed (RuntimeError)" in out


def test_a_search_site_that_blocked_is_skipped_for_a_while(monkeypatch):
    monkeypatch.setattr(T.settings.__class__, "brave_search_key", property(lambda self: ""), raising=False)
    monkeypatch.setattr(T, "_BLOCKED", {})
    b = _FakeSearchBrowser(["duckduckgo"])
    T._search(b, "petra", T.SEARCH_URL)
    b.opened.clear()
    out = T._search(b, "wadi rum", T.SEARCH_URL)
    assert "duckduckgo" not in " ".join(b.opened) and "blocked earlier" in out


# ---------------------------------------------------------------- the tool list and schemas

def test_tool_list_per_situation():
    all_tools = T.available()
    assert {"read_table", "fill_form", "upload_file", "read_file", "list_files", "login", "delegate"} <= all_tools
    helper = T.available(helper=True)
    assert "delegate" not in helper and "upload_file" not in helper and "login" not in helper
    for s in T.schemas():
        assert "$ref" not in json.dumps(s) and "$defs" not in json.dumps(s)


def test_desktop_gate_rules():
    class FakeDesktop:
        windows = {1: {}}
        info = {"d1": {"role": "edit", "name": "Text Editor", "password": False},
                "d2": {"role": "button", "name": "Save", "password": False},
                "d3": {"role": "edit", "name": "Password", "password": True},
                "d4": {"role": "button", "name": "Seven", "password": False}}

        def element(self, ref):
            return self.info[ref]

    d = FakeDesktop()
    v = T.validate
    assert T.gate("desktop_type", v("desktop_type", {"ref": "d1", "text": "hello"}), None, desktop=d).kind == "ok"
    assert T.gate("desktop_type", v("desktop_type", {"ref": "d3", "text": "x"}), None, desktop=d).kind == "refuse"
    assert T.gate("desktop_click", v("desktop_click", {"ref": "d2"}), None, desktop=d).kind == "approval"
    assert T.gate("desktop_click", v("desktop_click", {"ref": "d4"}), None, desktop=d).kind == "ok"
    assert T.gate("desktop_keys", v("desktop_keys", {"keys": "^s"}), None, desktop=d).kind == "approval"
    assert T.gate("desktop_keys", v("desktop_keys", {"keys": "^a"}), None, desktop=d).kind == "ok"
    # a desktop ref given to a browser tool gets a clear pointer to the right tool
    g = T.gate("click", v("click", {"ref": "d4"}), None)
    assert g.kind == "refuse" and "desktop_click" in g.reason
    assert T.gate("desktop_open", v("desktop_open", {"app": "regedit"}), None).kind == "refuse"
    assert T.gate("desktop_open", v("desktop_open", {"app": "Notepad"}), None).kind == "ok"


# ---------------------------------------------------------------- custom form boxes and long values

def test_custom_checkboxes_show_if_they_are_ticked_and_long_links_are_whole(browser, site_url):
    browser.goto(site_url + "/survey.html")
    snap = browser.snapshot()
    agree = ref_for(snap.text, "I agree to take part")
    assert f'{agree} [checkbox] "I agree to take part" (not checked)' in snap.text
    browser.click(agree)
    snap = browser.snapshot()
    assert f'{ref_for(snap.text, "I agree to take part")} [checkbox] "I agree to take part" (checked)' in snap.text
    assert '[radio] "Yes" (not checked) in "Do you like the shop?"' in snap.text
    # a click on a box says what it did (ticking changes no text on the page)
    out = browser.click(ref_for(snap.text, "Yes"))
    assert 'is now checked' in out and 'in "Do you like the shop?"' in out
    snap = browser.snapshot()
    out = T.run("fill_form", T.validate("fill_form", {"fields": [{"ref": ref_for(snap.text, "No", "radio"), "value": "true"}]}),
                browser=browser, llm=None, snap=snap)
    assert out.ok and '[radio] "No" (checked)' in browser.snapshot().text
    link = "https://forms.example.test/forms/d/e/1FAIpQLSfwwY5tfEZ5xrqfN6ynYRX4l0liCjjwWTy3LQOvEmV5XeN1og/viewform"
    assert f'value="{link}"' in snap.text
