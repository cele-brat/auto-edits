"""Write a copy of a preset pack in which every preset carries its full look name.

"Cele-Brat MG 01 Signature" becomes "Cele-Brat Marco Grob 01 Signature" in the
file name, in the name Lightroom shows, and in the header comment. Settings,
Product IDs and UUIDs are untouched, so importing the copy into Lightroom
replaces the short-named presets instead of duplicating them.

usage: python -m tools.rename_presets SRC_FOLDER DST_FOLDER
"""
import re
import shutil
import sys
from pathlib import Path

from engine.presets import parse_xmp


def rename_pack(src, dst):
    src, dst = Path(src), Path(dst)
    if dst.exists():
        shutil.rmtree(dst)
    renamed = {}                                   # old file name -> new, per look folder
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if path.is_dir():
            (dst / rel).mkdir(parents=True, exist_ok=True)
            continue
        (dst / rel.parent).mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() != ".xmp":
            shutil.copy2(path, dst / rel)
            continue
        text = path.read_text(encoding="utf-8")
        p = parse_xmp(text)
        new_name = f"Cele-Brat {p.display_name}"
        old = re.escape(p.name)
        text, n1 = re.subn(r"(<crs:Name>\s*<rdf:Alt>\s*<rdf:li xml:lang=\"x-default\">)" + old + r"(</rdf:li>)",
                           lambda m: m.group(1) + new_name + m.group(2), text, count=1)
        text, n2 = re.subn(r"(<!--\s*\n\s*)" + old + r"(\s*\n)", lambda m: m.group(1) + new_name + m.group(2), text, count=1)
        if n1 != 1:
            raise SystemExit(f"could not rename {rel}")
        (dst / rel.parent / f"{new_name}.xmp").write_text(text, encoding="utf-8")
        renamed[(p.id, path.name)] = f"{new_name}.xmp"
    # keep the Product ID register in step with the new file names
    by_id = {pid: new for (pid, _), new in renamed.items()}
    for csv_path in dst.rglob("*.csv"):
        lines = csv_path.read_text(encoding="utf-8").splitlines()
        out = [lines[0]]
        for line in lines[1:]:
            cells = line.split(",")
            if cells and cells[0] in by_id and len(cells) >= 5 and cells[4].endswith(".xmp"):
                cells[4] = by_id[cells[0]]
            out.append(",".join(cells))
        csv_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return len(renamed)


if __name__ == "__main__":
    print(rename_pack(sys.argv[1], sys.argv[2]), "presets renamed")
