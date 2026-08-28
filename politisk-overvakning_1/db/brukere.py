"""Databaselag for varslingsdelen — brukere, abonnement og utsendinger.

Skilt fra db/lager.py med vilje. Alt her slettes når First House flyttes inn
i Signalist; dokumentlageret blir stående.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2.extras

from db.lager import kobling
from matching.sporring import bygg_tsquery

logger = logging.getLogger(__name__)

LENKE_LEVETID = timedelta(minutes=30)

# Domener som får registrere seg. Settes med TILLATTE_DOMENER, komma-separert.
#
# uppercase.no er med fordi Uppercase drifter tjenesten og må kunne teste,
# feilsøke og vise den fram. Merk at ingen ser andres abonnement uansett —
# hvilke temaer en rådgiver overvåker kan røpe hvilken kunde han jobber for,
# og det er First House sine data. Driftstilgangen bør stå i
# databehandleravtalen.
STANDARD_DOMENER = ("firsthouse.no", "uppercase.no")


class UgyldigEpost(ValueError):
    """E-postadressen er ikke innenfor et tillatt domene."""


def tillatte_domener() -> tuple[str, ...]:
    rå = os.environ.get("TILLATTE_DOMENER", "")
    if not rå.strip():
        return STANDARD_DOMENER
    domener = tuple(
        d.strip().lower().lstrip("@") for d in rå.split(",") if d.strip()
    )
    return domener or STANDARD_DOMENER


# ── Brukere ──────────────────────────────────────────────────────────────
def normaliser_epost(epost: str) -> str:
    """Trim, gjør om til små bokstaver, og krev et tillatt domene.

    Tilgangsstyringen ligger her og ingen andre steder. Alle med en adresse på
    et tillatt domene kan registrere seg selv — det finnes ingen liste over
    hvem som skal ha tilgang, og en slik liste ville uansett vært utdatert på
    en måned.

    Domenet sammenlignes eksakt, ikke med endswith: `firsthouse.no.angriper.com`
    skal ikke slippe gjennom.
    """
    e = (epost or "").strip().lower()
    if "@" not in e:
        raise UgyldigEpost(_avvisningstekst())
    lokal, _, domene = e.rpartition("@")
    if domene not in tillatte_domener():
        raise UgyldigEpost(_avvisningstekst())
    if not lokal or len(e) > 254:
        raise UgyldigEpost("Ugyldig e-postadresse")
    return e


def _avvisningstekst() -> str:
    d = tillatte_domener()
    if len(d) == 1:
        return f"Bare @{d[0]}-adresser har tilgang"
    return "Bare adresser på " + " eller ".join(f"@{x}" for x in d) + " har tilgang"


def finn_eller_opprett_bruker(epost: str) -> int:
    e = normaliser_epost(epost)
    with kobling() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO bruker (epost) VALUES (%s)
                   ON CONFLICT (epost) DO UPDATE SET epost = EXCLUDED.epost
                   RETURNING id""",
                (e,),
            )
            return cur.fetchone()[0]


def hent_bruker(bruker_id: int) -> dict[str, Any] | None:
    with kobling() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM bruker WHERE id = %s AND aktiv", (bruker_id,))
            rad = cur.fetchone()
            return dict(rad) if rad else None


# ── Innlogging med engangslenke ──────────────────────────────────────────
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def lag_innloggingslenke(epost: str) -> tuple[str, str]:
    """Returnerer (token, normalisert e-post).

    Tokenet returneres i klartekst kun her, for å settes inn i e-posten.
    Databasen lagrer bare hashen.
    """
    e = normaliser_epost(epost)
    bruker_id = finn_eller_opprett_bruker(e)
    token = secrets.token_urlsafe(32)
    utlopt = datetime.now(timezone.utc) + LENKE_LEVETID
    with kobling() as conn:
        with conn.cursor() as cur:
            # Eldre ubrukte lenker gjøres ugyldige, så det aldri finnes flere
            # gyldige lenker til samme konto samtidig.
            cur.execute(
                """UPDATE innloggingslenke SET utlopt = NOW()
                   WHERE bruker_id = %s AND brukt IS NULL AND utlopt > NOW()""",
                (bruker_id,),
            )
            cur.execute(
                """INSERT INTO innloggingslenke (bruker_id, token_hash, utlopt)
                   VALUES (%s, %s, %s)""",
                (bruker_id, _hash(token), utlopt),
            )
    return token, e


def los_inn_lenke(token: str) -> int | None:
    """Bruk en innloggingslenke. Returnerer bruker_id, eller None.

    Lenken kan kun brukes én gang. En lenke som ligger i en e-postinnboks
    skal ikke gi evig tilgang.
    """
    if not token:
        return None
    with kobling() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE innloggingslenke SET brukt = NOW()
                   WHERE token_hash = %s AND brukt IS NULL AND utlopt > NOW()
                   RETURNING bruker_id""",
                (_hash(token),),
            )
            rad = cur.fetchone()
            if not rad:
                return None
            cur.execute(
                "UPDATE bruker SET sist_innlogget = NOW() WHERE id = %s", (rad[0],)
            )
            return rad[0]


# ── Abonnement ───────────────────────────────────────────────────────────
def hent_abonnement(bruker_id: int) -> list[dict[str, Any]]:
    with kobling() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT a.*,
                          (SELECT count(*) FROM varsel_sendt v
                           WHERE v.abonnement_id = a.id) AS antall_sendt
                   FROM abonnement a
                   WHERE a.bruker_id = %s AND a.aktiv
                   ORDER BY a.opprettet DESC""",
                (bruker_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def opprett_abonnement(bruker_id: int, stikkord: str) -> int:
    """Opprett et abonnement. Validerer stikkordet før lagring."""
    stikkord = (stikkord or "").strip()
    if not stikkord:
        raise ValueError("Stikkord kan ikke være tomt")
    bygg_tsquery(stikkord)  # kaster TomtSok hvis ingenting er søkbart

    with kobling() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO abonnement (bruker_id, stikkord) VALUES (%s, %s)
                   ON CONFLICT (bruker_id, stikkord)
                   DO UPDATE SET aktiv = TRUE
                   RETURNING id""",
                (bruker_id, stikkord),
            )
            return cur.fetchone()[0]


def slett_abonnement(bruker_id: int, abonnement_id: int) -> bool:
    """Deaktiver et abonnement.

    Raden beholdes, og det er med vilje: `varsel_sendt` peker hit. Slettes
    raden, mister vi kvitteringen på hva som er sendt — og gjenoppretter noen
    samme stikkord senere, ville alt blitt sendt om igjen.
    """
    with kobling() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE abonnement SET aktiv = FALSE
                   WHERE id = %s AND bruker_id = %s AND aktiv
                   RETURNING id""",
                (abonnement_id, bruker_id),
            )
            return cur.fetchone() is not None


def aktive_abonnement() -> list[dict[str, Any]]:
    """Alle aktive abonnement med brukerens e-post. Brukes av utsendingen."""
    with kobling() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT a.id, a.stikkord, a.velkomst_sendt, b.epost
                   FROM abonnement a
                   JOIN bruker b ON b.id = a.bruker_id
                   WHERE a.aktiv AND b.aktiv
                   ORDER BY b.epost, a.opprettet""",
            )
            return [dict(r) for r in cur.fetchall()]


# ── Varsler ──────────────────────────────────────────────────────────────
def usendte_treff(
    abonnement_id: int, stikkord: str, grense: int = 50
) -> list[dict[str, Any]]:
    """Dokumenter som matcher stikkordet og ikke er sendt til dette abonnementet.

    NOT EXISTS mot varsel_sendt er den harde garantien mot duplikater. Den er
    uavhengig av diff-logikken i innhentingen: selv om et dokument skulle bli
    registrert som nytt igjen, er det allerede kvittert ut her.
    """
    query = bygg_tsquery(stikkord)
    with kobling() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT d.id, d.tittel, d.sammendrag, d.dokumenttype, d.kildenavn,
                       d.url, d.publisert, d.avsender, d.parti, d.mottaker,
                       d.besvart_av, d.status, d.henvisning
                FROM dokument d
                WHERE d.sok_vektor @@ to_tsquery('norwegian', %(q)s)
                  AND NOT EXISTS (
                      SELECT 1 FROM varsel_sendt v
                      WHERE v.abonnement_id = %(ab)s AND v.dokument_id = d.id
                  )
                ORDER BY d.publisert DESC NULLS LAST, d.id DESC
                LIMIT %(grense)s
                """,
                {"q": query, "ab": abonnement_id, "grense": grense},
            )
            return [dict(r) for r in cur.fetchall()]


def marker_sendt(abonnement_id: int, dokument_ider: list[int]) -> None:
    if not dokument_ider:
        return
    with kobling() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO varsel_sendt (abonnement_id, dokument_id) VALUES %s "
                "ON CONFLICT DO NOTHING",
                [(abonnement_id, d) for d in dokument_ider],
            )


def marker_velkomst_sendt(abonnement_id: int) -> None:
    with kobling() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE abonnement SET velkomst_sendt = NOW() WHERE id = %s",
                (abonnement_id,),
            )


def logg_utsending(
    abonnement_id: int | None, epost: str, emne: str,
    antall: int, type_: str = "varsel", status: str = "sendt", feil: str = "",
) -> None:
    with kobling() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO utsendingslogg
                   (abonnement_id, epost, emne, antall_treff, type, status, feilmelding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (abonnement_id, epost, emne, antall, type_, status, feil[:500]),
            )
