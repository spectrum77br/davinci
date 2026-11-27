"""IBGE CNEFE 2022 (public) → one row per CEP: city code, most common locality, its count, total.
Reads each state's zip as a stream (never unpacked) and keeps nothing else."""

import io, sys, zipfile
from collections import Counter, defaultdict

zip_path, out_path = sys.argv[1], sys.argv[2]
z = zipfile.ZipFile(zip_path)
member = z.infolist()[0]
raw = z.open(member)
probe = raw.read(4 << 20)
try:
    probe.decode("utf-8")
    encoding = "utf-8"
except UnicodeDecodeError as error:
    encoding = "utf-8" if error.start > len(probe) - 4 else "latin-1"
raw.close()
stream = io.TextIOWrapper(
    z.open(member), encoding=encoding, errors="replace", newline=""
)
header = stream.readline().rstrip("\r\n").split(";")
i_cep, i_loc, i_mun = (
    header.index("CEP"),
    header.index("DSC_LOCALIDADE"),
    header.index("COD_MUNICIPIO"),
)
cut = max(i_cep, i_loc, i_mun) + 1
places = defaultdict(Counter)
cities = {}
rows = bad = 0
for line in stream:
    parts = line.split(";", cut)
    rows += 1
    if len(parts) <= cut - 1:
        bad += 1
        continue
    cep = parts[i_cep].strip().strip('"')
    loc = parts[i_loc].strip().strip('"')
    if len(cep) != 8 or not cep.isdigit() or not loc:
        continue
    places[cep][loc] += 1
    cities.setdefault(cep, parts[i_mun].strip().strip('"'))
with open(out_path, "w", encoding="utf-8") as out:
    for cep in sorted(places):
        counts = places[cep]
        loc, top = counts.most_common(1)[0]
        out.write(f"{cep}\t{cities[cep]}\t{loc}\t{top}\t{sum(counts.values())}\n")
print(
    f"{member.filename}: {rows} linhas, {len(places)} CEPs, encoding {encoding}, ruins {bad}, substituídos {sum(1 for c in places for l in places[c] if chr(0xFFFD) in l)}"
)
