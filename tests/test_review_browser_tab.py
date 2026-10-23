"""显式 tab_id 不存在时，不能静默转到其他浏览器页面。"""

import pytest

from pyagent.tools.browser.bridge import BrowserBridge, TabInfo
from pyagent.tools.browser.exceptions import BrowserNotConnectedError


def test_unknown_explicit_tab_fails_instead_of_fallback():
    bridge = BrowserBridge()
    bridge._tabs["123"] = TabInfo(id="123", url="https://example.com")
    bridge._default_tab_id = "123"

    with pytest.raises(BrowserNotConnectedError, match="不存在"):
        bridge._resolve_tab("456")


def test_unset_tab_uses_default_and_known_tab_works():
    bridge = BrowserBridge()
    bridge._tabs["123"] = TabInfo(id="123", url="https://example.com")
    bridge._default_tab_id = "123"
    assert bridge._resolve_tab(None) == "123"
    assert bridge._resolve_tab("123") == "123"
