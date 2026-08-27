"""Diff: hva er nytt siden sist?

Dette er kjernen i en overvåkningstjeneste. Alt annet kan gjøres om igjen —
men sender du et varsel to ganger, eller går glipp av ett, merker brukeren det
med én gang.

Regler:
  - Nytt = (kilde, kilde_id) vi ikke har sett før.
  - Endret = kjent nøkkel, men innholdet er endret (f.eks. spørsmål besvart).
  - Varsel sendes for nye. Endrede lagres, men utløser ikke nytt varsel med
    mindre statusen faktisk har endret seg meningsfullt.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from .modell import Dokument
from .normalisering import rydd_tekst

logger = logging.getLogger(__name__)


def innholdshash(dok: Dokument) -> str:
    """Hash av de feltene som betyr noe for om dokumentet er 'endret'.

    Med vilje utelatt: `publisert` fra saker-APIet, som er
    `sist_oppdatert_dato` og endrer seg av administrative årsaker uten at
    innholdet er nytt for en leser.
    """
    deler = [
        dok.tittel,
        dok.sammendrag,
        dok.status,
        dok.besvart_av,
        dok.dokumenttype,
        dok.henvisning,
    ]
    grunnlag = "|".join(rydd_tekst(d).lower() for d in deler)
    return hashlib.sha256(grunnlag.encode("utf-8")).hexdigest()[:32]


@dataclass(slots=True)
class Diff:
    nye: list[Dokument] = field(default_factory=list)
    endrede: list[Dokument] = field(default_factory=list)
    uendrede: int = 0
    forsvunne: int = 0
    """Dokumenter fra API-kilder som ikke lenger finnes i kilden.

    Telles kun for kilder med ekte ID-er. Slike ID-er skal aldri forsvinne, så
    et tall over null betyr at noe er galt — typisk at ID-er har skiftet.
    Det var nøyaktig det som skjedde 27.08.2026: 262 forsvant og 262 dukket
    opp som «nye» i samme kjøring, fordi RSS-høringene hadde hashede ID-er.

    Rullerende RSS-feeder telles ikke med. «Aktuelt» viser bare de siste
    sakene, så eldre poster forsvinner naturlig ut av feeden.
    """

    @property
    def antall_varslbare(self) -> int:
        return len(self.nye)

    def __str__(self) -> str:
        tekst = (
            f"{len(self.nye)} nye, {len(self.endrede)} endrede, "
            f"{self.uendrede} uendrede"
        )
        if self.forsvunne:
            tekst += f", {self.forsvunne} forsvunne"
        return tekst


def finn_nye(
    hentede: list[Dokument],
    kjente: dict[tuple[str, str], str],
) -> Diff:
    """Sammenlign hentede dokumenter mot det vi allerede har lagret.

    `kjente` er {(kilde, kilde_id): innholdshash} fra databasen.

    Dubletter innenfor samme kjøring fjernes også — Stortingets API kan
    returnere samme sak i flere lister.
    """
    diff = Diff()
    sett_denne_runden: set[tuple[str, str]] = set()

    for dok in hentede:
        nokkel = dok.nokkel
        if nokkel in sett_denne_runden:
            continue  # dublett innenfor samme kjøring
        sett_denne_runden.add(nokkel)

        ny_hash = innholdshash(dok)
        gammel_hash = kjente.get(nokkel)

        if gammel_hash is None:
            diff.nye.append(dok)
        elif gammel_hash != ny_hash:
            diff.endrede.append(dok)
        else:
            diff.uendrede += 1

    # Foreldreløse telles KUN for kilder som leverer et komplett datasett,
    # altså API-kildene. En rullerende RSS-feed som «Aktuelt» viser bare de
    # siste sakene, så eldre poster forsvinner naturlig ut av feeden mens de
    # blir stående i basen. Å telle dem som forsvunne ville gitt et tall som
    # vokser for evig og drukner det signalet vi faktisk er ute etter.
    komplette_kilder = {
        d.kilde for d in hentede if not d.id_er_syntetisk
    } - {d.kilde for d in hentede if d.id_er_syntetisk}

    sett_i_komplette = sum(
        1 for n in sett_denne_runden if n[0] in komplette_kilder
    )
    kjente_i_komplette = sum(1 for (kilde, _) in kjente if kilde in komplette_kilder)
    diff.forsvunne = max(0, kjente_i_komplette - sett_i_komplette)

    logger.info("Diff: %s", diff)

    if diff.forsvunne > 0:
        logger.warning(
            "%d dokumenter fra API-kilder finnes ikke lenger. Ekte ID-er skal "
            "ikke forsvinne — dette tyder på at ID-er har skiftet, ikke at "
            "innhold er slettet. %d dukket opp som nye i samme kjøring. "
            "Varslingslaget bør ikke sende ut denne runden uten kontroll.",
            diff.forsvunne, len(diff.nye),
        )

    return diff


def forste_gangs_kjoring(diff: Diff, grense: int = 500) -> bool:
    """Er dette sannsynligvis en kaldstart?

    Ved første kjøring mot tom database er ALT nytt. Sender man varsler da,
    får hver bruker tusenvis av e-poster. Den gamle løsningen håndterte dette
    med en «velkomstmail» som viste et utvalg — samme mekanisme bør beholdes.
    """
    return len(diff.nye) > grense
