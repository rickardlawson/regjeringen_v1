"""Normalisert dokumentmodell — felles form for alle kilder.

Hele poenget med dette laget er at matchings- og varslingslaget aldri skal
trenge å vite om et dokument kom fra Stortingets API, en RSS-feed eller
regjeringen.no. Alt normaliseres hit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Dokument:
    """Ett politisk dokument, normalisert.

    Unik nøkkel er (kilde, kilde_id). For API-kilder er kilde_id en ekte,
    stabil ID fra Stortinget. For RSS-kilder finnes ingen ID — der syntetiseres
    en hash, og `id_er_syntetisk` settes slik at nedstrøms logikk vet at
    dedupliseringen er svakere.
    """

    kilde: str
    """Maskinnavn på kilden, f.eks. 'stortinget_sak'."""

    kilde_id: str
    """Stabil ID innenfor kilden. Sammen med `kilde` er dette unik nøkkel."""

    tittel: str

    kildenavn: str = ""
    """Menneskelesbart kildenavn til visning, f.eks. 'Stortinget: Saker (API)'."""

    dokumenttype: str = ""
    """Utledet type: 'Melding', 'Proposisjon', 'Representantforslag', ..."""

    sammendrag: str = ""
    """Brødtekst/ingress. Går inn i søketeksten."""

    url: str = ""

    publisert: datetime | None = None

    # ── Politisk kontekst ────────────────────────────────────────────────
    avsender: str = ""
    """Hvem som fremmet saken eller stilte spørsmålet."""

    parti: str = ""

    mottaker: str = ""
    """Hvem spørsmålet er stilt til, typisk en statsrådstittel."""

    besvart_av: str = ""

    komite: str = ""

    status: str = ""

    henvisning: str = ""
    """Offisiell referanse, f.eks. 'Meld. St. 13 (2025-2026)'."""

    # ── Matching ─────────────────────────────────────────────────────────
    emner: list[str] = field(default_factory=list)
    """Stortingets emnetagger.

    Holdes BEVISST utenfor `sok_tekst`. Den gamle løsningen søkte i emnelisten
    sammen med resten, og da traff stikkordet 'havbruk' på Statsbudsjettet 2026
    fordi emnelisten inneholdt 'Fiskerier'. Emner er nyttige, men som et eget
    og svakere signal enn tittel og brødtekst.
    """

    id_er_syntetisk: bool = False
    """True når kilde_id er en hash fordi kilden mangler stabil ID (RSS)."""

    rådata: dict[str, Any] = field(default_factory=dict)
    """Ubearbeidet post fra kilden. Gjør det mulig å re-normalisere senere
    uten å hente alt på nytt."""

    @property
    def sok_tekst(self) -> str:
        """Primær tekst for stikkordsmatching: tittel, sammendrag, referanse."""
        deler = [self.tittel, self.sammendrag, self.henvisning]
        return " ".join(d for d in deler if d).strip()

    @property
    def nokkel(self) -> tuple[str, str]:
        return (self.kilde, self.kilde_id)

    def __post_init__(self) -> None:
        if not self.kilde:
            raise ValueError("kilde må være satt")
        if not self.kilde_id:
            raise ValueError("kilde_id må være satt")
