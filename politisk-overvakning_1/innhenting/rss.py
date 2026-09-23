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
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from .kilder import RSS_KILDER, RssKilde
from .modell import Dokument
from .normalisering import rydd_tekst

logger = logging.getLogger(__name__)

_TIMEOUT = 45
_BRUKERAGENT = "Uppercase-PolitiskOvervakning/1.0 (+https://uppercase.no)"
_DC = "{http://purl.org/dc/elements/1.1/}"

# Typede delfeeder har de 100 siste postene av sin type, og for sjeldne typer
# (proposisjoner, NOU-er) går det måneder tilbake. Uten grense ville første
# kjøring meldt alle som nye og sendt varsler om gamle dokumenter. Nyere
# poster enn dette har den generelle feeden allerede levert oss.
_MAKS_ALDER_DELFEED = timedelta(days=14)


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


def utled_type_fra_url(url: str) -> str:
    """Dokumenttype for poster fra den generelle feeden, ut fra URL-en.

    Brukes bare der ingen typet delfeed har levert posten. Kun mønstre som
    er entydige — ellers heller tom type enn feil type.
    """
    u = url.lower()
    if "/dokumenter/horing" in u or "/dokumenter/hoyring" in u:
        return "Høring"
    if "/sub/eos-notatbasen/" in u:
        return "EØS-notat"
    if "/reiseinformasjon/" in u:
        return "Reiseinformasjon"
    if "/statsbudsjett/" in u:
        return "Statsbudsjett"
    if "/tema/" in u:
        return "Temaside"
    return ""


def parse_feed(
    xml: str | bytes,
    kilde: RssKilde,
    dokumenttype: str = "",
    feed: str = "",
) -> list[Dokument]:
    """Parse RSS-XML til normaliserte dokumenter.

    Krever at hver post har en utfylt <guid>. Det er en bevisst streng regel,
    og den kommer av en konkret hendelse:

    Koden falt tidligere tilbake på en innholdshash når <guid> manglet. Den
    26.08.2026 leverte Stortingets feeder ingen guid, og 262 dokumenter fikk
    hashede ID-er. Dagen etter leverte de samme feedene guid — og fordi
    ID-ordningen dermed byttet, så alle 262 dokumentene nye ut på én gang.
    Hadde varslingslaget vært i drift, ville hver bruker fått 262 duplikater.

    En kilde som noen ganger har stabil ID og noen ganger ikke, er ikke egnet
    som varslingskilde. Derfor: mangler guid, avvises posten heller enn å få
    en ID som kan bytte ordning under føttene på oss.
    """
    etikett = f"{kilde.navn}[{feed}]" if feed else kilde.navn
    try:
        rot = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise RssFeil(f"{etikett}: ugyldig XML: {exc}") from exc

    kanal = rot.find("channel")
    poster = (kanal if kanal is not None else rot).findall("item")

    rådata: dict[str, str] = {"kanal": kilde.kildenavn}
    if feed:
        rådata["feed"] = feed

    dokumenter: list[Dokument] = []
    uten_guid = 0
    for post in poster:
        tittel = rydd_tekst(_tekst(post, "title"))
        if not tittel:
            continue

        guid = _tekst(post, "guid")
        if not guid:
            uten_guid += 1
            continue

        dokumenter.append(
            Dokument(
                kilde=kilde.navn,
                kilde_id=guid,
                kildenavn=kilde.kildenavn,
                tittel=tittel,
                sammendrag=rydd_tekst(_tekst(post, "description")),
                dokumenttype=dokumenttype,
                url=_tekst(post, "link"),
                publisert=parse_dato_rss(post),
                rådata=dict(rådata),
            )
        )

    if uten_guid:
        logger.error(
            "%s: %d av %d poster manglet <guid> og ble forkastet. Uten stabil "
            "ID kan de ikke dedupliseres pålitelig.",
            etikett, uten_guid, len(poster),
        )
    if poster and not dokumenter:
        raise RssFeil(
            f"{etikett}: ingen poster hadde <guid> — kilden kan ikke brukes "
            f"til varsling slik den er nå."
        )

    if not feed:
        logger.info("%s: %d dokumenter", etikett, len(dokumenter))
    return dokumenter


def _hent_xml(url: str, etikett: str) -> bytes:
    try:
        resp = requests.get(
            url, timeout=_TIMEOUT, headers={"User-Agent": _BRUKERAGENT}
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RssFeil(f"Klarte ikke hente {etikett}: {exc}") from exc
    return resp.content


def _er_for_gammel(dok: Dokument, grense: datetime) -> bool:
    if dok.publisert is None:
        return True
    dato = dok.publisert
    if dato.tzinfo is None:
        dato = dato.replace(tzinfo=timezone.utc)
    return dato < grense


def _hent_med_delfeeder(kilde: RssKilde) -> list[Dokument]:
    """Hent typede delfeeder, deretter den generelle feeden som sikkerhetsnett.

    Én post = én guid = ett dokument. Typen settes fra første delfeed som har
    posten. Poster fra den generelle feeden får typen en delfeed har gitt
    samme guid (også om delfeedens kopi var for gammel til å tas med), ellers
    en type utledet fra URL-en.
    """
    grense = datetime.now(timezone.utc) - _MAKS_ALDER_DELFEED
    typer: dict[str, str] = {}
    dokumenter: list[Dokument] = []
    sett: set[str] = set()
    feilet = 0
    per_type: dict[str, int] = {}

    for verdi, typenavn in kilde.delfeeder:
        url = f"{kilde.url}?documentType={verdi}"
        try:
            deler = parse_feed(_hent_xml(url, f"{kilde.navn}[{verdi}]"), kilde, typenavn, verdi)
        except RssFeil as exc:
            logger.warning("%s: delfeed feilet: %s", kilde.navn, exc)
            feilet += 1
            continue
        for dok in deler:
            typer.setdefault(dok.kilde_id, typenavn)
            if dok.kilde_id in sett or _er_for_gammel(dok, grense):
                continue
            sett.add(dok.kilde_id)
            dokumenter.append(dok)
            per_type[typenavn] = per_type.get(typenavn, 0) + 1

    try:
        generelle = parse_feed(_hent_xml(kilde.url, kilde.navn), kilde)
    except RssFeil:
        if not dokumenter:
            raise  # alt feilet — da er kilden nede
        logger.warning("%s: generell feed feilet, bruker bare delfeedene", kilde.navn)
        generelle = []

    fra_generell = 0
    for dok in generelle:
        if dok.kilde_id in sett:
            continue
        dok.dokumenttype = typer.get(dok.kilde_id) or utled_type_fra_url(dok.url)
        sett.add(dok.kilde_id)
        dokumenter.append(dok)
        fra_generell += 1

    logger.info(
        "%s: %d dokumenter (%d fra %d delfeeder, %d kun fra generell feed%s)",
        kilde.navn, len(dokumenter), len(dokumenter) - fra_generell,
        len(kilde.delfeeder) - feilet, fra_generell,
        f", {feilet} delfeeder feilet" if feilet else "",
    )
    logger.info("%s: per type: %s", kilde.navn, per_type)
    return dokumenter


def hent_feed(kilde: RssKilde) -> list[Dokument]:
    if kilde.delfeeder:
        return _hent_med_delfeeder(kilde)
    return parse_feed(_hent_xml(kilde.url, kilde.navn), kilde)


def hent_alle_feeder(kilder=RSS_KILDER) -> list[Dokument]:
    """Hent alle RSS-kilder. En feed som feiler stopper ikke de andre."""
    alle: list[Dokument] = []
    for kilde in kilder:
        try:
            alle.extend(hent_feed(kilde))
        except RssFeil as exc:
            logger.error("Hopper over %s: %s", kilde.navn, exc)
    return alle
