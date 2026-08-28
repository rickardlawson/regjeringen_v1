"""Utsending av varsler.

Kjøres som en egen cron-jobb etter innhentingen.

Duplikatvern i to uavhengige lag:

  1. Innhentingen avgjør hva som er NYTT (diff mot forrige kjøring).
  2. `varsel_sendt` avgjør hva som er SENDT.

Den gamle løsningen hadde bare det første, og sendte «Statsbudsjettet 2026»
tre ganger i én e-post. Da RSS-feeden byttet ID-ordning 27.08.2026 og 262
dokumenter så nye ut, ville lag to ha stoppet utsendingen helt på egen hånd.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from db import brukere
from matching.sporring import TomtSok, beskriv
from varsling import epost as epostmodul
from varsling import maler

logger = logging.getLogger(__name__)

# Tak per e-post. Et abonnement på «energi» treffer nesten hundre saker; ingen
# leser en e-post med hundre kort. Resten ligger i arkivet.
MAKS_PER_VARSEL = 25
MAKS_I_VELKOMST = 15

# Hvor mange treff velkomstmailen kvitterer ut, uavhengig av hvor mange den
# viser. Alt som finnes ved opprettelse regnes som "allerede sett".
VELKOMST_KVITTERINGSTAK = 5000


@dataclass(slots=True)
class Resultat:
    sendt: int = 0
    velkomster: int = 0
    hoppet_over: int = 0
    feilet: int = 0
    detaljer: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"{self.sendt} varsler, {self.velkomster} velkomstmailer, "
            f"{self.hoppet_over} uten nye treff, {self.feilet} feilet"
        )


def _basis_url() -> str:
    return os.environ.get("BASIS_URL", "").rstrip("/")


def behandle_abonnement(ab: dict, tørrkjør: bool = False) -> str:
    """Behandle ett abonnement. Returnerer 'sendt', 'velkomst', 'tom' eller 'feil'."""
    ab_id, stikkord, mottaker = ab["id"], ab["stikkord"], ab["epost"]
    er_velkomst = ab.get("velkomst_sendt") is None

    try:
        grense = MAKS_I_VELKOMST if er_velkomst else MAKS_PER_VARSEL
        treff = brukere.usendte_treff(ab_id, stikkord, grense=grense)
    except TomtSok as exc:
        logger.error("Abonnement %s har ugyldig stikkord %r: %s", ab_id, stikkord, exc)
        return "feil"

    if not treff:
        if er_velkomst and not tørrkjør:
            # Ingen treff ennå, men abonnementet er registrert. Marker
            # velkomsten som sendt så brukeren ikke får en tom e-post nå og
            # en «velkomst» om tre uker.
            brukere.marker_velkomst_sendt(ab_id)
        return "tom"

    vist = beskriv(stikkord)
    if er_velkomst:
        # Velkomstmailen VISER et utvalg, men kvitterer ut ALT som matcher.
        #
        # Uten dette ville de treffene som ikke fikk plass i e-posten blitt
        # liggende som «usendte», og gått ut som løpende varsler i de neste
        # kjøringene. Et nytt abonnement på «Havbruk OR Fiskeri» ville da gitt
        # tre e-poster på rad: velkomst med 15, så 25 «nye», så 18 «nye» —
        # alle med saker fra i vår.
        #
        # Semantikken skal være: «her er det som finnes nå, resten ligger i
        # arkivet. Fremover hører du bare om det som faktisk er nytt.»
        alle = brukere.usendte_treff(ab_id, stikkord, grense=VELKOMST_KVITTERINGSTAK)
        emne, html, tekst = maler.velkomst(vist, treff, len(alle), _basis_url())
        kvitteres = [d["id"] for d in alle]
        type_ = "velkomst"
    else:
        emne, html, tekst = maler.varsel(vist, treff, _basis_url())
        kvitteres = [d["id"] for d in treff]
        type_ = "varsel"

    if tørrkjør:
        logger.info("[tørrkjøring] %s -> %s: %d treff (%s)",
                    mottaker, stikkord, len(treff), type_)
        return "velkomst" if er_velkomst else "sendt"

    try:
        epostmodul.send(epostmodul.Epost(til=mottaker, emne=emne, html=html, tekst=tekst))
    except epostmodul.EpostFeil as exc:
        logger.error("Utsending til %s feilet: %s", mottaker, exc)
        brukere.logg_utsending(ab_id, mottaker, emne, len(treff), type_,
                               status="feilet", feil=str(exc))
        return "feil"

    # Markeres FØRST etter vellykket sending. Feiler sendingen, prøver vi på
    # nytt neste runde i stedet for å tape varselet i stillhet.
    brukere.marker_sendt(ab_id, kvitteres)
    if er_velkomst:
        brukere.marker_velkomst_sendt(ab_id)
    brukere.logg_utsending(ab_id, mottaker, emne, len(treff), type_)
    logger.info("Sendte %s til %s: %d treff for %r",
                type_, mottaker, len(treff), stikkord)
    return "velkomst" if er_velkomst else "sendt"


def send_alle(tørrkjør: bool = False) -> Resultat:
    res = Resultat()
    abonnementer = brukere.aktive_abonnement()
    logger.info("Behandler %d aktive abonnement.", len(abonnementer))

    for ab in abonnementer:
        try:
            utfall = behandle_abonnement(ab, tørrkjør)
        except Exception as exc:
            logger.exception("Uventet feil for abonnement %s: %s", ab["id"], exc)
            res.feilet += 1
            continue
        if utfall == "sendt":
            res.sendt += 1
        elif utfall == "velkomst":
            res.velkomster += 1
        elif utfall == "tom":
            res.hoppet_over += 1
        else:
            res.feilet += 1

    logger.info("Utsending ferdig: %s", res)
    return res
