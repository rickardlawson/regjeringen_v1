#!/usr/bin/env python3
"""Send varsler for alle aktive abonnement.

Kjøres som egen Railway cron-jobb, like etter innhentingen:

    0 6,12 * * 1-5     (én time etter hent.py, som går 05:00 og 11:00 UTC)
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

from db import lager
from varsling import epost, utsending

logger = logging.getLogger("send_varsler")


def main() -> int:
    p = argparse.ArgumentParser(description="Send varsler til abonnenter.")
    p.add_argument("--torrkjor", action="store_true",
                   help="vis hva som ville blitt sendt, uten å sende")
    p.add_argument("--maks-sekunder", type=int,
                   default=int(os.environ.get("MAKS_SEKUNDER", 600)))
    a = p.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    # Samme vaktbikkje som i hent.py: Railway hopper over neste cron-kjøring
    # hvis den forrige fortsatt er aktiv, og sier ikke fra.
    if hasattr(signal, "SIGALRM"):
        def _timeout(signum, frame):  # noqa: ANN001
            raise TimeoutError(f"Overskred {a.maks_sekunder} sekunder.")
        signal.signal(signal.SIGALRM, _timeout)
        signal.alarm(a.maks_sekunder)

    if not epost.er_konfigurert() and not a.torrkjor:
        logger.warning(
            "E-postutsending er ikke konfigurert (EPOST_LEVERANDOR/RESEND_API_KEY). "
            "Varsler skrives til loggen i stedet for å sendes."
        )

    try:
        lager.init_skjema()
        res = utsending.send_alle(tørrkjør=a.torrkjor)
        logger.info("Ferdig: %s", res)
        return 1 if res.feilet else 0
    except TimeoutError as exc:
        logger.error("%s", exc)
        return 2
    except Exception as exc:
        logger.exception("Utsending feilet: %s", exc)
        return 1
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
        lager.lukk_pool()


if __name__ == "__main__":
    sys.exit(main())
