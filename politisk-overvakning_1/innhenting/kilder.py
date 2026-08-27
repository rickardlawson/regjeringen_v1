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
# Alt som ligger her har syntetiske ID-er og er derfor mindre pålitelig
# deduplisert enn API-kildene. Finnes noe i APIet, hent det derfra.
RSS_KILDER: tuple[RssKilde, ...] = (
    RssKilde(
        navn="stortinget_aktuelt",
        kildenavn="Stortinget: Aktuelt",
        url=f"{RSS_BASE}/Aktuelt-saker/",
    ),
)

# regjeringen.no ligger bak Cloudflare og svarte 403 under uttesting fra
# utviklingsmiljøet. Feeden må verifiseres fra selve driftsmiljøet før den
# aktiveres. Se README, avsnittet «Åpne punkter».
REGJERINGEN_RSS_URL = "https://www.regjeringen.no/no/rss/Rss/?id=2581966"
