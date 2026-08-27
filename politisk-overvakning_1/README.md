# Politisk overvåkning — innhentingslag

Henter politiske dokumenter fra Stortinget og normaliserer dem til én form.
Dette er **ryggraden**, ment å overleve inn i Signalist. Varslingslaget
(abonnement, e-post, webvisning) bygges oppå og er midlertidig.

Erstatter den forsvunne `rss-daily`-appen som First House brukte.

## Status

Innhentingslaget og matchingen er ferdig og testet mot ekte data.
Varslingslaget (abonnement, e-post, webvisning) er ikke begynt.

## Kom i gang

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://...

python3 hent.py                 # oppretter skjema ved behov og henter
python3 hent.py --sjekk-kilder  # test alle kilder + databasen, skriv ingenting
python3 hent.py --torrkjor      # hent og diff uten å skrive
```

## Arkitektur

```
innhenting/
  modell.py           Dokument — felles form for alle kilder
  normalisering.py    .NET-datoer, dokumenttype, lenker, tekstrydding
  kilder.py           Kildedefinisjoner (API + RSS)
  stortinget_api.py   Hentere for saker, skriftlige spørsmål, spørretime,
                      interpellasjoner
  rss.py              RSS-parser som håndterer Stortingets særegenheter
  diff.py             Hva er nytt siden sist
matching/
  sporring.py         Stikkordsparsing: OR-lister og prefikssøk
db/
  skjema.sql          dokument + innhentingslogg
  lager.py            Pool, lagring, fulltekstsøk
hent.py               CLI
```

### Prinsipp: APIet er ryggraden, RSS er supplement

Stortingets API har stabile ID-er, ordentlige datoer og strukturerte felter.
RSS-feedene har det ikke (se under). Derfor hentes alt som finnes i APIet
derfra, og RSS brukes bare til det APIet ikke dekker.

## Kilder

| Kilde | Type | Dokumenter (2025–2026) |
|---|---|---|
| Saker | API | 665 |
| Skriftlige spørsmål | API | 3 547 |
| Spørretimespørsmål | API | 524 |
| Interpellasjoner | API | 30 |
| Høringer | API | 351 |
| Aktuelt | RSS | 28 |

Til sammen ~5 145 dokumenter. Tilveksten er rundt 15 skriftlige spørsmål per
virkedag — lite nok til at hyppig polling er uproblematisk.

## Tre feller i datakildene

Alle tre er årsaken til synlige feil i den gamle løsningens varsler.

**1. Datoer.** Stortingets API returnerer `/Date(1787223052180+0200)/`, ikke
ISO. RSS-feedene har ingen `<pubDate>` — datoen ligger i `<dc:date>`. En
parser som bare ser etter `pubDate` får `None` på alt.

**2. Dokumenttype.** Feltet `type` på en sak er 1, 2 eller 3, og betyr
*saksklasse* (budsjett / alminnelig / lov) — ikke dokumenttype. Den gamle
løsningen leste det som dokumenttype, og merket derfor «Innst. 3 S Skatte-,
avgifts- og tollinntekter» som «Sakstype: Spørsmål». Her utledes typen fra
`henvisning`, som er fasit: «Meld. St. 13 (2025-2026)» sier presist hva
dokumentet er.

**3. RSS-feedene er ustabile med hensyn til `<guid>`.** Dette tok ned
dedupliseringen i produksjon 27.08.2026 — se avsnittet under.

Koden krever nå utfylt `<guid>`. Poster uten forkastes, og en feed helt uten
guid får kilden til å feile. Stille degradering er verre enn en tydelig feil.

## Matching

Den gamle løsningen var samtidig for upresis **og** for smal. To ulike feil
med to ulike årsaker.

### For upresis: emner ble søkt sammen med teksten

Oppstartsmailen for «havbruk» ga 15 treff, hvorav minst fem ikke inneholdt
ordet i det hele tatt — «Statsbudsjettet 2026» (to ganger), «Innst. 3 S»,
«Mercator internasjonale senter for havet». De traff fordi `emne_liste`
inneholder «Fiskerier», og matchingen søkte i emnelisten sammen med resten.

Her holdes emner **utenfor** søketeksten og lagres som eget felt.

### For smal: norsk stemming splitter ikke sammensatte ord

```
havbruk            -> 'havbruk'
havbruket          -> 'havbruk'          (endelse fjernet)
havbruksnæringen   -> 'havbruksnæring'   ANNET ord
havbruksmelding    -> 'havbruksmelding'  ANNET ord
```

Et eksakt søk på «havbruk» finner altså ikke Havbruksfondet,
havbruksdirektoratet eller havbruksnæringen. Det er derfor First House
opplevde ett stikkord som for smalt — og selv la til «havbruksmelding» som
eget søkeord ved siden av «havbruk».

Løsningen er prefikssøk (`havbruk:*`), som treffer alle tokens med samme
begynnelse. Termer under fire tegn brukes eksakt; «EU:*» ville truffet
europavei og eutanasi.

### Resultat

| Abonnement | Treff |
|---|---|
| `havbruk` (eksakt, som før) | 10 |
| `havbruk` (med prefiks) | 23 |
| `Havbruk OR Fiskeri` | 58 |
| `Energi OR Havvind` | 93 |

Uten at «Statsbudsjettet 2026», «Innst. 3 S» eller «Mercator» kommer tilbake.

### Syntaks

Stikkord OR-es sammen. Alle disse gir samme resultat:

```
Havbruk OR Fiskeri
havbruk eller fiskeri
havbruk, fiskeri
havbruk | fiskeri
```

Flerordstermer krever alle ordene: `fiskeri- og havministeren` blir
`(fiskeri:* & og & havministeren:*)`. Brukerinput renses for tsquery-metategn
før den når databasen.

## Verifisert

- 59 tester passerer
- Full innhenting mot ekte API: 4 988 dokumenter, 0 hoppet over
- **Idempotent**: kjøring 2 og 3 gir `0 nye, 0 endrede, 4988 uendrede`
- 100 % unike ID-er på alle fire API-kilder
- Databasepoolen overlever at Postgres dropper forbindelser (testet med
  `pg_terminate_backend` midt i trafikk — 6/6 spørringer OK)
- Ondsinnet stikkordsinput (`havbruk'; DROP TABLE dokument;--`) renses og gir
  normalt søkeresultat; tabellen står urørt

## Åpne punkter

**regjeringen.no er ikke aktivert.** Feeden fra den gamle løsningen
(`id=2581966`) er regjeringen.no sin feed-bygger. Alle URL-varianter svarte 403
med Cloudflare-utfordring fra utviklingsmiljøet. **Må testes fra selve
Railway-containeren før det loves til First House.** Er den blokkert også
derfra, er alternativene å be Departementenes sikkerhets- og serviceorganisasjon
om tilgang, eller å droppe kilden — Stortinget dekker det meste av det samme
stoffet når saker først er fremmet.

**Dyplenker for spørsmål** bruker mønsteret `?qid=<id>`. Sidene svarer 200, men
rendres klientside, så innholdet lot seg ikke verifisere programmatisk. Bør
klikktestes manuelt på et par eksempler.

**Kaldstart.** Første kjøring mot tom database gir ~5 000 «nye» dokumenter.
`diff.forste_gangs_kjoring()` flagger dette. Varslingslaget må sende en
velkomstmail med et utvalg — slik den gamle løsningen gjorde — og ikke ett
varsel per dokument.

**Sesjonsskifte.** `--sesjon` er valgfri; uten den bruker APIet inneværende
sesjon. Ved sesjonsskifte bør en kjøring mot forrige sesjon gjøres én siste
gang, ellers mister man de siste dagene.

## Deploy på Railway

Innhentingen er en **batch-jobb, ikke en webtjeneste**. Den kjøres som en
Railway cron-jobb: containeren startes på timeplan, gjør jobben og avslutter.
Ingen alltid-på-prosess, ingen scheduler i koden, ingen `--workers 1`-krav.

Det er en bevisst forskjell fra de to Arendalsuka-prosjektene, som kjørte
APScheduler inne i en gunicorn-prosess. Den arkitekturen ga en `--workers 1`-
begrensning og en oppstart som blokkerte healthchecken.

### Oppsett

1. Push repoet til GitHub under Uppercase-organisasjonen.
2. Railway → New Project → Deploy from GitHub repo.
3. Legg til **PostgreSQL** i samme prosjekt.
4. Sett `DATABASE_URL = ${{Postgres.DATABASE_URL}}` (referansevariabel, så den
   følger med ved rotasjon).
5. Deploy. Skjemaet opprettes automatisk ved første kjøring — all DDL er
   `IF NOT EXISTS`, så det er trygt å kjøre hver gang.
6. Verifiser: sett start-kommandoen midlertidig til
   `python3 hent.py --sjekk-kilder`, deploy, les loggen, sett den tilbake.
   Den svarer blant annet på om regjeringen.no er tilgjengelig herfra.
7. Service → Settings → **Cron Schedule**: `0 5,11 * * 1-5`
   (kl. 07:00 og 13:00 norsk sommertid, ukedager).

Railway tolker cron-uttrykk i UTC, og minste frekvens er hvert 5. minutt.

### Kritisk: jobben må avslutte

Railway hopper over neste kjøring hvis den forrige fortsatt er aktiv — og sier
**ikke fra**. En hengende jobb stanser altså innhentingen for godt, i stillhet.

Derfor:

- `hent.py` har en vaktbikkje (`MAKS_SEKUNDER`, standard 600) som garanterer
  at prosessen dør.
- Databasepoolen lukkes eksplisitt i en `finally`-blokk.
- `restartPolicyType = "never"` i railway.toml. Standard er restart ved feil,
  som for en cron-jobb gjør én feilende kjøring om til ti kjøringer og ti
  alarmer. Neste planlagte kjøring prøver uansett igjen.

### Exit-koder

| Kode | Betydning |
|---|---|
| 0 | Alt gikk bra |
| 1 | Ingen dokumenter hentet, eller flertallet av kildene feilet — ingenting skrevet |
| 2 | Tidsavbrudd — ingenting skrevet |

Jobben skriver **aldri** et delvis datasett og rapporterer suksess. En stille
halv innhenting er verre enn en synlig feilet en.

## Videre

Neste lag er abonnement + e-postutsending + en enkel admin-side. Det bygges
over dette og slås av når First House flyttes inn i Signalist. Dokumentlageret
og matchingen blir stående.

**Test regjeringen.no fra Railway først.** Feeden svarte 403 med Cloudflare
fra utviklingsmiljøet. Railways IP-adresser kan gi et annet resultat. Kjør
dette i en Railway-shell før kilden loves til First House:

```bash
python3 hent.py --sjekk-kilder
```

Den rapporterer `OK` eller `BLOKKERT` for regjeringen.no, sammen med status for
alle Stortinget-kildene og databasen.
