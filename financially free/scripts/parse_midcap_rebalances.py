#!/usr/bin/env python3
"""Extract Nifty Midcap 150 rebalance events from NSE press-release PDFs."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

PRESS_DIR = Path(__file__).resolve().parents[1] / "data" / "nse_press"

# Announcement PDF -> index effective date (last working day of Mar/Sep)
PDF_TO_EFFECTIVE = {
    "ind_prs01092022.pdf": "2022-09-30",
    "ind_prs17082023.pdf": "2023-09-30",
    "ind_prs28022024.pdf": "2024-03-28",
    "ind_prs23082024.pdf": "2024-09-30",
    "ind_prs21022025.pdf": "2025-03-28",
    "ind_prs22082025.pdf": "2025-09-30",
}


def extract_midcap_changes(text: str) -> tuple[list[str], list[str]] | None:
    m = re.search(
        r"\d*\)?\s*Nifty Midcap 150\s*\n+The following companies are being excluded:"
        r"(.*?)The following companies are being included:(.*?)"
        r"(?=\n\s*\d*\)?\s*Nifty Smallcap|\n\d+\)\s+Nifty Smallcap|\f\d+\)\s+Nifty )",
        text,
        re.S | re.I,
    )
    if not m:
        return None

    def symbols_from_block(block: str) -> list[str]:
        syms = []
        for part in re.split(r"Symbol\s*\n", block)[1:]:
            for line in part.splitlines():
                line = line.strip()
                if re.fullmatch(r"[A-Z][A-Z0-9&.-]{1,20}", line):
                    syms.append(line)
                elif line.startswith("Nifty ") or re.match(r"^\d+\)\s*$", line):
                    break
        return syms

    return symbols_from_block(m.group(1)), symbols_from_block(m.group(2))


def main():
    for path in sorted(PRESS_DIR.glob("*.pdf")):
        if path.read_bytes()[:4] != b"%PDF":
            continue
        text = subprocess.check_output(["pdftotext", str(path), "-"], text=True)
        if "Nifty Midcap 150" not in text:
            continue
        res = extract_midcap_changes(text)
        eff = PDF_TO_EFFECTIVE.get(path.name, "?")
        print(f"\n{path.name} -> {eff}")
        if not res:
            print("  PARSE FAILED")
            continue
        rem, add = res
        print(f"  removed ({len(rem)}):", sorted(rem))
        print(f"  added   ({len(add)}):", sorted(add))


if __name__ == "__main__":
    main()
