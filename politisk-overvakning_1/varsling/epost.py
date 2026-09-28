"""E-postutsending.

To leverandører:

  resend  — produksjon. Krever RESEND_API_KEY.
  konsoll — skriver e-posten til loggen i stedet for å sende.

Konsollmodus er ikke bare for testing. Da dette ble bygget hadde vi ikke
tilgang til Resend-kontoen som DNS-postene for oppdatert.firsthouse.no peker
mot, fordi personen som satte den opp hadde sluttet. Hele varslingslaget kan
bygges, testes og gjennomgås i konsollmodus, og så byttes med én miljøvariabel
når kontoen er på plass.
"""
from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import requests

from varsling.maler import LOGO_CID, LOGO_FIL

logger = logging.getLogger(__name__)

RESEND_API = "https://api.resend.com/emails"
STANDARD_AVSENDER = "Politisk overvåkning <hello@oppdatert.firsthouse.no>"


class EpostFeil(RuntimeError):
    """E-posten kunne ikke sendes."""


@dataclass(slots=True)
class Epost:
    til: str
    emne: str
    html: str
    tekst: str = ""


def _avsender() -> str:
    return os.environ.get("EPOST_AVSENDER", STANDARD_AVSENDER)


@lru_cache(maxsize=1)
def _logo_base64() -> str | None:
    """Logoen som base64, lest én gang per prosess.

    Mangler filen, sendes e-posten likevel. Da viser e-postklienten
    alt-teksten «FIRST HOUSE» i stedet for bildet — ikke pent, men bedre enn
    at ingen varsler går ut.
    """
    fil = Path(__file__).parent / LOGO_FIL
    try:
        return base64.b64encode(fil.read_bytes()).decode("ascii")
    except OSError as exc:
        logger.warning("Fant ikke logoen %s: %s — sender uten.", fil, exc)
        return None


def _vedlegg(epost: Epost) -> list[dict]:
    """Innebygde bilder HTML-en refererer til med cid:."""
    if f"cid:{LOGO_CID}" not in epost.html:
        return []
    innhold = _logo_base64()
    if not innhold:
        return []
    return [{
        "filename": LOGO_FIL,
        "content": innhold,
        "content_type": "image/png",
        "content_id": LOGO_CID,
    }]


def _send_resend(epost: Epost) -> str:
    nokkel = os.environ.get("RESEND_API_KEY")
    if not nokkel:
        raise EpostFeil("RESEND_API_KEY mangler")
    vedlegg = _vedlegg(epost)
    try:
        svar = requests.post(
            RESEND_API,
            timeout=30,
            headers={"Authorization": f"Bearer {nokkel}"},
            json={
                "from": _avsender(),
                "to": [epost.til],
                "subject": epost.emne,
                "html": epost.html,
                **({"text": epost.tekst} if epost.tekst else {}),
                **({"attachments": vedlegg} if vedlegg else {}),
            },
        )
    except requests.RequestException as exc:
        raise EpostFeil(f"Kunne ikke nå Resend: {exc}") from exc

    if svar.status_code >= 400:
        raise EpostFeil(f"Resend svarte {svar.status_code}: {svar.text[:200]}")
    try:
        return svar.json().get("id", "")
    except ValueError:
        return ""


def _send_konsoll(epost: Epost) -> str:
    logger.info(
        "\n─── E-POST (konsollmodus, ikke sendt) ───\n"
        "Til:   %s\nEmne:  %s\n%s\n─────────────────────────────────────────",
        epost.til, epost.emne,
        (epost.tekst or epost.html)[:1200],
    )
    return "konsoll"


def send(epost: Epost) -> str:
    """Send én e-post. Returnerer leverandørens id.

    Leverandør velges av EPOST_LEVERANDOR. Standard er konsoll, slik at et
    feilkonfigurert miljø logger i stedet for å sende ut noe uventet.
    """
    leverandor = os.environ.get("EPOST_LEVERANDOR", "konsoll").lower()
    if leverandor == "resend":
        return _send_resend(epost)
    if leverandor == "konsoll":
        return _send_konsoll(epost)
    raise EpostFeil(f"Ukjent EPOST_LEVERANDOR: {leverandor!r}")


def er_konfigurert() -> bool:
    """Er utsending faktisk aktiv? Brukes av admin-visningen."""
    return (
        os.environ.get("EPOST_LEVERANDOR", "konsoll").lower() == "resend"
        and bool(os.environ.get("RESEND_API_KEY"))
    )
