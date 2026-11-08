"""Offline guard: founder admin must remain explicitly local-only until authenticated."""
from pathlib import Path


def validate_admin_prototype(html: str) -> None:
    required = (
        "PROTOTYPE ONLY",
        "no live P2P connection",
        "Authentication not implemented",
        "Changes are local only",
        "Export draft JSON",
    )
    missing = [phrase for phrase in required if phrase not in html]
    if missing:
        raise ValueError("admin_prototype_guard_missing: " + ", ".join(missing))


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    validate_admin_prototype((root / "apps/p2p-admin/index.html").read_text(encoding="utf-8"))
    print("Admin prototype guard: PASS")
