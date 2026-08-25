"""RSS-innhenting.

Stortingets RSS-feeder har tre egenskaper som knekker en vanlig parser:

  1. <guid> er tom.
  2. <pubDate> finnes ikke — datoen ligger i <dc:date> (Dublin Core).
  3. <link> peker til samme oversiktsside for ALLE poster i feeden.

Uten stabil ID må deduplisering gjøres på en hash av innholdet. Det er skjørt:
endrer Stortinget ett komma i tittelen, ser posten ny ut. Derfor brukes RSS
kun til kilder APIet ikke dekker, og alle RSS-dokumenter merkes med
`id_er_syntetisk=True` slik at varslingslaget kan behandle dem forsiktigere.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from .kilder import RSS_KILDER, RssKilde
from .modell import Dokument
from .normalisering import rydd_tekst, syntetisk_id

logger = logging.getLogger(__name__)

_TIMEOUT = 45
_BRUKERAGENT = "Uppercase-PolitiskOvervakning/1.0 (+https://uppercase.no)"
_DC = "{http://purl.org/dc/elements/1.1/}"


class RssFeil(RuntimeError):
    """Feeden svarte ikke, eller er ikke gyldig XML."""


def _tekst(node: ET.Element, *tagger: str) -> str:
    """Returner teksten fra første tag som finnes og har innhold."""
    for tag in tagger:
        funnet = node.find(tag)
        if funnet is not None and funnet.text and funnet.text.strip():
            return funnet.text.strip()
    return ""


def parse_dato_rss(node: ET.Element) -> datetime | None:
    """Les dato fra <pubDate> ELLER <dc:date>.

    Stortinget bruker dc:date. En parser som bare ser etter pubDate får None,
    og da havner alt i samme tidsbøtte.
    """
    rå = _tekst(node, "pubDate")
    if rå:
        try:
            return parsedate_to_datetime(rå)
        except (TypeError, ValueError):
            pass
    rå = _tekst(node, f"{_DC}date")
    if rå:
        try:
            return datetime.fromisoformat(rå.replace("Z", "+00:00"))
        except ValueError:
            logger.debug("Ukjent dc:date-format: %r", rå)
    return None


def parse_feed(xml: str | bytes, kilde: RssKilde) -> list[Dokument]:
    """Parse RSS-XML til normaliserte dokumenter."""
    try:
        rot = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise RssFeil(f"{kilde.navn}: ugyldig XML: {exc}") from exc

    kanal = rot.find("channel")
    poster = (kanal if kanal is not None else rot).findall("item")

    dokumenter: list[Dokument] = []
    for post in poster:
        tittel = rydd_tekst(_tekst(post, "title"))
        if not tittel:
            continue
        sammendrag = rydd_tekst(_tekst(post, "description"))
        publisert = parse_dato_rss(post)

        # Bruk guid hvis den finnes og har innhold — ellers hash innholdet.
        guid = _tekst(post, "guid")
        if guid:
            kilde_id, syntetisk = guid, False
        else:
            datostreng = publisert.date().isoformat() if publisert else ""
            kilde_id = syntetisk_id(tittel, datostreng, sammendrag[:200])
            syntetisk = True

        dokumenter.append(
            Dokument(
                kilde=kilde.navn,
                kilde_id=kilde_id,
                kildenavn=kilde.kildenavn,
                tittel=tittel,
                sammendrag=sammendrag,
                url=_tekst(post, "link"),
                publisert=publisert,
                id_er_syntetisk=syntetisk,
                rådata={"kanal": kilde.kildenavn},
            )
        )

    syntetiske = sum(1 for d in dokumenter if d.id_er_syntetisk)
    if syntetiske:
        logger.info(
            "%s: %d dokumenter, %d uten stabil ID (hashet)",
            kilde.navn, len(dokumenter), syntetiske,
        )
    return dokumenter


def hent_feed(kilde: RssKilde) -> list[Dokument]:
    try:
        resp = requests.get(
            kilde.url, timeout=_TIMEOUT, headers={"User-Agent": _BRUKERAGENT}
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RssFeil(f"Klarte ikke hente {kilde.navn}: {exc}") from exc
    return parse_feed(resp.content, kilde)


def hent_alle_feeder(kilder=RSS_KILDER) -> list[Dokument]:
    """Hent alle RSS-kilder. En feed som feiler stopper ikke de andre."""
    alle: list[Dokument] = []
    for kilde in kilder:
        try:
            alle.extend(hent_feed(kilde))
        except RssFeil as exc:
            logger.error("Hopper over %s: %s", kilde.navn, exc)
    return alle
