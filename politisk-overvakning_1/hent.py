#!/usr/bin/env python3
"""Kjør én innhenting: hent alle kilder, finn nye, lagre.

    python3 hent.py                    # inneværende sesjon
    python3 hent.py --sesjon 2025-2026
    python3 hent.py --torrkjor         # hent og diff, men ikke skriv
"""
from __future__ import annotations

import argparse
import os
import signal
import logging
import sys

from db import lager
from innhenting import rss, stortinget_api
from innhenting.diff import finn_nye, forste_gangs_kjoring
from innhenting.kilder import API_KILDER, RSS_KILDER

logger = logging.getLogger("hent")

# Settes av vaktbikkja. Et flagg er nødvendig fordi et unntak ikke er nok:
# fyrer SIGALRM midt i et HTTP-kall, fanger `requests` TimeoutError og pakker
# den om til RequestException. Da ser den ut som en helt vanlig kildefeil, og
# jobben fullfører med et halvt datasett og rapporterer suksess.
_tidsavbrudd = False


def _avbrutt() -> bool:
    return _tidsavbrudd


def kjor(sesjon: str | None = None, torrkjor: bool = False) -> int:
    ok: list[str] = []
    feilet: list[str] = []
    hentede = []

    def _hent(kilde, henter) -> None:  # noqa: ANN001
        if _avbrutt():
            raise TimeoutError("Tidsavbrudd — avbryter innhentingen.")
        try:
            hentede.extend(henter(kilde, sesjon) if sesjon is not None else henter(kilde))
            ok.append(kilde.navn)
        except TimeoutError:
            raise
        except Exception as exc:
            if _avbrutt():
                # Feilen skyldes vaktbikkja, ikke kilden.
                raise TimeoutError("Tidsavbrudd under henting.") from exc
            logger.error("%s feilet: %s", kilde.navn, exc)
            feilet.append(kilde.navn)

    for kilde in API_KILDER:
        _hent(kilde, lambda k, s=None: stortinget_api.hent_kilde(k, s))
    for kilde in RSS_KILDER:
        _hent(kilde, lambda k, s=None: rss.hent_feed(k))

    if _avbrutt():
        raise TimeoutError("Tidsavbrudd — skriver ikke.")

    if not hentede:
        logger.error("Ingen dokumenter hentet — avbryter uten å skrive.")
        return 1

    # Feiler mer enn halvparten av kildene, er noe strukturelt galt. Da er det
    # tryggere å avbryte enn å skrive et halvt datasett og se vellykket ut.
    if len(feilet) > len(ok):
        logger.error(
            "Flertallet av kildene feilet (%s) — avbryter uten å skrive.",
            ", ".join(feilet),
        )
        return 1

    logger.info("Hentet %d dokumenter fra %d kilder.", len(hentede), len(ok))

    if torrkjor:
        logger.info("Tørrkjøring — skriver ikke til databasen.")
        for d in hentede[:5]:
            logger.info("  [%s] %s", d.dokumenttype or d.kilde, d.tittel[:70])
        return 0

    logg_id = lager.start_logg()
    try:
        kjente = lager.hent_kjente_hasher()
        diff = finn_nye(hentede, kjente)

        if forste_gangs_kjoring(diff):
            logger.warning(
                "Kaldstart: %d nye dokumenter. Varslingslaget må sende "
                "velkomstmail med utvalg, ikke ett varsel per dokument.",
                len(diff.nye),
            )

        lager.lagre(hentede)
        lager.avslutt_logg(
            logg_id, len(hentede), len(diff.nye), len(diff.endrede), ok, feilet
        )
        logger.info("Ferdig: %s", diff)
        return 0
    except Exception as exc:
        logger.exception("Innhenting feilet: %s", exc)
        lager.avslutt_logg(logg_id, len(hentede), 0, 0, ok, feilet, str(exc))
        return 1


def sjekk_kilder() -> int:
    """Test alle datakilder uten å skrive noe. Kjøres etter deploy."""
    feil = 0
    print("\nStortinget — API")
    for kilde in API_KILDER:
        try:
            n = len(stortinget_api.hent_kilde(kilde, None))
            print(f"  OK    {n:6} dokumenter  {kilde.kildenavn}")
        except Exception as exc:
            print(f"  FEIL              {kilde.kildenavn}: {str(exc)[:60]}")
            feil += 1

    print("\nRSS")
    for kilde in RSS_KILDER:
        try:
            n = len(rss.hent_feed(kilde))
            print(f"  OK    {n:6} dokumenter  {kilde.kildenavn}")
        except Exception as exc:
            print(f"  FEIL              {kilde.kildenavn}: {str(exc)[:60]}")
            feil += 1

    print("\nDatabase")
    try:
        with lager.kobling() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM dokument")
                print(f"  OK    {cur.fetchone()[0]:6} dokumenter lagret")
    except Exception as exc:
        print(f"  FEIL  {str(exc)[:70]}")
        feil += 1

    print()
    return 1 if feil else 0


def main() -> int:
    p = argparse.ArgumentParser(description="Hent politiske dokumenter.")
    p.add_argument("--sesjon", default=None, help="f.eks. 2025-2026")
    p.add_argument("--torrkjor", action="store_true", help="ikke skriv til DB")
    p.add_argument(
        "--sjekk-kilder", action="store_true",
        help="test alle kilder og databasen, skriv ingenting",
    )
    p.add_argument(
        "--maks-sekunder", type=int, default=int(os.environ.get("MAKS_SEKUNDER", 600)),
        help="hard grense for kjøretid (standard 600)",
    )
    a = p.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    # Vaktbikkje. Railway hopper over neste cron-kjøring hvis den forrige
    # fortsatt er aktiv — og sier ikke fra. En hengende jobb ville altså
    # stanset innhentingen for godt, i stillhet. Heller drepe oss selv.
    if hasattr(signal, "SIGALRM"):
        def _timeout(signum, frame):  # noqa: ANN001
            global _tidsavbrudd
            _tidsavbrudd = True
            raise TimeoutError(f"Overskred {a.maks_sekunder} sekunder — avbryter.")
        signal.signal(signal.SIGALRM, _timeout)
        signal.alarm(a.maks_sekunder)

    try:
        if a.sjekk_kilder:
            return sjekk_kilder()
        # Idempotent, og gjør at et nytt miljø bare virker.
        lager.init_skjema()
        return kjor(a.sesjon, a.torrkjor)
    except TimeoutError as exc:
        logger.error("%s", exc)
        return 2
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
        lager.lukk_pool()


if __name__ == "__main__":
    sys.exit(main())
