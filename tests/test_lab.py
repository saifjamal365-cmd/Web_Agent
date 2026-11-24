"""The hard kinds of pages from the capability eval (tests/fixtures/lab), without the model: can the browser
layer see and use them? Shadow DOM, embedded frames, hover menus, drag and drop, sliders and switches, a canvas
spreadsheet typed with the keyboard, a rich text editor, a canvas chart."""
import sys
import time
from pathlib import Path

import pytest

from conftest import ref_for
from webagent.agent import tools as T
from webagent.browser.session import BrowserSession
from webagent.config import settings

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import eval_lab as L  # noqa: E402


@pytest.fixture(scope="module")
def lab():
    srv = L.start_lab(0)
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    b = BrowserSession("t-lab", headless=True, channel=settings.browser_channel).start()
    yield b
    b.close()


def wait_state(name, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end and name not in L.STATE:
        time.sleep(0.1)
    return L.STATE.get(name)


def test_shadow_dom_elements_and_text_are_seen(browser, lab):
    L.STATE.clear()
    browser.goto(lab + "/outdoor.html")
    snap = browser.snapshot()
    assert "54.90 JOD" in snap.text and "Trail Backpack 30L" in snap.text
    assert "54.90 JOD" in browser.read_text()
    browser.click(ref_for(snap.text, "Add to wishlist"))
    assert wait_state("wishlist") == {"items": ["Trail Backpack 30L"]}


def test_form_inside_an_embedded_frame(browser, lab):
    L.STATE.clear()
    browser.goto(lab + "/blog.html")
    snap = browser.snapshot()
    assert 'inside the embedded frame "Newsletter sign-up"' in snap.text
    browser.type(ref_for(snap.text, "Your email"), "omar@example.com")
    browser.click(ref_for(snap.text, "Join the newsletter"))
    assert wait_state("newsletter") == {"email": "omar@example.com"}


def test_hover_menu_shows_its_links(browser, lab):
    browser.goto(lab + "/garden.html")
    snap = browser.snapshot()
    assert "Garden tools" not in snap.text
    browser.hover(ref_for(snap.text, "Shop by category"))
    snap = browser.snapshot()
    browser.click(ref_for(snap.text, "Garden tools"))
    assert browser.url.endswith("cat-7f3.html")


def test_drag_a_card_to_another_column(browser, lab):
    L.STATE.clear()
    browser.goto(lab + "/board.html")
    snap = browser.snapshot()
    assert T.gate("drag", T.validate("drag", {"ref": "e1", "to_ref": "e99"}), snap, None).kind == "refuse"
    L.STATE.clear()
    assert '[draggable] "Write report"' in snap.text
    done = next(l.split()[0] for l in snap.text.splitlines() if '[drop area] "Done"' in l)
    out = browser.drag(ref_for(snap.text, "Write report"), done)
    assert "Write report" in wait_state("board")["Done"]
    # a moved card only re-orders the page text: the result must not say "nothing changed" (eval: the agent then
    # kept dragging and dropped the card in the wrong column)
    assert "No visible text changed" not in out and '"Write report" is now inside "Done"' in out


def test_slider_and_switch(browser, lab):
    L.STATE.clear()
    browser.goto(lab + "/settings.html")
    snap = browser.snapshot()
    slider = ref_for(snap.text, "Brightness")
    assert "(slider 0-100" in snap.text
    assert "now at 70" in browser.type(slider, "70")
    out = browser.click(ref_for(snap.text, "Dark mode"))
    assert "is now checked" in out
    browser.click(ref_for(snap.text, "Save settings"))
    st = wait_state("settings")
    assert st["brightness"] == 70 and st["dark"] is True


def test_canvas_spreadsheet_with_the_keyboard(browser, lab):
    L.STATE.clear()
    browser.goto(lab + "/sheets.html")
    browser.click(ref_for(browser.snapshot().text, "Blank spreadsheet"))
    snap = browser.snapshot()
    grid = next(l.split()[0] for l in snap.text.splitlines() if "[canvas]" in l)
    assert "(a drawing" in snap.text
    assert grid
    browser.keys("A1\n", ref_for(snap.text, "Name box"))      # go to A1 first, then type the rows
    browser.keys("Date\tProduct\tUnits\nMon\tMilk\t3\n")
    time.sleep(0.3)
    cells = L.STATE["sheets"]["cells"]
    assert cells[0][:3] == ["Date", "Product", "Units"] and cells[1][:3] == ["Mon", "Milk", "3"]
    # the way the agent is told to do it: click the grid (lands in a middle cell, and it is told so), Ctrl+Home, type
    browser.snapshot()
    assert "not A1" in browser.click(grid)
    out = browser.keys("{Ctrl+Home}Day\tItem\n", grid)
    assert "check the result with look" in out
    time.sleep(0.3)
    assert L.STATE["sheets"]["cells"][0][:2] == ["Day", "Item"]
    # typing a table into a one-line field warns the agent
    snap = browser.snapshot()
    out = browser.type(ref_for(snap.text, "Name box"), "a\tb\nc")
    assert "one-line field" in out


def test_rich_text_editor_keeps_line_breaks(browser, lab):
    L.STATE.clear()
    browser.goto(lab + "/docs.html")
    snap = browser.snapshot()
    browser.type(ref_for(snap.text, "Document title"), "Weekly plan")
    browser.type(ref_for(snap.text, "Document body"), "Monday: report\nTuesday: calls\nWednesday: review")
    browser.click(ref_for(snap.text, "Save document"))
    d = wait_state("docs")
    assert d["title"] == "Weekly plan"
    assert [l for l in d["text"].splitlines() if l.strip()] == ["Monday: report", "Tuesday: calls", "Wednesday: review"]


def test_keys_refuses_secrets_and_asks_before_enter_in_a_form():
    from webagent.browser.session import Snapshot
    snap = Snapshot(url="http://x", title="t", text="", refs={
        "e1": {"ref": "e1", "role": "input:password", "name": "Password", "type": "password"},
        "e2": {"ref": "e2", "role": "input:text", "name": "Message", "form": "contact send"}})
    assert T.gate("keys", T.validate("keys", {"text": "hunter2", "ref": "e1"}), snap, None).kind == "refuse"
    assert T.gate("keys", T.validate("keys", {"text": "4111 1111 1111 1111"}), snap, None).kind == "refuse"
    assert T.gate("keys", T.validate("keys", {"text": "Mon\tMilk"}), snap, None).kind == "ok"


def test_counting_with_find_in_page_after_show_more(browser, lab):
    browser.goto(lab + "/reviews.html")
    for _ in range(10):
        snap = browser.snapshot()
        if "Show more reviews" not in snap.text:
            break
        browser.click(ref_for(snap.text, "Show more reviews"))
    out = browser.find_text("1 of 5 stars")
    assert out.splitlines()[1].startswith("4 line(s) contain exactly")
    assert "4 line(s)" in browser.find_text("★☆☆☆☆")


def test_made_up_personal_details_are_refused():
    from webagent.browser.session import Snapshot
    snap = Snapshot(url="http://x/contact", title="t", text="", refs={
        "e1": {"ref": "e1", "role": "textarea", "name": "Message"},
        "e2": {"ref": "e2", "role": "input:text", "name": "Your name", "field": "name name"},
        "e3": {"ref": "e3", "role": "input:email", "name": "Email", "type": "email"},
        "e4": {"ref": "e4", "role": "input:text", "name": "Name box"},
        "e5": {"ref": "e5", "role": "input:text", "name": "Product name"}})
    form = lambda **kv: T.validate("fill_form", {"fields": [{"ref": r, "value": v} for r, v in kv.items()]})
    task = "send a message to the store with the contact form."
    bad = T.invented_personal("fill_form", form(e2="Test User", e3="test@example.com", e1="Hi"), snap, task)
    assert "Test User" in bad and "test@example.com" in bad and "ask_user" in bad
    # what the person said in their reply is fine
    told = task + "\nthe person answered: name: sara, email sara@example.com"
    assert T.invented_personal("fill_form", form(e2="Sara", e3="sara@example.com", e1="Open Friday?"), snap, told) == ""
    # not personal fields; and the person asked for made-up values
    assert T.invented_personal("type", T.validate("type", {"ref": "e4", "text": "A1"}), snap, task) == ""
    assert T.invented_personal("type", T.validate("type", {"ref": "e5", "text": "Kettle"}), snap, task) == ""
    assert T.invented_personal("fill_form", form(e2="Ali Test"), snap, "fill the form with random test data") == ""


def test_key_names_people_write_are_understood():
    assert BrowserSession.key_name("Ctrl+Home") == "Control+Home"
    assert BrowserSession.key_name("cmd+shift+z") == "Meta+Shift+z"
    assert BrowserSession.key_name("esc") == "Escape" and BrowserSession.key_name("f5") == "F5"


def test_typing_a_table_into_a_drawn_grid_is_checked_with_vision(browser, lab, monkeypatch):
    asked = []
    monkeypatch.setattr(T.vision, "look", lambda llm, shot, q: (asked.append(q) or "A1: Day, B1: Item", 7))
    browser.goto(lab + "/sheets.html")
    browser.click(ref_for(browser.snapshot().text, "Blank spreadsheet"))
    snap = browser.snapshot()
    grid = next(l.split()[0] for l in snap.text.splitlines() if "[canvas]" in l)
    r = T.run("keys", T.validate("keys", {"text": "{Ctrl+Home}Day\tItem\n", "ref": grid}), browser, None, snap)
    assert asked and "Vision: A1: Day, B1: Item" in r.text and r.tokens == 7


def test_lessons_with_element_refs_are_not_saved(monkeypatch):
    from webagent.agent import memory
    saved = []
    monkeypatch.setattr(memory.db, "save_skill", lambda d, t, e: saved.append(t))
    memory.remember("shop.test", "Click e5 to send the form")
    memory.remember("shop.test", "The contact form is under Help > Contact us")
    assert saved == ["The contact form is under Help > Contact us"]


def test_a_table_typed_into_a_drawn_grid_starts_at_the_first_cell(browser, lab):
    L.STATE.clear()
    browser.goto(lab + "/sheets.html")
    browser.click(ref_for(browser.snapshot().text, "Blank spreadsheet"))
    snap = browser.snapshot()
    grid = next(l.split()[0] for l in snap.text.splitlines() if "[canvas]" in l)
    out = browser.keys("Date\tProduct\nMon\tMilk", grid)   # no Ctrl+Home and no last Enter: the code adds both
    assert "starting at the first cell" in out
    time.sleep(0.3)
    cells = L.STATE["sheets"]["cells"]
    assert cells[0][:2] == ["Date", "Product"] and cells[1][:2] == ["Mon", "Milk"]


def test_an_open_pop_up_is_named_and_its_buttons_come_first(browser, lab):
    browser.goto(lab + "/popup.html")
    snap = browser.snapshot()
    assert "A POP-UP IS OPEN" in snap.text and "The name given to this range is invalid" in snap.text
    first = next(l for l in snap.text.splitlines() if l.startswith("e"))
    assert '"OK"' in first                      # listed before the 150 toolbar buttons
    browser.click(first.split()[0])
    assert "A POP-UP IS OPEN" not in browser.snapshot().text


def test_a_table_typed_into_a_one_line_field_is_refused_before_typing():
    from webagent.browser.session import Snapshot
    snap = Snapshot(url="http://x", title="t", text="", refs={
        "e1": {"ref": "e1", "role": "input:text", "name": "Name box"},
        "e2": {"ref": "e2", "role": "textarea", "name": "Notes"}})
    g = T.gate("type", T.validate("type", {"ref": "e1", "text": "Date\tProduct\nMon\tMilk"}), snap, None)
    assert g.kind == "refuse" and "keys" in g.reason
    assert T.gate("type", T.validate("type", {"ref": "e1", "text": "A1"}), snap, None).kind == "ok"
    assert T.gate("type", T.validate("type", {"ref": "e2", "text": "line 1\nline 2"}), snap, None).kind == "ok"


def test_a_grid_under_a_clear_layer_still_gets_the_table(browser, lab):
    # Google Sheets puts a clear layer over its drawn grid, so a normal click on the canvas times out
    L.STATE.clear()
    browser.goto(lab + "/sheets.html")
    browser.click(ref_for(browser.snapshot().text, "Blank spreadsheet"))
    browser.page.evaluate("""() => { const cv = document.getElementById('grid'), r = cv.getBoundingClientRect();
      const d = document.createElement('div');
      Object.assign(d.style, {position: 'fixed', left: r.left + 'px', top: r.top + 'px', width: r.width + 'px',
                              height: r.height + 'px', zIndex: 5});
      d.addEventListener('mousedown', () => setTimeout(() => cv.focus(), 0));
      document.body.appendChild(d); }""")
    snap = browser.snapshot()
    grid = next(l.split()[0] for l in snap.text.splitlines() if "[canvas]" in l)
    out = browser.keys("Date\tProduct\nMon\tMilk", grid)
    assert "check the result with look" in out
    end = time.time() + 3      # the page reports its cells a moment later on a busy machine
    while time.time() < end:
        cells = (L.STATE.get("sheets") or {}).get("cells") or [[], []]
        if cells[1][:2] == ["Mon", "Milk"]:
            break
        time.sleep(0.1)
    assert cells[0][:2] == ["Date", "Product"] and cells[1][:2] == ["Mon", "Milk"]


def test_a_button_beyond_the_list_is_found_by_its_words(browser, lab):
    # MegaShop's header has 160 links, so Add to Cart is not among the 120 listed elements (like Amazon)
    L.STATE.clear()
    browser.goto(lab + "/mega-item.html?id=lenovo-ideapad-slim-5")
    snap = browser.snapshot()
    assert "more elements are not listed" in snap.text
    assert '[button] "Add to Cart"' in snap.text      # buttons and form controls are kept before menu links
    assert '"Sports 4"' not in snap.text
    out = browser.find_text("Add to Cart")
    line = next(l for l in out.splitlines() if '[button] "Add to Cart"' in l)
    browser.click(line.split()[0])
    assert wait_state("mega")["cart"] == ["lenovo-ideapad-slim-5"]


def test_a_slider_in_its_own_steps_says_what_it_shows(browser, lab):
    browser.goto(lab + "/megashop.html?q=lenovo")
    snap = browser.snapshot()
    slider = ref_for(snap.text, "Max price")
    out = browser.type(slider, "800")
    assert "did not take 800" in out and "go up to 100" in out
    assert '"$800"' in browser.type(slider, "40")


def test_extract_answers_from_the_whole_page_in_one_call(browser, lab):
    class Reply:
        text, tokens = "Lenovo IdeaPad Slim 5 14\" Laptop | $749.00 | 16 GB RAM", 50

    class FakeLLM:
        seen = []

        def chat(self, messages, **kw):
            self.seen.append(messages[-1]["content"])
            return Reply()
    llm = FakeLLM()
    browser.goto(lab + "/megashop.html?q=lenovo")
    r = T.run("extract", T.validate("extract", {"question": "every laptop: name, price, RAM"}), browser, llm,
              browser.snapshot())
    assert "$749.00" in r.text and r.tokens == 50
    assert "Lenovo LOQ 15" in llm.seen[0]            # the whole page went to the extractor, not to the agent
    # the model often forgets a requirement in its question (eval: "name, price" for a 16 GB task): the task goes too
    T.run("extract", T.validate("extract", {"question": "every laptop: name, price"}), browser, llm, browser.snapshot(),
          env=T.Env(task="Which Lenovo laptop with at least 16 GB RAM is the cheapest?"))
    assert "at least 16 GB RAM" in llm.seen[1] and "even if the question above forgot it" in llm.seen[1]


def test_gesture_tools_come_only_when_the_task_or_page_needs_them(browser, lab):
    assert "keys" not in T.tools_for(T.task_groups("Find a Lenovo laptop under $800 and add it to the cart"))
    assert "drag" in T.tools_for(T.task_groups("Move the card to Done on the board"))
    browser.goto(lab + "/sheets.html")
    browser.click(ref_for(browser.snapshot().text, "Blank spreadsheet"))
    assert "gestures" in T.page_groups(browser.snapshot())          # a drawn grid: the keyboard is needed
    browser.goto(lab + "/megashop.html?q=lenovo")
    assert "gestures" not in T.page_groups(browser.snapshot())


def test_a_second_add_to_cart_is_refused_unless_the_task_wants_more():
    from webagent.agent import graph as G
    from webagent.browser.session import Snapshot
    snap = Snapshot(url="http://x/item", title="t", text="", refs={
        "e1": {"ref": "e1", "role": "button", "name": "Add to Cart"},
        "e2": {"ref": "e2", "role": "link", "name": "Cart (1)"}})
    added = [{"n": 6, "tool": "click", "ok": True, "result": "New text on the page: Cart (1) | Added to Cart: Laptop"}]
    click = T.validate("click", {"ref": "e1"})
    state = {"task": "add the best laptop to the cart", "steps": added}
    assert "already added" in G._added_before(state, "click", click, snap)
    assert G._added_before({**state, "steps": []}, "click", click, snap) == ""
    assert G._added_before({**state, "task": "add two of them to the cart"}, "click", click, snap) == ""
    assert G._added_before(state, "click", T.validate("click", {"ref": "e2"}), snap) == ""


def test_a_filter_box_that_needs_go_says_so(browser, lab):
    browser.goto(lab + "/megashop.html?q=lenovo")
    browser.snapshot()
    out = browser.find_text("16 GB")
    box = next(l.split()[0] for l in out.splitlines() if '"16 GB"' in l and "checkbox" in l)
    res = browser.click(box)
    assert "is now checked" in res and "not applied yet" in res and '"Go"' in res
    go = res.split("Click ")[1].split()[0]
    assert go.startswith("e")
    browser.click(go)
    assert "ram=16" in browser.url


def test_an_empty_search_result_is_reported_as_the_answer(browser, lab):
    # Live Gmail run: "No messages matched your search" was not taken as an answer; the agent retyped the search.
    browser.goto(lab + "/megashop.html")
    assert browser._no_results_line() == ""          # a normal results page does not trigger it
    lines = browser._body_lines()
    browser.page.evaluate("() => document.body.insertAdjacentHTML('beforeend', "
                          "'<p>No messages matched your search. Try using search options.</p>')")
    out = browser._after_action(browser.url, len(browser.pages), lines)
    assert "No messages matched your search" in out and "that is the answer: none" in out


def test_extract_that_finds_nothing_suggests_asking_for_the_rows_plainly(browser, lab):
    class Reply:
        text, tokens = "Nothing found.", 10

    class FakeLLM:
        def chat(self, messages, **kw):
            return Reply()
    browser.goto(lab + "/megashop.html")
    r = T.run("extract", T.validate("extract", {"question": "every important email: sender"}), browser, FakeLLM(),
              browser.snapshot())
    assert "Nothing found" in r.text and "ask again in plain words" in r.text
    assert "already filtered" in T.EXTRACT_PROMPT


def test_a_chat_shows_its_newest_message_and_scrolls_its_own_pane(browser, lab):
    """Live run (Google Chat): the agent saw only the oldest loaded messages, its name search showed the name without
    the message under it, and scroll turned the wheel where nothing scrolls, so it said nothing new came today."""
    browser.goto(lab + "/chat.html")
    snap = browser.snapshot()
    # the start of the page text is yesterday; the screen shows the newest message
    assert "Call ended" in snap.text
    assert "Text in view now" in snap.text and "I have other tasks" in snap.text and "15 min" in snap.text
    # a name search shows what each person wrote, newest last
    found = browser.find_text("Mahmoud Ayman Sayed")
    assert "15 min  ->  I have other tasks" in found and found.rstrip().endswith("the last one is the newest.)")
    # the message list is at the bottom: scrolling down says so; scrolling up moves the list, not the still page
    assert "already at the bottom" in browser.scroll("down")
    up = browser.scroll("up")
    assert up.startswith('Scrolled up the "Message list" area'), up
    assert "Older note number" in browser.snapshot().text


def test_opening_the_same_chat_address_again_reloads_it(browser, lab):
    # 24 Nov: at "inbox.html#" page.goto only jumped inside the page, so a chat "reload" never fetched new messages
    browser.goto(f"{lab}/inbox.html#")
    assert "4:30 PM" not in browser.snapshot().text
    browser.goto(browser.url)
    assert "4:30 PM" in browser.snapshot().text
