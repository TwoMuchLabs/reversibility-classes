"""Regenerate the registry table in README.md from registry/. Run after editing records."""

from pathlib import Path

from rclass import RClass, Registry

ROOT = Path(__file__).resolve().parent.parent
START, END = "<!-- registry-stats:start -->", "<!-- registry-stats:end -->"


def main() -> None:
    reg = Registry.from_paths([ROOT / "registry"])
    rows, totals = [], {c: 0 for c in RClass}
    for record in sorted(reg.records, key=lambda r: r["server"]["name"].lower()):
        counts = {c: 0 for c in RClass}
        for tool in record["tools"]:
            counts[RClass.parse(tool["class"])] += 1
        for c in RClass:
            totals[c] += counts[c]
        sid = record["server"]["id"]
        name = record["server"]["name"]
        cells = " | ".join(str(counts[c] or "·") for c in RClass)
        rows.append(f"| [{name}](registry/{sid}.yaml) | {len(record['tools'])} | {cells} |")
    n_tools = sum(totals.values())
    header = (
        f"**{len(reg.records)} servers, {n_tools} tools classified.** Counts are base classes; "
        "some tools drop to a lower class for specific arguments.\n\n"
        "| Server | Tools | R0 | R1 | R2 | R3 | R4 |\n|---|---|---|---|---|---|---|"
    )
    total_row = f"| **Total** | **{n_tools}** | " + " | ".join(f"**{totals[c]}**" for c in RClass) + " |"
    block = "\n".join([START, header, *rows, total_row, END])
    readme = ROOT / "README.md"
    text = readme.read_text()
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    readme.write_text(before + block + after)
    print(f"{len(reg.records)} servers, {n_tools} tools")


if __name__ == "__main__":
    main()
