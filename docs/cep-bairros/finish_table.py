"""Joins the per-state CEP tables into the ERP's file: CEP, city code, neighborhood (title case),
only where most of the CEP's census addresses share one locality (share given as argv[3])."""

import gzip, re, sys
from pathlib import Path

src, out, share = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
SMALL = {"de", "da", "do", "das", "dos", "e", "d"}
ROMAN = re.compile(r"^(?=[ivxlc]+$)c{0,3}(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")


def title(name):
    words = []
    for i, word in enumerate(name.lower().split()):
        if i and word in SMALL:
            words.append(word)
        elif ROMAN.match(word):
            words.append(word.upper())
        else:
            words.append(
                word[:1].upper() + word[1:]
                if "'" not in word
                else "'".join(p[:1].upper() + p[1:] for p in word.split("'"))
            )
    return " ".join(words)


kept = dropped = 0
rows = []
for part in sorted(src.glob("*.tsv")):
    for line in part.read_text("utf-8").splitlines():
        cep, city, name, top, total = line.split("\t")
        if int(top) / int(total) < share:
            dropped += 1
            continue
        rows.append((cep, city, title(name)))
        kept += 1
rows.sort()
with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as f:
    f.writelines(f"{c}\t{m}\t{n}\n" for c, m, n in rows)
print(f"mantidos {kept}, fora {dropped}, arquivo {out.stat().st_size / 1e6:.1f} MB")
