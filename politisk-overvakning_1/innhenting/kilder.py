"""Kildedefinisjoner.

Prinsippet: **APIet er ryggraden, RSS er supplement.**

Stortingets API har ekte, stabile ID-er, ordentlige datoer og strukturerte
felter. RSS-feedene har tom <guid>, ingen <pubDate> (datoen ligger i dc:date)
og samme <link> for alle poster. Deduplisering på RSS må derfor gjøres på en
hash, og det er skjørt.

RSS brukes bare til det APIet ikke dekker:
  - regjeringen.no (ingen åpen API)
  - høringsdatoer
  - aktuelt-saker
"""
from __future__ import annotations

from dataclasses import dataclass

API_BASE = "https://data.stortinget.no/eksport"
RSS_BASE = "https://www.stortinget.no/no/Stottemeny/RSS"


@dataclass(frozen=True, slots=True)
class ApiKilde:
    navn: str
    kildenavn: str
    endepunkt: str
    listenokkel: str


API_KILDER: tuple[ApiKilde, ...] = (
    ApiKilde(
        navn="stortinget_sak",
        kildenavn="Stortinget: Saker",
        endepunkt="saker",
        listenokkel="saker_liste",
    ),
    ApiKilde(
        navn="stortinget_skriftlig_sporsmal",
        kildenavn="Stortinget: Skriftlige spørsmål",
        endepunkt="skriftligesporsmal",
        listenokkel="sporsmal_liste",
    ),
    ApiKilde(
        navn="stortinget_sporretime",
        kildenavn="Stortinget: Spørretimespørsmål",
        endepunkt="sporretimesporsmal",
        listenokkel="sporsmal_liste",
    ),
    ApiKilde(
        navn="stortinget_interpellasjon",
        kildenavn="Stortinget: Interpellasjoner",
        endepunkt="interpellasjoner",
        listenokkel="sporsmal_liste",
    ),
    # Høringer hentes fra APIet, ikke fra RSS-feeden.
    #
    # RSS-versjonen hadde tom <description> på samtlige poster og ingen <guid>.
    # ID-en måtte da hashes fra tittel og dato alene, og 27.08.2026 førte det
    # til at alle 234 høringer ble registrert som nye på nytt i én kjøring.
    # Hadde varslingslaget vært i drift, ville hver bruker fått 234 varsler om
    # saker de allerede hadde sett.
    #
    # APIet gir 351 høringer med ekte, stabile ID-er — flere enn RSS, og med
    # komité, sted, frister og tilknyttet sak som strukturerte felter.
    ApiKilde(
        navn="stortinget_horing",
        kildenavn="Stortinget: Høringer",
        endepunkt="horinger",
        listenokkel="horinger_liste",
    ),
)


@dataclass(frozen=True, slots=True)
class RssKilde:
    navn: str
    kildenavn: str
    url: str


# Kun det APIet ikke gir oss. Stortinget har ~150 RSS-feeder, men de fleste er
# delmengder av data vi allerede henter strukturert via APIet — å abonnere på
# dem i tillegg ville bare gitt duplikater med dårligere metadata.
#
# Alt som ligger her må ha stabil <guid>. Se rss.py for hvorfor.
RSS_KILDER: tuple[RssKilde, ...] = (
    RssKilde(
        navn="stortinget_aktuelt",
        kildenavn="Stortinget: Aktuelt",
        url=f"{RSS_BASE}/Aktuelt-saker/",
    ),
    # regjeringen.no — pressemeldinger, nyheter, taler og kalender fra
    # departementene. Dekker det Stortinget IKKE har: ministerbesøk,
    # departementsnyheter og saker før de fremmes for Stortinget.
    #
    # URL-formatet er en felle. Den gamle løsningen brukte
    # `/no/rss/Rss/?id=2581966` — spørringsparameter — og det gir i dag 404.
    # Riktig format er sti-segment: `/no/rss/Rss/2581966/`. Det var derfor
    # kilden så blokkert ut. Vanlige HTML-sider på regjeringen.no ligger
    # bak Cloudflare og svarer 403, men RSS-endepunktet er åpent.
    RssKilde(
        navn="regjeringen",
        kildenavn="Regjeringen.no",
        url="https://www.regjeringen.no/no/rss/Rss/2581966/",
    ),
)

# Beholdt for kildesjekken (--sjekk-kilder), som tester denne eksplisitt.
REGJERINGEN_RSS_URL = "https://www.regjeringen.no/no/rss/Rss/2581966/"
