"""Fallback for skriftlige spørsmål: listesiden på stortinget.no.

data.stortinget.no kan ligge flere dager etter nettsiden (sept. 2026: ~100
spørsmål / 6 dager bak). Listesiden har en innebygd JSON-blob per spørsmål,
så vi slipper å skrape HTML eller hente hver detaljside.

qnid på nettsiden == id i API-et. Dokumentene får samme `kilde` og
`kilde_id` som API-versjonen, så når API-et tar igjen etterslepet,
overskrives web-versjonen av den fyldigere API-versjonen i samme rad.
"""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .modell import Dokument
from .normalisering import rydd_tekst, skriftlig_sporsmal_url

logger = logging.getLogger(__name__)

LISTE_URL = (
    "https://www.stortinget.no/no/Saker-og-publikasjoner/Sporsmal/"
    "Skriftlige-sporsmal-og-svar/"
)
_TIMEOUT = 30
_BRUKERAGENT = "Uppercase-PolitiskOvervakning/1.0 (+https://uppercase.no)"
_OSLO = ZoneInfo("Europe/Oslo")

# Ett JSON-objekt per spørsmål på listesiden.
_ITEM_RE = re.compile(
    r'\{"questionPageLink":\{.*?"presentationQuestionTitleText":".*?(?<!\\)"\}', re.S
)
# "Skriftlig spørsmål fra Grunde Almeland (V) til barne- og familieministeren"
_OVERSKRIFT_RE = re.compile(r"fra (?P<navn>.+?) \((?P<parti>[^)]+)\) til (?P<til>.+)$")

# API-et bruker fullt partinavn; nettsiden bruker forkortelse.
_PARTI = {
    "A": "Arbeiderpartiet", "H": "Høyre", "FrP": "Fremskrittspartiet",
    "Sp": "Senterpartiet", "SV": "Sosialistisk Venstreparti", "R": "Rødt",
    "V": "Venstre", "MDG": "Miljøpartiet De Grønne",
    "KrF": "Kristelig Folkeparti", "PF": "Pasientfokus",
}


def _dato(tekst: str | None) -> datetime | None:
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", tekst or "")
    if not m:
        return None
    d, mnd, aar = (int(x) for x in m.groups())
    return datetime(aar, mnd, d, tzinfo=_OSLO)


def _normaliser(post: dict) -> Dokument | None:
    href = (post.get("questionPageLink") or {}).get("href", "")
    m = re.search(r"qnid=(\d+)", href)
    tittel = rydd_tekst(html.unescape(post.get("presentationQuestionTitleText") or ""))
    if not m or not tittel:
        return None
    qnid = m.group(1)

    overskrift = html.unescape((post.get("questionPageLink") or {}).get("text", ""))
    o = _OVERSKRIFT_RE.search(overskrift)
    avsender = rydd_tekst(o.group("navn")) if o else ""
    parti = _PARTI.get(o.group("parti"), o.group("parti")) if o else ""
    mottaker = rydd_tekst(o.group("til")) if o else ""

    besvart = "besvart" in (post.get("presentationAnsweredText") or "").lower()

    return Dokument(
        kilde="stortinget_skriftlig_sporsmal",
        kilde_id=qnid,
        kildenavn="Stortinget: Skriftlige spørsmål",
        tittel=tittel,
        dokumenttype="Skriftlig spørsmål",
        url=skriftlig_sporsmal_url(qnid),
        publisert=(
            _dato(post.get("presentationSentText"))
            or _dato(post.get("presentationDatedText"))
        ),
        avsender=avsender,
        parti=parti,
        mottaker=mottaker,
        status="Besvart" if besvart else "Til behandling",
        emner=[],
        rådata={**post, "_kilde": "stortinget_web"},
    )


def _hent_side(side: int) -> list[Dokument]:
    resp = requests.get(
        LISTE_URL, params={"page": side},
        timeout=_TIMEOUT, headers={"User-Agent": _BRUKERAGENT},
    )
    resp.raise_for_status()
    ut: list[Dokument] = []
    for raw in _ITEM_RE.findall(resp.text):
        try:
            dok = _normaliser(json.loads(raw))
        except Exception as exc:  # én råtten post skal ikke velte kjøringen
            logger.warning("web-fallback: hoppet over post: %s", exc)
            continue
        if dok:
            ut.append(dok)
    return ut


def hent_manglende(api_ider: set[str], maks_sider: int = 10) -> list[Dokument]:
    """Spørsmål på stortinget.no som ikke er med i API-svaret.

    Blar fra nyeste og stopper når en hel side kun har id-er API-et kjenner.
    """
    nye: list[Dokument] = []
    sett: set[str] = set()
    for side in range(1, maks_sider + 1):
        dokumenter = _hent_side(side)
        if not dokumenter:
            if side == 1:
                # Sannsynligvis endret sideoppsett — si fra i stedet for å tie.
                raise RuntimeError("Fant ingen spørsmål på listesiden (endret oppsett?)")
            break
        ukjente = [
            d for d in dokumenter
            if d.kilde_id not in api_ider and d.kilde_id not in sett
        ]
        for d in ukjente:
            sett.add(d.kilde_id)
        nye.extend(ukjente)
        if not ukjente:
            break
    return nye
