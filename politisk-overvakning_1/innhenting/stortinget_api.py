"""Henting og normalisering fra Stortingets åpne API.

APIet krever ingen autentisering og har ingen bot-beskyttelse. Alle poster har
stabile ID-er, som gjør deduplisering triviell — i motsetning til RSS.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

import requests

from .kilder import API_BASE, API_KILDER, ApiKilde
from .modell import Dokument
from .normalisering import (
    SAKSKLASSE,
    SAKSSTATUS,
    interpellasjon_url,
    parse_dato,
    rydd_tekst,
    sak_url,
    skriftlig_sporsmal_url,
    sporretime_url,
    utled_dokumenttype,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 60
_BRUKERAGENT = "Uppercase-PolitiskOvervakning/1.0 (+https://uppercase.no)"


class ApiFeil(RuntimeError):
    """Kilden svarte ikke som forventet."""


def hent_json(endepunkt: str, sesjonid: str | None = None) -> dict[str, Any]:
    url = f"{API_BASE}/{endepunkt}?format=json"
    if sesjonid:
        url += f"&sesjonid={sesjonid}"
    try:
        resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": _BRUKERAGENT})
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise ApiFeil(f"Klarte ikke hente {endepunkt}: {exc}") from exc
    except ValueError as exc:
        raise ApiFeil(f"Ugyldig JSON fra {endepunkt}: {exc}") from exc


def _navn(post: dict[str, Any], nokkel: str) -> str:
    """Hent 'navn' fra et nøstet objekt som kan være None."""
    obj = post.get(nokkel)
    if isinstance(obj, dict):
        return rydd_tekst(obj.get("navn") or "")
    return ""


def _personnavn(obj: Any) -> tuple[str, str]:
    """Returner (navn, parti) fra et representantobjekt."""
    if not isinstance(obj, dict):
        return "", ""
    navn = " ".join(
        p for p in (obj.get("fornavn"), obj.get("etternavn")) if p
    ).strip()
    parti = ""
    p = obj.get("parti")
    if isinstance(p, dict):
        parti = rydd_tekst(p.get("navn") or "")
    return rydd_tekst(navn), parti


def _emner(post: dict[str, Any]) -> list[str]:
    liste = post.get("emne_liste") or []
    if not isinstance(liste, list):
        return []
    return [rydd_tekst(e.get("navn", "")) for e in liste if isinstance(e, dict) and e.get("navn")]


# ── Normalisering per dokumenttype ───────────────────────────────────────

def normaliser_sak(post: dict[str, Any]) -> Dokument | None:
    sak_id = post.get("id")
    if sak_id is None:
        return None
    henvisning = rydd_tekst(post.get("henvisning"))
    tittel = rydd_tekst(post.get("korttittel") or post.get("tittel"))
    if not tittel:
        return None
    lang_tittel = rydd_tekst(post.get("tittel"))
    return Dokument(
        kilde="stortinget_sak",
        kilde_id=str(sak_id),
        kildenavn="Stortinget: Saker",
        tittel=tittel,
        # Den lange tittelen er ofte en fyldigere beskrivelse enn korttittelen.
        sammendrag=lang_tittel if lang_tittel != tittel else "",
        dokumenttype=utled_dokumenttype(henvisning, post.get("dokumentgruppe")),
        henvisning=henvisning,
        url=sak_url(sak_id),
        publisert=parse_dato(post.get("sist_oppdatert_dato")),
        komite=_navn(post, "komite"),
        status=SAKSSTATUS.get(post.get("status"), ""),
        emner=_emner(post),
        rådata={
            **post,
            "_saksklasse": SAKSKLASSE.get(post.get("type"), ""),
        },
    )


def _normaliser_sporsmal(
    post: dict[str, Any],
    kilde: str,
    kildenavn: str,
    dokumenttype: str,
    url_bygger: Callable[[Any], str],
) -> Dokument | None:
    sp_id = post.get("id")
    if sp_id is None:
        return None
    tittel = rydd_tekst(post.get("tittel"))
    if not tittel:
        return None

    avsender, parti = _personnavn(post.get("sporsmal_fra"))
    besvart_av, _ = _personnavn(post.get("besvart_av"))

    return Dokument(
        kilde=kilde,
        kilde_id=str(sp_id),
        kildenavn=kildenavn,
        tittel=tittel,
        dokumenttype=dokumenttype,
        url=url_bygger(sp_id),
        publisert=(
            parse_dato(post.get("besvart_dato"))
            or parse_dato(post.get("sendt_dato"))
            or parse_dato(post.get("datert_dato"))
        ),
        avsender=avsender,
        parti=parti,
        mottaker=rydd_tekst(post.get("sporsmal_til_minister_tittel")),
        besvart_av=besvart_av or rydd_tekst(post.get("besvart_av_minister_tittel")),
        status="Besvart" if post.get("besvart_dato") else "Til behandling",
        emner=_emner(post),
        rådata=post,
    )


def normaliser_skriftlig_sporsmal(post: dict[str, Any]) -> Dokument | None:
    return _normaliser_sporsmal(
        post,
        "stortinget_skriftlig_sporsmal",
        "Stortinget: Skriftlige spørsmål",
        "Skriftlig spørsmål",
        skriftlig_sporsmal_url,
    )


def normaliser_sporretime(post: dict[str, Any]) -> Dokument | None:
    return _normaliser_sporsmal(
        post,
        "stortinget_sporretime",
        "Stortinget: Spørretimespørsmål",
        "Spørretimespørsmål",
        sporretime_url,
    )


def normaliser_interpellasjon(post: dict[str, Any]) -> Dokument | None:
    return _normaliser_sporsmal(
        post,
        "stortinget_interpellasjon",
        "Stortinget: Interpellasjoner",
        "Interpellasjon",
        interpellasjon_url,
    )


def normaliser_horing(post: dict[str, Any]) -> Dokument | None:
    """Normaliser en høring fra APIet.

    En høring har ingen egen tittel — den identifiseres av saken eller sakene
    den gjelder. Tittelen bygges derfor fra `horing_sak_info_liste`.
    """
    horing_id = post.get("id")
    if horing_id is None:
        return None

    saker = post.get("horing_sak_info_liste") or []
    sakstitler = [
        rydd_tekst(s.get("sak_korttittel") or s.get("sak_tittel") or "")
        for s in saker
        if isinstance(s, dict)
    ]
    sakstitler = [t for t in sakstitler if t]
    if not sakstitler:
        return None

    skriftlig = bool(post.get("skriftlig"))
    dokumenttype = "Skriftlig høring" if skriftlig else "Høring"
    tittel = f"{dokumenttype}: {' / '.join(sakstitler)}"

    # Sted og tidspunkt er det som gjør en høring handlingsrelevant for en
    # rådgiver — det er der man faktisk kan møte opp.
    steder = [
        rydd_tekst(t.get("sted") or "")
        for t in (post.get("horingstidspunkt_liste") or [])
        if isinstance(t, dict) and t.get("sted")
    ]

    komite = _navn(post, "komite")
    biter = [b for b in (komite, ", ".join(dict.fromkeys(steder))) if b]
    frist = parse_dato(post.get("innspillsfrist"))
    if frist:
        biter.append(f"Innspillsfrist {frist:%d.%m.%Y}")

    henvisning = ""
    for s in saker:
        if isinstance(s, dict) and s.get("sak_henvisning"):
            henvisning = rydd_tekst(s["sak_henvisning"])
            break

    # Lenk til saken høringen gjelder — RSS-feeden gjør det samme.
    url = ""
    for s in saker:
        if isinstance(s, dict) and s.get("sak_id"):
            url = sak_url(s["sak_id"])
            break

    tidspunkt = (post.get("horingstidspunkt_liste") or [{}])[0]
    return Dokument(
        kilde="stortinget_horing",
        kilde_id=str(horing_id),
        kildenavn="Stortinget: Høringer",
        tittel=tittel,
        sammendrag=" · ".join(biter),
        dokumenttype=dokumenttype,
        henvisning=henvisning,
        url=url,
        publisert=(
            parse_dato(post.get("start_dato"))
            or parse_dato(tidspunkt.get("tidspunkt") if isinstance(tidspunkt, dict) else None)
        ),
        komite=komite,
        status=rydd_tekst(post.get("horing_status")),
        rådata=post,
    )


_NORMALISERERE: dict[str, Callable[[dict[str, Any]], Dokument | None]] = {
    "stortinget_sak": normaliser_sak,
    "stortinget_skriftlig_sporsmal": normaliser_skriftlig_sporsmal,
    "stortinget_sporretime": normaliser_sporretime,
    "stortinget_interpellasjon": normaliser_interpellasjon,
    "stortinget_horing": normaliser_horing,
}


def hent_kilde(kilde: ApiKilde, sesjonid: str | None = None) -> list[Dokument]:
    """Hent og normaliser én API-kilde."""
    data = hent_json(kilde.endepunkt, sesjonid)
    poster = data.get(kilde.listenokkel) or []
    if not isinstance(poster, list):
        raise ApiFeil(f"{kilde.endepunkt}: forventet liste i '{kilde.listenokkel}'")

    normaliser = _NORMALISERERE[kilde.navn]
    dokumenter: list[Dokument] = []
    hoppet_over = 0
    for post in poster:
        try:
            dok = normaliser(post)
        except Exception as exc:  # én råtten post skal ikke velte kjøringen
            logger.warning("%s: klarte ikke normalisere post: %s", kilde.navn, exc)
            hoppet_over += 1
            continue
        if dok is None:
            hoppet_over += 1
            continue
        dokumenter.append(dok)

    logger.info(
        "%s: %d dokumenter (%d hoppet over)", kilde.navn, len(dokumenter), hoppet_over
    )
    return dokumenter


def hent_alle(
    sesjonid: str | None = None,
    kilder: Iterable[ApiKilde] = API_KILDER,
) -> list[Dokument]:
    """Hent alle API-kilder. En kilde som feiler stopper ikke de andre."""
    alle: list[Dokument] = []
    for kilde in kilder:
        try:
            alle.extend(hent_kilde(kilde, sesjonid))
        except ApiFeil as exc:
            logger.error("Hopper over %s: %s", kilde.navn, exc)
    return alle
