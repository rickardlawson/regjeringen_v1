
/
Claude finished the response
Troubleshooting why a GitHub file upload isn't committing.
Troubleshooting why a GitHub file upload isn't committing.
Ja, commit den. Det er trygt, fordi filen ikke gjør noe før den blir kalt fra koden. Railway bygger på nytt, men jobben oppfører seg akkurat som før.

Steg 1: Commit filen

Sjekk at filen ligger på riktig sted: politisk-overvakning_1/innhenting/stortinget_web_fallback.py. Ligger den i roten av repoet eller i en annen mappe, flytt den dit før du committer.
Nederst på siden, under «Commit changes», skriver du en melding, for eksempel: Legg til fallback for skriftlige spørsmål fra stortinget.no
La «Commit directly to the main branch» være valgt.
Klikk Commit changes.
Steg 2: Vis meg hvor API-hentingen skjer

For å koble fallbacken inn må jeg se hvordan jobben henter og lagrer skriftlige spørsmål i dag. Send meg innholdet (eller et skjermbilde) av:

filen som starter jobben (sannsynligvis main.py, run.py eller lignende i politisk-overvakning_1/)
innhenting/stortinget_api.py
Da skriver jeg de nøyaktige linjene du skal lime inn, og hvor de skal stå.

Skjermbilde 2026-09-22 kl. 09.10.39.png
Filen er allerede committet. Når man laster opp via GitHub, skjer committen i samme operasjon. «innhenting» viser «now», så den ligger på plass.

Steg 2: Send meg tre filer

hent.py er nesten helt sikkert filen som starter jobben. Åpne hver av disse i GitHub, klikk på kopieringsikonet («Copy raw file») øverst til høyre, og lim innholdet inn her:

hent.py
innhenting/stortinget_api.py
requirements.txt, så jeg kan sjekke at requests er med. Fallbacken trenger det.
Da skriver jeg nøyaktig hva som skal endres, linje for linje.


#!/usr/bin/env python3 """Kjør én innhenting: hent alle kilder, finn nye, lagre. python3 hent.py # inneværende sesjon python3 hent.py --sesjon 2025-2026 python3 hent.py --torrkjor # hent og diff, men ikke skriv """ from __future__ import annotations import ar

pasted


"""Henting og normalisering fra Stortingets åpne API. APIet krever ingen autentisering og har ingen bot-beskyttelse. Alle poster har stabile ID-er, som gjør deduplisering triviell — i motsetning til RSS. """ from __future__ import annotations import logging from typing import Any, Callable, Iterab

pasted

3.# Låste versjoner. Uten pinning bygger Railway mot nyeste pakke — det er slik
# en app som "ikke er rørt" plutselig slutter å virke.
requests==2.33.1
psycopg2-binary==2.9.12
flask==3.1.3
gunicorn==26.1.0

Ran 3 commands
Ja, gjør to commits nå. Jeg har skrevet om fallback-filen slik at den lager samme type Dokument som API-et, med samme kilde og kilde_id. Da havner et spørsmål i samme rad når API-et tar igjen etterslepet. hent.py kaller nå fallbacken. Begge filene er syntakssjekket, og fallbacken er testet: den finner 103 manglende spørsmål, inkludert surrogatispørsmålet, med riktig avsender, parti og statsråd. requests står allerede i requirements, så den trenger ingen endring.

Steg 3: Erstatt fallback-filen

I GitHub, åpne politisk-overvakning_1/innhenting/stortinget_web_fallback.py.
Klikk blyanten (Edit) øverst til høyre.
Merk alt (Cmd+A) og slett.
Lim inn innholdet fra den nye stortinget_web_fallback.py nedenfor.
Klikk Commit changes, skriv meldingen Fallback returnerer Dokument-objekter, og klikk Commit changes igjen.
Steg 4: Erstatt hent.py

Åpne politisk-overvakning_1/hent.py.
Klikk blyanten, merk alt og slett.
Lim inn innholdet fra den nye hent.py nedenfor.
Commit med meldingen Koble inn web-fallback for skriftlige spørsmål.
Endringene i hent.py er disse:

Én ekstra import.
En blokk etter API- og RSS-hentingen som kjører fallbacken.
En ekstra linje i --sjekk-kilder.
Fallbacken kjører bare når API-hentingen av skriftlige spørsmål lyktes i samme kjøring. Ellers kunne den overskrevet gode API-data med tynnere web-data.

Steg 5: Test i Railway

Gå til regjeringen_v1 og vent til den nye deployen er grønn.
Trigg en kjøring: trepunktsmenyen på siste deployment, deretter Restart.
Se i loggene etter denne linjen:
stortinget_web_skriftlig: 103 skriftlige spørsmål som mangler i API-et (tallet kan være litt annerledes).
Står det stortinget_web_skriftlig feilet, eller finner du ImportError eller SyntaxError i loggen, send meg et skjermbilde.
Søk på «surrogati» på politisk.uppercase.no.
Én ting jeg ikke kan se herfra: når API-et senere leverer samme spørsmål, blir raden oppdatert med litt annen tekst. Den regnes da som «endret», ikke «ny». Hvis varslingen også sender e-post for endrede dokumenter, kan noen få et dobbeltvarsel én gang per spørsmål. Send meg gjerne send_varsler.py etterpå, så sjekker jeg det.

Presented 2 files
Presented 2 files
Begge filene ligger vedlagt nedenfor.


Stortinget web fallback
PY 

Hent
PY 



Claude is AI and can make mistakes.
Stortinget web fallback · PY
"""Fallback for skriftlige spørsmål: listesiden på stortinget.no.
 
data.stortinget.no kan ligge flere dager etter nettsiden (sept. 2026: ~100
spørsmål / 6 dager bak). Listesiden har en innebygd JSON-blob per spørsmål,
så vi slipper å skrape HTML eller hente hver detaljside.
 
qnid på nettsiden == id i API-et. Dokumentene får samme `kilde` og
`kilde_id` som API-versjonen, så når API-et tar igjen etterslepet,
overskrives web-versjonen av den fyldigere API-versjonen i samme rad.
"""
from __future__ import annotations
 
import html
import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo
 
import requests
 
from .modell import Dokument
from .normalisering import rydd_tekst, skriftlig_sporsmal_url
 
logger = logging.getLogger(__name__)
 
LISTE_URL = (
    "https://www.stortinget.no/no/Saker-og-publikasjoner/Sporsmal/"
    "Skriftlige-sporsmal-og-svar/"
)
_TIMEOUT = 30
_BRUKERAGENT = "Uppercase-PolitiskOvervakning/1.0 (+https://uppercase.no)"
_OSLO = ZoneInfo("Europe/Oslo")
 
# Ett JSON-objekt per spørsmål på listesiden.
_ITEM_RE = re.compile(
    r'\{"questionPageLink":\{.*?"presentationQuestionTitleText":".*?(?<!\\)"\}', re.S
)
# "Skriftlig spørsmål fra Grunde Almeland (V) til barne- og familieministeren"
_OVERSKRIFT_RE = re.compile(r"fra (?P<navn>.+?) \((?P<parti>[^)]+)\) til (?P<til>.+)$")
 
# API-et bruker fullt partinavn; nettsiden bruker forkortelse.
_PARTI = {
    "A": "Arbeiderpartiet", "H": "Høyre", "FrP": "Fremskrittspartiet",
    "Sp": "Senterpartiet", "SV": "Sosialistisk Venstreparti", "R": "Rødt",
    "V": "Venstre", "MDG": "Miljøpartiet De Grønne",
    "KrF": "Kristelig Folkeparti", "PF": "Pasientfokus",
}
 
 
def _dato(tekst: str | None) -> datetime | None:
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", tekst or "")
    if not m:
        return None
    d, mnd, aar = (int(x) for x in m.groups())
    return datetime(aar, mnd, d, tzinfo=_OSLO)
 
 
def _normaliser(post: dict) -> Dokument | None:
    href = (post.get("questionPageLink") or {}).get("href", "")
    m = re.search(r"qnid=(\d+)", href)
    tittel = rydd_tekst(html.unescape(post.get("presentationQuestionTitleText") or ""))
    if not m or not tittel:
        return None
    qnid = m.group(1)
 
    overskrift = html.unescape((post.get("questionPageLink") or {}).get("text", ""))
    o = _OVERSKRIFT_RE.search(overskrift)
    avsender = rydd_tekst(o.group("navn")) if o else ""
    parti = _PARTI.get(o.group("parti"), o.group("parti")) if o else ""
    mottaker = rydd_tekst(o.group("til")) if o else ""
 
    besvart = "besvart" in (post.get("presentationAnsweredText") or "").lower()
 
    return Dokument(
        kilde="stortinget_skriftlig_sporsmal",
        kilde_id=qnid,
        kildenavn="Stortinget: Skriftlige spørsmål",
        tittel=tittel,
        dokumenttype="Skriftlig spørsmål",
        url=skriftlig_sporsmal_url(qnid),
        publisert=(
            _dato(post.get("presentationSentText"))
            or _dato(post.get("presentationDatedText"))
        ),
        avsender=avsender,
        parti=parti,
        mottaker=mottaker,
        status="Besvart" if besvart else "Til behandling",
        emner=[],
        rådata={**post, "_kilde": "stortinget_web"},
    )
 
 
def _hent_side(side: int) -> list[Dokument]:
    resp = requests.get(
        LISTE_URL, params={"page": side},
        timeout=_TIMEOUT, headers={"User-Agent": _BRUKERAGENT},
    )
    resp.raise_for_status()
    ut: list[Dokument] = []
    for raw in _ITEM_RE.findall(resp.text):
        try:
            dok = _normaliser(json.loads(raw))
        except Exception as exc:  # én råtten post skal ikke velte kjøringen
            logger.warning("web-fallback: hoppet over post: %s", exc)
            continue
        if dok:
            ut.append(dok)
    return ut
 
 
def hent_manglende(api_ider: set[str], maks_sider: int = 10) -> list[Dokument]:
    """Spørsmål på stortinget.no som ikke er med i API-svaret.
 
    Blar fra nyeste og stopper når en hel side kun har id-er API-et kjenner.
    """
    nye: list[Dokument] = []
    sett: set[str] = set()
    for side in range(1, maks_sider + 1):
        dokumenter = _hent_side(side)
        if not dokumenter:
            if side == 1:
                # Sannsynligvis endret sideoppsett — si fra i stedet for å tie.
                raise RuntimeError("Fant ingen spørsmål på listesiden (endret oppsett?)")
            break
        ukjente = [
            d for d in dokumenter
            if d.kilde_id not in api_ider and d.kilde_id not in sett
        ]
        for d in ukjente:
            sett.add(d.kilde_id)
        nye.extend(ukjente)
        if not ukjente:
            break
    return nye
 




