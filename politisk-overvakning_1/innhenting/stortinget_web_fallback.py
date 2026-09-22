"""
Fallback for skriftlige spørsmål: henter fra listesiden på stortinget.no
når data.stortinget.no ligger etter.

Listesiden har en innebygd JSON-blob per spørsmål (questionPageLink,
presentationDatedText osv.), så vi slipper å hente hver detaljside.
qnid på nettsiden == id i API-et, så dedup skjer på samme kilde_id.
"""
import html
import json
import re
from datetime import datetime

import requests

LISTE_URL = "https://www.stortinget.no/no/Saker-og-publikasjoner/Sporsmal/Skriftlige-sporsmal-og-svar/"
BASE = "https://www.stortinget.no"
HEADERS = {"User-Agent": "politisk-overvakning (Uppercase AS)"}

# Ett objekt per spørsmål i den innebygde JSON-en
ITEM_RE = re.compile(r'\{"questionPageLink":\{.*?"presentationQuestionTitleText":".*?(?<!\\)"\}', re.S)


def _dato(tekst: str):
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})", tekst or "")
    return datetime.strptime(m.group(1), "%d.%m.%Y").date() if m else None


def hent_side(side: int) -> list[dict]:
    r = requests.get(LISTE_URL, params={"page": side}, headers=HEADERS, timeout=30)
    r.raise_for_status()
    ut = []
    for raw in ITEM_RE.findall(r.text):
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        href = d["questionPageLink"]["href"]
        qnid = int(re.search(r"qnid=(\d+)", href).group(1))
        ut.append({
            "kilde_id": qnid,
            "type": "skriftlig_sporsmal",
            "overskrift": html.unescape(d["questionPageLink"]["text"]),
            "tekst": html.unescape(d.get("presentationQuestionTitleText", "")).strip(),
            "dokumentnr": d.get("presentationDocumentTitleText"),
            "dato": _dato(d.get("presentationDatedText")),
            "status": d.get("presentationAnsweredText"),
            "url": f"{BASE}/no/Saker-og-publikasjoner/Sporsmal/Skriftlige-sporsmal-og-svar/Skriftlig-sporsmal/?qnid={qnid}",
            "kilde": "stortinget_web",
        })
    return ut


def hent_nye(kjente_ider: set[int], maks_sider: int = 10) -> list[dict]:
    """Blar fra nyeste og stopper når en hel side kun har kjente id-er."""
    nye = []
    for side in range(1, maks_sider + 1):
        items = hent_side(side)
        if not items:
            break
        ukjente = [i for i in items if i["kilde_id"] not in kjente_ider]
        nye.extend(ukjente)
        if not ukjente:
            break
    return nye


if __name__ == "__main__":
    # Rask test: hva ligger på nettsiden som ikke er i API-et?
    api = requests.get(
        "https://data.stortinget.no/eksport/skriftligesporsmal",
        params={"sesjonid": "2025-2026", "format": "json"}, timeout=60,
    ).json()
    api_ider = {int(x["id"]) for x in api["sporsmal_liste"]}
    nye = hent_nye(api_ider)
    print(f"{len(nye)} spørsmål på stortinget.no som mangler i API-et")
    for n in nye[:5]:
        print(n["kilde_id"], n["dato"], n["overskrift"][:70])
