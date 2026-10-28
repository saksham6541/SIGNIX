"""Check Hindi coverage, placeholders, and parity with the shipped MO catalog."""
from pathlib import Path
import gettext

from babel.messages.pofile import read_po


def main():
    root = Path(__file__).resolve().parents[1]
    directory = root / "translations/hi/LC_MESSAGES"
    with (directory / "messages.po").open(encoding="utf-8") as stream:
        catalog = read_po(stream)
    with (directory / "messages.mo").open("rb") as stream:
        compiled = gettext.GNUTranslations(stream)
    failures = []
    for message in catalog:
        if not message.id:
            continue
        if message.fuzzy or not message.string:
            failures.append(f"Missing or fuzzy Hindi translation: {message.id!r}")
        failures.extend(f"{message.id!r}: {error}" for error in message.check(catalog))
        if isinstance(message.id, str) and compiled.gettext(message.id) != message.string:
            failures.append(f"Compiled catalog is stale: {message.id!r}")
    if failures:
        print("\n".join(failures))
        return 1
    print(f"Hindi catalog: {len(catalog)} active messages; coverage, placeholders and MO parity OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
