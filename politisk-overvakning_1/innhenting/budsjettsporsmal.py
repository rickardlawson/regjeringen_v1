"""Svar på budsjettspørsmål fra regjeringen.no.

Partienes budsjettspørsmål går fra finanskomiteens fraksjoner til
Finansdepartementet og besvares der. De er ikke med i data.stortinget.no og
heller ikke i regjeringen.no sin RSS. Søkesiden på regjeringen.no laster dem
fra et åpent JSON-endepunkt, og det er det vi bruker:

    /api/BudsjettSporsmalApi/GetBudsjettSporsmal
        ?filterRootPageId=<søkesidens id>&language=no&pagesize=100
        &bq=&sort=lastpublished&page=<n>

Hvert element har en stabil ID (f.eks. «ID3180040»), spørsmålsnummer, parti,
en kort tematittel (Subtitle) og fulltekst av spørsmål og svar som HTML.
Ingen PDF-uttrekk eller skraping trengs.

Fallgruve: enkeltsvarene har INGEN egen side. ID-en redirecter til en URL som
gir 404. Svarene vises bare som utvidbare elementer på søkesiden. Lenken peker
derfor til søkesiden filtrert på spørsmålsnummeret (`?bq=381`), som gir en
kort liste med riktig svar øverst.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

import requests

from .kilder import BUDSJETT_KILDER, BudsjettKilde
from .modell import Dokument
from .normalisering import rydd_tekst

logger = logging.getLogger(__name__)

_API = "https://www.regjeringen.no/api/BudsjettSporsmalApi/GetBudsjettSporsmal"
_TIMEOUT = 45
_BRUKERAGENT = "Uppercase-PolitiskOvervakning/1.0 (+https://uppercase.no)"
_PER_SIDE = 100
# 2027-budsjettet hadde 499 svar etter én uke. Tidligere år har landet på
# noen tusen. Grensen er et sikkerhetsnett mot en API-endring som gir
# uendelig paginering, ikke en reell begrensning.
_MAKS_SIDER = 60

# «Svar på spørsmål 381 fra Finanskomiteen/Hs fraksjon av 13.10.2026»
_AVSENDER = re.compile(r"\bfra\s+(.+?)\s+av\s+\d", re.IGNORECASE)
# «Spørsmålet er forelagt Kunnskapsdepartementet, som har gitt følgende svar»
_FORELAGT = re.compile(r"forelagt\s+([A-ZÆØÅ][\wæøåÆØÅ-]*departementet)")


class BudsjettFeil(RuntimeError):
    """Endepunktet svarte ikke, eller svarte med noe vi ikke forstår."""


def _hent_side(kilde: BudsjettKilde, side: int) -> dict:
    params = {
        "filterRootPageId": kilde.rotside_id,
        "language": "no",
        "pagesize": _PER_SIDE,
        "bq": "",
        "sort": "lastpublished",
        "page": side,
    }
    try:
        resp = requests.get(
            _API, params=params, timeout=_TIMEOUT,
            headers={"User-Agent": _BRUKERAGENT, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise BudsjettFeil(f"{kilde.navn} side {side}: {exc}") from exc

    if data.get("Success") != "OK" or not isinstance(data.get("Result"), dict):
        raise BudsjettFeil(f"{kilde.navn} side {side}: uventet svar {str(data)[:200]}")
    return data["Result"]


def _parse_dato(verdi: str | None) -> datetime | None:
    if not verdi:
        return None
    try:
        return datetime.fromisoformat(verdi)
    except ValueError:
        logger.debug("Ukjent datoformat: %r", verdi)
        return None


def normaliser(post: dict, kilde: BudsjettKilde) -> Dokument | None:
    """Ett element fra API-et til et Dokument. None hvis det mangler ID."""
    kilde_id = str(post.get("ID") or "").strip()
    if not kilde_id:
        return None

    navn = rydd_tekst(post.get("Name"))
    tema = rydd_tekst(post.get("Subtitle"))
    nummer = post.get("QuestionNumber")
    parti = rydd_tekst(post.get("Party"))
    sporsmal = rydd_tekst(post.get("Question"))
    svar = rydd_tekst(post.get("Response"))

    # Tittelen vektes høyest i søket (A). «Svar på spørsmål 381 fra …» sier
    # ingenting om innholdet, så temaet må stå der.
    tittel = f"Budsjettspørsmål {nummer} ({parti}): {tema}" if tema else navn

    m = _AVSENDER.search(navn)
    avsender = m.group(1).strip() if m else ""
    m = _FORELAGT.search(svar)
    besvart_av = m.group(1) if m else "Finansdepartementet"

    deler = []
    if sporsmal:
        deler.append(f"Spørsmål: {sporsmal}")
    if svar:
        deler.append(f"Svar: {svar}")

    return Dokument(
        kilde=kilde.navn,
        kilde_id=kilde_id,
        kildenavn=kilde.kildenavn,
        tittel=tittel or kilde_id,
        dokumenttype="Svar på budsjettspørsmål",
        sammendrag=" ".join(deler),
        url=f"{kilde.side_url}?bq={nummer}" if nummer else kilde.side_url,
        publisert=_parse_dato(post.get("SortByPublished")),
        avsender=avsender,
        parti=parti,
        mottaker="Finansdepartementet",
        besvart_av=besvart_av,
        komite="Finanskomiteen",
        status="Besvart",
        # Navnet («Svar på spørsmål 381 fra Finanskomiteen/Hs fraksjon av
        # 13.10.2026») gjør spørsmålsnummer og fraksjon søkbare.
        henvisning=navn,
        rådata={
            "_kilde": "regjeringen_budsjett_api",
            "_budsjettaar": kilde.aar,
            "sporsmalsnummer": nummer,
            "publisert_tekst": post.get("Published"),
        },
    )


def hent_kilde(kilde: BudsjettKilde) -> list[Dokument]:
    """Hent alle svar for ett budsjettår. Feiler hele kilden hvis én side feiler.

    Et halvt datasett er verre enn ingen: det ser ut som en vellykket kjøring.
    """
    dokumenter: list[Dokument] = []
    sett: set[str] = set()
    uten_id = 0
    side = 1
    totalt_sider = 1

    while side <= totalt_sider:
        if side > _MAKS_SIDER:
            raise BudsjettFeil(
                f"{kilde.navn}: over {_MAKS_SIDER} sider — har API-et endret seg?"
            )
        resultat = _hent_side(kilde, side)
        totalt_sider = int(resultat.get("TotalPages") or 0)
        for post in resultat.get("List") or []:
            dok = normaliser(post, kilde)
            if dok is None:
                uten_id += 1
                continue
            if dok.kilde_id in sett:
                # Sortering på publiseringstid er ustabil ved like tidsstempler,
                # så et svar kan dukke opp på to sider.
                continue
            sett.add(dok.kilde_id)
            dokumenter.append(dok)
        side += 1

    if uten_id:
        logger.warning("%s: %d svar uten ID ble hoppet over", kilde.navn, uten_id)
    logger.info("%s: %d svar", kilde.navn, len(dokumenter))
    return dokumenter


def hent_alle(kilder=BUDSJETT_KILDER) -> list[Dokument]:
    alle: list[Dokument] = []
    for kilde in kilder:
        try:
            alle.extend(hent_kilde(kilde))
        except BudsjettFeil as exc:
            logger.error("Hopper over %s: %s", kilde.navn, exc)
    return alle
