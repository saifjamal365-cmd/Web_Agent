"""The real browser (Edge/Chromium, headless) on the local test website."""
import pytest

from conftest import ref_for
from webagent.browser.session import BrowserError, BrowserSession
from webagent.config import settings


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    b = BrowserSession("t-browser", headless=True, channel=settings.browser_channel,
                       live_dir=tmp_path_factory.mktemp("live")).start()
    yield b
    b.close()


def test_snapshot_lists_elements_with_refs(browser, site_url):
    browser.goto(site_url + "/index.html")
    snap = browser.snapshot()
    assert "Tiny Shop" in snap.text
    search = ref_for(snap.text, "Search products")
    assert snap.refs[search]["role"] == "input:search"
    assert ref_for(snap.text, "Contact us", role="link")
    assert "Page text (start):" in snap.text and "Free delivery" in snap.text


def test_type_and_submit_search(browser, site_url):
    browser.goto(site_url + "/index.html")
    snap = browser.snapshot()
    msg = browser.type(ref_for(snap.text, "Search products"), "laptop", submit=True)
    assert "search.html" in browser.url and "Enter" in msg
    snap = browser.snapshot()
    assert "Aero Laptop 14" in snap.text and "Silent Mouse" not in snap.text


def test_click_link_select_and_read(browser, site_url):
    browser.goto(site_url + "/search.html?q=laptop")
    snap = browser.snapshot()
    browser.click(ref_for(snap.text, "Aero Laptop 14", role="link"))
    assert browser.url.endswith("product-laptop.html")
    snap = browser.snapshot()
    qty = ref_for(snap.text, "Quantity")
    assert "options: 1, 2, 3" in snap.text
    browser.select(qty, "2")
    text = browser.read_text()
    assert "999.00 JOD" in text and "part 1 of 1" in text


def test_click_reports_new_text_on_the_same_page(browser, site_url):
    browser.goto(site_url + "/product-laptop.html")
    snap = browser.snapshot()
    msg = browser.click(ref_for(snap.text, "Buy now"))
    assert "New text on the page: Order placed for 1 laptop(s)." in msg


def test_new_tab_becomes_active(browser, site_url):
    browser.goto(site_url + "/index.html")
    snap = browser.snapshot()
    msg = browser.click(ref_for(snap.text, "About (new tab)"))
    assert "new tab" in msg.lower()
    assert browser.url.endswith("about.html")
    assert "[tab 2 of 2]" in browser.snapshot().text
    browser.switch_tab(1)
    assert browser.url.endswith("index.html")


def test_unknown_ref_is_a_clear_error(browser, site_url):
    browser.goto(site_url + "/index.html")
    browser.snapshot()
    with pytest.raises(BrowserError, match="no element e999"):
        browser.click("e999")


def test_live_screenshot_is_written(browser, site_url):
    browser.goto(site_url + "/index.html")
    path = browser.save_live()
    assert path and path.exists() and path.stat().st_size > 1000


def test_bad_address_is_reported(browser):
    with pytest.raises(BrowserError, match="Could not open"):
        browser.goto("http://127.0.0.1:1/nothing")


def test_read_page_reads_the_whole_list_not_the_first_card(browser, site_url):
    # Pages where every product card is an <article> (like many shops) must be read as a whole.
    browser.goto(site_url + "/list.html")
    text = browser.read_text()
    assert "3 results" in text and "Silent Mouse" in text and "Clicky Keyboard" in text
    assert "3 results" in browser.snapshot().text


def test_find_in_page(browser, site_url):
    browser.goto(site_url + "/list.html")
    assert "Clicky Keyboard" in browser.find_text("keyboard") and "Silent Mouse" not in browser.find_text("keyboard")
    assert browser.find_text("zebra").startswith("No paragraph")


def test_long_page_says_how_to_read_the_next_part(browser, site_url):
    browser.goto(site_url + "/list.html")
    first = browser.read_text(1, size=40)  # small parts so the test page has several
    assert "part 1 of" in first and "read_page with part=2" in first and "Scrolling does not change" in first
    whole = browser.read_text()
    assert "part 1 of 1" in whole and "read_page with part=" not in whole


def test_scroll_says_when_an_endless_list_loaded_more(browser, site_url):
    browser.goto(site_url + "/infinite.html")
    out = ""
    for _ in range(4):
        out = browser.scroll("down")
        if "More content loaded" in out:
            break
    assert "More content loaded" in out and "extract or read_page again" in out
    assert "Kettle model 21" in browser.full_text(40000)


def test_opening_a_bot_check_page_says_the_site_blocked_the_browser(browser, site_url):
    out = browser.goto(site_url + "/captcha.html")
    assert "blocked the browser" in out and "another website" in out
    assert "blocked" not in browser.goto(site_url + "/index.html")


def test_finding_a_product_shows_its_own_add_to_cart_button(browser, site_url):
    # 30 "Add to cart" buttons: the page list shows only 2, so the product's own button comes with find_in_page
    browser.goto(site_url + "/cards.html")
    browser.snapshot()
    out = browser.find_text("Steel kettle model 17")
    line = next(l for l in out.splitlines() if "in the same card" in l)
    ref = line.split("in the same card:")[1].split()[0]
    assert "Add to cart" in line
    assert "model 17" in browser.click(ref)
