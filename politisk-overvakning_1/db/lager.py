"""Databaselag for dokumentlageret.

Poolen er bygget med test-on-borrow fra dag én. Railway resirkulerer
Postgres-forbindelser, og uten denne sjekken serverer poolen døde forbindelser
og brukeren får tilfeldige 500-feil til containeren restartes.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime  # noqa: F401 — brukt i typehint
from pathlib import Path
from typing import Any, Generator, Iterable

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from innhenting.diff import innholdshash
from innhenting.modell import Dokument
from matching.sporring import bygg_tsquery

logger = logging.getLogger(__name__)

_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()
_CHECKOUT_FORSOK = 3
_DODE_FEIL = (psycopg2.OperationalError, psycopg2.InterfaceError)


def hent_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                url = os.environ.get("DATABASE_URL")
                if not url:
                    # Ikke SystemExit: det dreper gunicorn-workeren ved import.
                    raise RuntimeError("DATABASE_URL mangler")
                _pool = ThreadedConnectionPool(
                    1, 5, url,
                    connect_timeout=10,
                    keepalives=1, keepalives_idle=30,
                    keepalives_interval=10, keepalives_count=3,
                    application_name="politisk-overvakning",
                )
    return _pool


def _levende_conn(pool: ThreadedConnectionPool):
    siste: Exception | None = None
    for _ in range(_CHECKOUT_FORSOK):
        try:
            conn = pool.getconn()
        except psycopg2.pool.PoolError as exc:
            raise RuntimeError("Databasepoolen er tom — prøv igjen om litt") from exc
        try:
            if conn.closed:
                raise psycopg2.InterfaceError("connection already closed")
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            conn.rollback()
            return conn
        except _DODE_FEIL as exc:
            siste = exc
            logger.warning("Forkaster død databaseforbindelse: %s", exc)
            try:
                pool.putconn(conn, close=True)
            except Exception:
                pass
    raise RuntimeError("Fikk ikke en levende databaseforbindelse") from siste


@contextmanager
def kobling() -> Generator[Any, None, None]:
    pool = hent_pool()
    conn = _levende_conn(pool)
    odelagt = False
    try:
        yield conn
        conn.commit()
    except Exception as exc:
        odelagt = isinstance(exc, _DODE_FEIL)
        if not odelagt:
            try:
                conn.rollback()
            except Exception:
                odelagt = True
        raise
    finally:
        try:
            pool.putconn(conn, close=odelagt)
        except Exception as exc:
            logger.warning("Klarte ikke levere forbindelsen tilbake: %s", exc)


def lukk_pool() -> None:
    """Lukk alle forbindelser og nullstill poolen.

    Railway kjører innhentingen som en cron-jobb: containeren startes, gjør
    jobben og skal avslutte. Åpne databaseforbindelser holder prosessen i live,
    og henger en kjøring, hopper Railway over ALLE senere kjøringer uten å si
    fra. Derfor lukkes poolen eksplisitt i stedet for å stole på at
    tolkeren rydder opp ved avslutning.
    """
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
                logger.info("Databasepool lukket.")
            except Exception as exc:
                logger.warning("Feil ved lukking av pool: %s", exc)
            finally:
                _pool = None


def init_skjema() -> None:
    """Kjør skjema.sql. Idempotent — all DDL er IF NOT EXISTS.

    Kalles ved hver kjøring. Den koster noen millisekunder når tabellene
    finnes, og sparer et manuelt steg som er lett å glemme ved oppsett av et
    nytt miljø.
    """
    sql = (Path(__file__).parent / "skjema.sql").read_text(encoding="utf-8")
    with kobling() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)


def hent_kjente_hasher() -> dict[tuple[str, str], str]:
    """{(kilde, kilde_id): innholdshash} for alt vi har lagret."""
    with kobling() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT kilde, kilde_id, innholdshash FROM dokument")
            return {(k, i): h for k, i, h in cur.fetchall()}


def lagre(dokumenter: Iterable[Dokument]) -> int:
    """Sett inn eller oppdater dokumenter. Returnerer antall berørte rader.

    `forst_sett` bevares ved oppdatering — det er den datoen som avgjør om et
    dokument er nytt for en abonnent, og den skal aldri flyttes.
    """
    rader = [
        (
            d.kilde, d.kilde_id, d.kildenavn, d.tittel, d.sammendrag,
            d.dokumenttype, d.henvisning, d.url, d.publisert,
            d.avsender, d.parti, d.mottaker, d.besvart_av, d.komite, d.status,
            d.emner, d.id_er_syntetisk, innholdshash(d),
            json.dumps(d.rådata, ensure_ascii=False, default=str),
        )
        for d in dokumenter
    ]
    if not rader:
        return 0

    with kobling() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO dokument (
                    kilde, kilde_id, kildenavn, tittel, sammendrag,
                    dokumenttype, henvisning, url, publisert,
                    avsender, parti, mottaker, besvart_av, komite, status,
                    emner, id_er_syntetisk, innholdshash, raadata
                ) VALUES %s
                ON CONFLICT (kilde, kilde_id) DO UPDATE SET
                    tittel       = EXCLUDED.tittel,
                    sammendrag   = EXCLUDED.sammendrag,
                    dokumenttype = EXCLUDED.dokumenttype,
                    henvisning   = EXCLUDED.henvisning,
                    url          = EXCLUDED.url,
                    publisert    = EXCLUDED.publisert,
                    avsender     = EXCLUDED.avsender,
                    parti        = EXCLUDED.parti,
                    mottaker     = EXCLUDED.mottaker,
                    besvart_av   = EXCLUDED.besvart_av,
                    komite       = EXCLUDED.komite,
                    status       = EXCLUDED.status,
                    emner        = EXCLUDED.emner,
                    raadata      = EXCLUDED.raadata,
                    sist_sett    = NOW(),
                    sist_endret  = CASE
                        WHEN dokument.innholdshash IS DISTINCT FROM EXCLUDED.innholdshash
                        THEN NOW() ELSE dokument.sist_endret END,
                    innholdshash = EXCLUDED.innholdshash
                """,
                rader,
                page_size=500,
            )
            return cur.rowcount


_FELTER = """kilde, kilde_id, kildenavn, tittel, sammendrag, dokumenttype,
             henvisning, url, publisert, avsender, parti, mottaker,
             besvart_av, komite, status, emner, forst_sett"""


def sok(
    stikkord: str,
    grense: int = 50,
    nye_etter: "datetime | None" = None,
) -> list[dict[str, Any]]:
    """Fulltekstsøk på norsk, rangert.

    `stikkord` kan være én term eller en OR-liste: «Havbruk OR Fiskeri»,
    «energi, havvind». Se matching/sporring.py for hvordan det tolkes.

    Søker i tittel/sammendrag/henvisning — IKKE i emnelisten. Se kommentaren
    i skjema.sql for hvorfor.

    `nye_etter` begrenser til dokumenter registrert etter et tidspunkt, slik
    varslingslaget trenger for å finne «nytt siden forrige utsending».
    """
    query = bygg_tsquery(stikkord)
    betingelser = ["sok_vektor @@ to_tsquery('norwegian', %(q)s)"]
    params: dict[str, Any] = {"q": query, "grense": grense}
    if nye_etter is not None:
        betingelser.append("forst_sett > %(etter)s")
        params["etter"] = nye_etter

    with kobling() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT {_FELTER},
                       ts_rank(sok_vektor, to_tsquery('norwegian', %(q)s)) AS rang
                FROM dokument
                WHERE {' AND '.join(betingelser)}
                ORDER BY rang DESC, publisert DESC NULLS LAST
                LIMIT %(grense)s
                """,
                params,
            )
            return [dict(r) for r in cur.fetchall()]


def tell_treff(stikkord: str) -> int:
    """Antall treff uten å hente radene. Brukes til forhåndsvisning når en
    bruker oppretter et abonnement."""
    query = bygg_tsquery(stikkord)
    with kobling() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM dokument "
                "WHERE sok_vektor @@ to_tsquery('norwegian', %s)",
                (query,),
            )
            return cur.fetchone()[0]


def start_logg() -> int:
    with kobling() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO innhentingslogg DEFAULT VALUES RETURNING id")
            return cur.fetchone()[0]


def avslutt_logg(
    logg_id: int, hentet: int, nye: int, endrede: int,
    ok: list[str], feilet: list[str], feilmelding: str = "",
) -> None:
    with kobling() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE innhentingslogg
                   SET ferdig = NOW(), antall_hentet = %s, antall_nye = %s,
                       antall_endrede = %s, kilder_ok = %s, kilder_feilet = %s,
                       feilmelding = %s
                   WHERE id = %s""",
                (hentet, nye, endrede, ok, feilet, feilmelding, logg_id),
            )
