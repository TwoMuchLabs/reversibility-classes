import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_dco", ROOT / "scripts" / "check_dco.py")
dco = importlib.util.module_from_spec(spec)
sys.modules["check_dco"] = dco
spec.loader.exec_module(dco)


def test_signed_commit_passes():
    assert dco.missing([("a" * 40, "Ann@Example.com", "Fix\n\nSigned-off-by: Ann <ann@example.com>\n")]) == []


def test_unsigned_commit_fails():
    assert dco.missing([("b" * 40, "ann@example.com", "Fix\n")]) == [("b" * 40, "ann@example.com")]


def test_sign_off_by_someone_else_fails():
    rows = [("c" * 40, "ann@example.com", "Fix\n\nSigned-off-by: Bob <bob@example.com>\n")]
    assert dco.missing(rows) == [("c" * 40, "ann@example.com")]


def test_sign_off_must_be_a_trailer_line_not_prose():
    rows = [("d" * 40, "ann@example.com", "Mention Signed-off-by: Ann <ann@example.com> inline\n")]
    assert dco.missing(rows) == [("d" * 40, "ann@example.com")]


def test_github_bots_are_exempt():
    rows = [("e" * 40, "49699333+dependabot[bot]@users.noreply.github.com", "Bump x\n")]
    assert dco.missing(rows) == []


def test_a_human_cannot_borrow_the_bot_exemption():
    rows = [("f" * 40, "dependabot@example.com", "Bump x\n"),
            ("0" * 40, "me+[bot]@example.com", "x\n")]
    assert len(dco.missing(rows)) == 2
