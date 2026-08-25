"""Normaliseringshjelpere for Stortingets datakilder.

Her ligger de tre tingene som er lette å gjøre feil, og som den gamle
løsningen gjorde feil:

1. Datoer. Stortingets API returnerer .NET-serialiserte datoer.
2. Dokumenttype. Feltet `type` er et heltall som ikke betyr det man tror.
3. Deduplisering av RSS, som mangler både guid og unik lenke.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timedelta, timezone

# ── Datoer ───────────────────────────────────────────────────────────────
# Stortingets API: "/Date(1787223052180+0200)/"
_DOTNET_DATO = re.compile(r"/Date\((-?\d+)([+-]\d{4})?\)/")


def parse_dato(verdi: str | None) -> datetime | None:
    """Parse .NET-datoen Stortingets API bruker.

    >>> parse_dato("/Date(1787223052180+0200)/").year
    2026
    >>> parse_dato(None) is None
    True
    """
    if not verdi:
        return None
    m = _DOTNET_DATO.fullmatch(verdi.strip())
    if not m:
        # Noen felter er allerede ISO-formatert.
        try:
            return datetime.fromisoformat(verdi.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
    millis = int(m.group(1))
    dt = datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
    offset = m.group(2)
    if offset:
        tegn = 1 if offset[0] == "+" else -1
        delta = timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5]))
        dt = dt.astimezone(timezone(tegn * delta))
    return dt


# ── Dokumenttype ─────────────────────────────────────────────────────────
# Feltet `henvisning` er fasit: "Meld. St. 13 (2025-2026)" sier presist hva
# dokumentet er. Enum-feltene brukes bare som reserve.
#
# Den gamle løsningen leste `type` (1/2/3) som om det var dokumenttype. Det er
# det ikke — det er saksklasse (budsjett/alminnelig/lov). Resultatet var at
# "Innst. 3 S Skatte-, avgifts- og tollinntekter" ble merket "Sakstype:
# Spørsmål" i varselet.

_HENVISNING_TYPER: tuple[tuple[str, str], ...] = (
    ("meld. st.", "Melding"),
    ("prop.", "Proposisjon"),
    ("innst.", "Innstilling"),
    ("dokument 8", "Representantforslag"),
    ("dokument 12", "Grunnlovsforslag"),
    ("dokument 3", "Riksrevisjonen"),
    ("dokument", "Dokumentserien"),
    ("innberetning", "Innberetning"),
)

# Reserve når `henvisning` er tom. Utledet empirisk mot sesjon 2025-2026 ved
# å krysse dokumentgruppe mot henvisning-prefiks.
_DOKUMENTGRUPPE: dict[int, str] = {
    1: "Proposisjon",
    2: "Melding",
    3: "Redegjørelse",
    4: "Representantforslag",
    5: "Grunnlovsforslag",
    6: "Riksrevisjonen",
    7: "Innstilling",
    8: "Innberetning",
}

# `type` på sak = saksklasse, ikke dokumenttype.
SAKSKLASSE: dict[int, str] = {
    1: "Budsjettsak",
    2: "Alminnelig sak",
    3: "Lovsak",
}

SAKSSTATUS: dict[int, str] = {
    1: "Til behandling",
    2: "Trukket",
    3: "Behandlet",
    4: "Bortfalt",
}


def utled_dokumenttype(henvisning: str | None, dokumentgruppe: int | None = None) -> str:
    """Utled dokumenttype, primært fra `henvisning`.

    >>> utled_dokumenttype("Meld. St. 13 (2025-2026)")
    'Melding'
    >>> utled_dokumenttype("Innst. 66 S (2025-2026)")
    'Innstilling'
    >>> utled_dokumenttype("", 2)
    'Melding'
    >>> utled_dokumenttype("")
    ''
    """
    h = (henvisning or "").strip().lower()
    for prefiks, navn in _HENVISNING_TYPER:
        if h.startswith(prefiks):
            return navn
    if dokumentgruppe is not None:
        return _DOKUMENTGRUPPE.get(dokumentgruppe, "")
    return ""


# ── Lenker ───────────────────────────────────────────────────────────────
_BASE = "https://www.stortinget.no/no/Saker-og-publikasjoner"


def sak_url(sak_id: str | int) -> str:
    return f"{_BASE}/Saker/Sak/?p={sak_id}"


def skriftlig_sporsmal_url(sporsmal_id: str | int) -> str:
    return f"{_BASE}/Sporsmal/Skriftlige-sporsmal-og-svar/?qid={sporsmal_id}"


def sporretime_url(sporsmal_id: str | int) -> str:
    return f"{_BASE}/Sporsmal/Sporretimesporsmal/?qid={sporsmal_id}"


def interpellasjon_url(sporsmal_id: str | int) -> str:
    return f"{_BASE}/Sporsmal/Interpellasjoner/?qid={sporsmal_id}"


# ── Tekst ────────────────────────────────────────────────────────────────
_FLERE_MELLOMROM = re.compile(r"\s+")


def rydd_tekst(tekst: str | None) -> str:
    """Fjern HTML-rester, normaliser unicode og klem sammen mellomrom."""
    if not tekst:
        return ""
    t = unicodedata.normalize("NFC", tekst)
    t = re.sub(r"<[^>]+>", " ", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return _FLERE_MELLOMROM.sub(" ", t).strip()


def syntetisk_id(*deler: str) -> str:
    """Stabil ID for kilder uten egen ID (RSS).

    Stortingets RSS-feeder har tom <guid>, og <link> peker til samme
    oversiktsside for alle poster. Uten dette ville hver kjøring sett hver
    post som ny.

    >>> syntetisk_id("Tittel", "2026-08-20") == syntetisk_id("Tittel", "2026-08-20")
    True
    >>> syntetisk_id("A") == syntetisk_id("B")
    False
    """
    grunnlag = "|".join(rydd_tekst(d).lower() for d in deler)
    return hashlib.sha256(grunnlag.encode("utf-8")).hexdigest()[:32]
