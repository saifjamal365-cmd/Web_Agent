"""Windows desktop apps, for real (Notepad and Calculator open on the screen for a few seconds).
Opt in:  $env:WA_TEST_DESKTOP = "1"; .venv\\Scripts\\python -m pytest tests\\test_desktop.py -q"""
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32" or os.environ.get("WA_TEST_DESKTOP") != "1",
                                reason="opens real apps on the screen: set WA_TEST_DESKTOP=1")


@pytest.fixture()
def desktop(monkeypatch):
    from webagent import desktop as D

    monkeypatch.setattr(D, "available", lambda: True)
    d = D.DesktopSession()
    yield d
    d.close_all()


def _ref(d, name=None, role=None):
    return next(r for r, i in d.info.items() if (name is None or i["name"] == name) and (role is None or i["role"] == role))


def test_notepad_type_read_and_close_without_saving(desktop):
    out = desktop.open("notepad")
    assert "Notepad" in out
    edit = _ref(desktop, role="edit")
    assert 'value="Hello from the agent"' in desktop.type(edit, "Hello from the agent")
    assert desktop.screenshot()  # the live view can show the app
    desktop._window().close()
    page = desktop.read()
    assert "Do you want to save" in page  # the app's own question is part of the window list
    assert "No app windows" in desktop.click(_ref(desktop, name="Don't Save"))


def test_calculator(desktop):
    desktop.open("calculator")
    assert "already open" in desktop.open("calculator") and len(desktop.windows) == 1  # no second window
    for label in ("Seven", "Multiply by", "Six", "Equals"):
        desktop.click(_ref(desktop, name=label))
    assert any("Display is 42" in i["name"] for i in desktop.info.values())


def test_only_allowed_apps(desktop):
    from webagent.desktop import DesktopError

    with pytest.raises(DesktopError, match="not on the allowed"):
        desktop.open("regedit")
