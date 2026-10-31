from sim import System
from tools import Toolbox

def test_forbidden_tool_is_blocked():
    tb = Toolbox(System())
    try:
        tb.call("delete_database", table="orders")
    except PermissionError as e:
        assert "allowlist" in str(e)
        return
    raise AssertionError("forbidden tool was executed")

def test_allowed_tools_are_listed():
    assert "delete_database" not in Toolbox.ALLOWED
    assert "rollback_config" in Toolbox.ALLOWED
    assert "escalate" in Toolbox.ALLOWED

if __name__ == "__main__":
    test_forbidden_tool_is_blocked()
    test_allowed_tools_are_listed()
    print("2 passed")