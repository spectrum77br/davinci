"""Neighborhood of a CEP, from IBGE's address register of the 2022 Census (CNEFE, public data at
ftp.ibge.gov.br): for each CEP, the locality most of its census addresses are in, and its city.
In towns the locality is the neighborhood. Kept only where 90% of the CEP's addresses share it:
761,124 of 931,505 CEPs. Against the neighborhoods the ERP had on 27/11/2026 (from the stores
and Bling) it was the same in 66-70%, nearly the same in 7-11%, and mostly a name the buyer
wrote that IBGE does not have otherwise; against Bling, another official one in 6%.

Used only where the store sends no neighborhood (Amazon through UpSeller: in 5 of 56 buyers of
27/11/2026 it was nowhere in the address), never over one the buyer wrote. Read here, in memory
(about 20 MB); no CEP of a buyer leaves the ERP. Built by docs/cep-bairros (scripts there).
"""

import bisect
import gzip
import re
import threading
from array import array
from pathlib import Path

PATH = Path(__file__).with_name("cep_bairros.tsv.gz")
_LOCK = threading.Lock()
_TABLE = {}


def _load(path):
    ceps, cities, places, names, index = array("I"), array("I"), array("I"), [], {}
    with gzip.open(path, "rt", encoding="utf-8") as source:
        for line in source:
            cep, city, name = line.rstrip("\n").split("\t")
            ceps.append(int(cep))
            cities.append(int(city))
            if name not in index:
                index[name] = len(names)
                names.append(name)
            places.append(index[name])
    return ceps, cities, places, names


def lookup(cep, path=None):
    """(neighborhood, IBGE city code) of a CEP, or None when the register has no clear one."""
    path = Path(path or PATH)
    digits = re.sub(r"\D", "", str(cep or ""))
    if len(digits) != 8 or not path.exists():
        return None
    with _LOCK:
        if path not in _TABLE:
            _TABLE.clear()
            _TABLE[path] = _load(path)
        ceps, cities, places, names = _TABLE[path]
    key = int(digits)
    at = bisect.bisect_left(ceps, key)
    if at < len(ceps) and ceps[at] == key:
        return names[places[at]], f"{cities[at]:07d}"
    return None
