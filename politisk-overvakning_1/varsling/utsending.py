"""Utsending av varsler.

Kjøres som en egen cron-jobb etter innhentingen.

Duplikatvern i to uavhengige lag:

  1. Innhentingen avgjør hva som er NYTT (diff mot forrige kjøring).
  2. `varsel_sendt` avgjør hva som er SENDT.

Den gamle løsningen hadde bare det første, og sendte «Statsbudsjettet 2026»
tre ganger i én e-post. Da RSS-feeden byttet ID-ordning 27.08.2026 og 262
dokumenter så nye ut, ville lag to ha stoppet utsendingen helt på egen hånd.

Svarvarsler følger samme mønster: innhentingen oppretter en `hendelse` når et
spørsmål blir besvart, og `hendelse_sendt` avgjør hva som er sendt.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import psycopg2.extras

from db import brukere, lager
from matching.sporring import TomtSok, beskriv
from varsling import epost as epostmodul
from varsling import maler

logger = logging.getLogger(__name__)

# Tak per e-post. Et abonnement på «energi» treffer nesten hundre saker; ingen
# leser en e-post med hundre kort. Resten ligger i arkivet.
MAKS_PER_VARSEL = 25
MAKS_I_VELKOMST = 15
MAKS_SVAR_PER_VARSEL = 25

# Hvor mange treff velkomstmailen kvitterer ut, uavhengig av hvor mange den
# viser. Alt som finnes ved opprettelse regnes som "allerede sett".
VELKOMST_KVITTERINGSTAK = 5000

# Svar eldre enn dette sendes ikke. Et vern mot at en feil i utsendingen,
# rettet etter tre uker, sender en bunke utdaterte svar på én gang.
SVAR_MAKS_ALDER_DAGER = 14


@dataclass(slots=True)
class Resultat:
    sendt: int = 0
    velkomster: int = 0
    svar: int = 0
    hoppet_over: int = 0
    feilet: int = 0
    detaljer: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"{self.sendt} varsler, {self.velkomster} velkomstmailer, "
            f"{self.svar} svarvarsler, {self.hoppet_over} uten nye treff, "
            f"{self.feilet} feilet"
        )


def _basis_url() -> str:
    return os.environ.get("BASIS_URL", "").rstrip("/")


# ── Svar på spørsmål ─────────────────────────────────────────────────────

def _usendte_svar(ab_id: int, grense: int) -> list[dict[str, Any]]:
    """Besvarte spørsmål dette abonnementet har fått varsel om, men ikke svar på.

    Mottakerne er de som fikk varsel om spørsmålet da det ble stilt
    (`varsel_sendt`). Svaret må ha kommet ETTER det varselet: da unngår vi at
    en ny abonnent får «svar mottatt» på spørsmål som allerede var besvart da
    de så dem i velkomstmailen.
    """
    with lager.kobling() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT h.id AS hendelse_id,
                       d.id, d.tittel, d.url, d.kildenavn, d.dokumenttype,
                       d.publisert, d.avsender, d.parti, d.mottaker,
                       d.besvart_av
                FROM hendelse h
                JOIN dokument d ON d.id = h.dokument_id
                JOIN varsel_sendt vs
                  ON vs.dokument_id = d.id AND vs.abonnement_id = %(ab)s
                WHERE h.type = 'svar_mottatt'
                  AND h.oppstod > vs.sendt
                  AND h.oppstod > NOW() - make_interval(days => %(dager)s)
                  AND NOT EXISTS (
                      SELECT 1 FROM hendelse_sendt hs
                      WHERE hs.abonnement_id = %(ab)s AND hs.hendelse_id = h.id
                  )
                ORDER BY h.oppstod DESC
                LIMIT %(grense)s
                """,
                {"ab": ab_id, "dager": SVAR_MAKS_ALDER_DAGER, "grense": grense},
            )
            return [dict(r) for r in cur.fetchall()]


def _marker_svar_sendt(ab_id: int, hendelse_ider: list[int]) -> None:
    if not hendelse_ider:
        return
    with lager.kobling() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO hendelse_sendt (abonnement_id, hendelse_id) "
                "VALUES %s ON CONFLICT DO NOTHING",
                [(ab_id, h) for h in hendelse_ider],
            )


def behandle_svar(ab: dict, tørrkjør: bool = False) -> str:
    """Send svarvarsel for ett abonnement. Returnerer 'svar', 'tom' eller 'feil'."""
    ab_id, stikkord, mottaker = ab["id"], ab["stikkord"], ab["epost"]

    svar = _usendte_svar(ab_id, MAKS_SVAR_PER_VARSEL)
    if not svar:
        return "tom"

    try:
        vist = beskriv(stikkord)
    except TomtSok:
        vist = stikkord
    emne, html, tekst = maler.svar(vist, svar, _basis_url())

    if tørrkjør:
        logger.info("[tørrkjøring] %s -> %s: %d svar", mottaker, stikkord, len(svar))
        return "svar"

    try:
        epostmodul.send(epostmodul.Epost(til=mottaker, emne=emne, html=html, tekst=tekst))
    except epostmodul.EpostFeil as exc:
        logger.error("Svarvarsel til %s feilet: %s", mottaker, exc)
        brukere.logg_utsending(ab_id, mottaker, emne, len(svar), "svar",
                               status="feilet", feil=str(exc))
        return "feil"

    # Som for vanlige varsler: markeres først etter vellykket sending.
    _marker_svar_sendt(ab_id, [s["hendelse_id"] for s in svar])
    brukere.logg_utsending(ab_id, mottaker, emne, len(svar), "svar")
    logger.info("Sendte svarvarsel til %s: %d svar for %r",
                mottaker, len(svar), stikkord)
    return "svar"


# ── Nye treff ────────────────────────────────────────────────────────────

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

        # Svar behandles separat, så en feil her ikke stopper vanlige varsler
        # (og omvendt). Velkomstabonnement har ingen svar å få ennå.
        try:
            svar_utfall = behandle_svar(ab, tørrkjør)
        except Exception as exc:
            logger.exception("Uventet feil i svarvarsel for abonnement %s: %s",
                             ab["id"], exc)
            res.feilet += 1
            continue
        if svar_utfall == "svar":
            res.svar += 1
        elif svar_utfall == "feil":
            res.feilet += 1

    logger.info("Utsending ferdig: %s", res)
    return res
