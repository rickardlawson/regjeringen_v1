# Politisk overvåkning — innhentingslag

Henter politiske dokumenter fra Stortinget og normaliserer dem til én form.
Dette er **ryggraden**, ment å overleve inn i Signalist. Varslingslaget
(abonnement, e-post, webvisning) bygges oppå og er midlertidig.

Erstatter den forsvunne `rss-daily`-appen som First House brukte.

## Status

Innhenting, matching og varsling er ferdig og testet mot ekte data.
Utsending står i konsollmodus til Resend-kontoen er på plass.

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
varsling/
  epost.py            Resend, med konsollmodus som fungerer uten konto
  maler.py            E-postmaler i First House sin form
  utsending.py        Hvem skal ha hva, og hva er allerede sendt
web/
  app.py              Flask: innlogging, abonnement, arkivsøk
  templates/          Tre skjermbilder
send_varsler.py       CLI: send varsler (egen cron-jobb)
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
| Aktuelt | RSS | 30 |
| Regjeringen.no | RSS | 98 |

Til sammen ~5 280 dokumenter. Tilveksten er rundt 15 skriftlige spørsmål per
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

- 95 tester passerer
- Full innhenting mot ekte API: 4 988 dokumenter, 0 hoppet over
- **Idempotent**: kjøring 2 og 3 gir `0 nye, 0 endrede, 4988 uendrede`
- 100 % unike ID-er på alle fire API-kilder
- Databasepoolen overlever at Postgres dropper forbindelser (testet med
  `pg_terminate_backend` midt i trafikk — 6/6 spørringer OK)
- Ondsinnet stikkordsinput (`havbruk'; DROP TABLE dokument;--`) renses og gir
  normalt søkeresultat; tabellen står urørt

## Åpne punkter

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


## Varslingslaget

Midlertidig. Slettes når First House flyttes inn i Signalist; dokumentlageret
og matchingen blir stående. Ingen fremmednøkler går fra `dokument` og ned hit,
nettopp for at det skal kunne droppes rent.

### Innlogging uten passord

Brukeren skriver e-postadressen sin og får en innloggingslenke. Ingen passord
lagres, så det finnes ingen passord å lekke. Tokenet ligger kun som hash i
databasen og virker i 30 minutter.

Lenken tåler inntil fem bruk innenfor vinduet. Den var opprinnelig strengt
engangs, og det fungerte ikke hos First House: lenkeskanneren i Microsoft 365
følger URL-er automatisk for å sjekke om de er trygge, og brukte opp lenken før
brukeren rakk å klikke — «Lenken er brukt opp» på første forsøk, hver gang.
Sikkerhetsmessig er endringen liten, siden innboksen uansett er roten til
tilliten: den som leser e-posten kan bare be om en ny lenke. Taket begrenser
skaden hvis selve lenken lekker videre.

Tilgangsstyringen er domenet: alle med en adresse på et tillatt domene kan
registrere seg selv. Det finnes ingen brukerliste å vedlikeholde — en slik
liste ville uansett vært utdatert på en måned.

Standard er `firsthouse.no` og `uppercase.no`, overstyrbart med
`TILLATTE_DOMENER`. Uppercase er med fordi vi drifter tjenesten og må kunne
teste og feilsøke. Ingen ser andres abonnement, så det gir ikke innsyn i First
House sine overvåkninger — men **driftstilgangen hører hjemme i
databehandleravtalen**.

Domenet sammenlignes eksakt, ikke med `endswith`:
`firsthouse.no.angriper.com` slipper ikke gjennom.

Innloggingsforsøk er ratebegrenset, ellers kunne hvem som helst fylt en
kollegas innboks med lenker.

### Duplikatvern i to lag

Dette er det viktigste i hele varslingsdelen.

1. **Innhentingen** avgjør hva som er NYTT (diff mot forrige kjøring).
2. **`varsel_sendt`** avgjør hva som er SENDT.

Primærnøkkelen `(abonnement_id, dokument_id)` gjør det fysisk umulig å sende
samme dokument to ganger til samme abonnement — uansett hva som skjer i
diff-logikken.

Testet mot det verst tenkelige: alle 5 145 dokumenter ble markert som nettopp
registrert, altså akkurat 27.08-hendelsen i stor skala. Resultat:
`0 varsler, 0 velkomstmailer, 1 uten nye treff`. Den gamle løsningen hadde
bare lag 1, og sendte «Statsbudsjettet 2026» tre ganger i én e-post.

Kvittering skjer **etter** vellykket sending. Feiler e-posten, forsøkes
varselet på nytt neste runde i stedet for å tapes i stillhet.

### Velkomstmail

Et nytt abonnement på «Havbruk OR Fiskeri» treffer 58 saker i inneværende
sesjon. Velkomstmailen **viser** de 15 nyeste, men **kvitterer ut alle 58**.

Uten det siste ville de 43 andre gått ut som «nye varsler» i de neste
kjøringene — tre e-poster på rad, fulle av saker fra i vår. Semantikken skal
være: her er det som finnes nå, resten ligger i arkivet, fremover hører du
bare om det som faktisk er nytt.

### E-post uten Resend-konto

`EPOST_LEVERANDOR=konsoll` skriver e-posten til loggen i stedet for å sende.
Standard, så et feilkonfigurert miljø aldri sender ut noe uventet.

Det er ikke bare for testing: da dette ble bygget hadde vi ikke tilgang til
Resend-kontoen som DNS-postene for `oppdatert.firsthouse.no` peker mot, fordi
personen som satte den opp hadde sluttet. Hele laget kan bygges, testes og
gjennomgås i konsollmodus, og byttes med én miljøvariabel når kontoen er på
plass.

Webgrensesnittet viser en synlig stripe til brukerne så lenge utsending ikke
er aktiv — abonnementene lagres som normalt, og sendes når den slås på.

### Deploy

Tre tjenester mot samme Postgres:

| Tjeneste | Dockerfile | Cron | Rolle |
|---|---|---|---|
| innhenting | `Dockerfile` | `0 5,11 * * 1-5` | henter fra Stortinget |
| varsling | `Dockerfile` | `0 6,12 * * 1-5` | sender e-post |
| web | `Dockerfile.web` | — | grensesnittet |

Webtjenesten må peke på `railway.web.toml` under Settings → Config-as-code.
Uten det bygger den cron-jobbens image, starter `hent.py`, avslutter etter tre
sekunder — og svarer aldri.

Varslingstjenesten bruker samme image som innhentingen, men med
start-kommandoen `python3 send_varsler.py`. Den kjører en time etter, så det
alltid finnes ferske data å varsle om.

Webtjenesten trenger `SECRET_KEY` og `BASIS_URL`, og et eget domene —
innloggingslenkene peker dit.


## Railway-oppsett

Config as Code (`railway.toml`) ble deprecated 28.08.2026 og kan ikke tas i
bruk av nye tjenester. Erstatningen krever TypeScript og Railways npm-SDK, som
er feil pris for et Python-prosjekt som skal slettes. Alt settes derfor i
dashbordet — og dokumenteres her, siden det ikke lenger er versjonert.

**Felles for alle tre:** Root Directory `politisk-overvakning_1`,
`DATABASE_URL = ${{Postgres.DATABASE_URL}}`.

| | innhenting | varsling | web |
|---|---|---|---|
| Dockerfile Path | `Dockerfile` | `Dockerfile` | `Dockerfile.web` |
| Start Command | (fra Dockerfile) | `python3 send_varsler.py` | (fra Dockerfile) |
| Cron Schedule | `0 5,11 * * 1-5` | `0 6,12 * * 1-5` | (ingen) |
| Healthcheck | — | — | `/helse` |

Webtjenesten trenger i tillegg `SECRET_KEY`, `BASIS_URL` og et generert domene.

**Porten:** Railway setter `PORT=8080`, men et generert domene låses ofte til
5000. Stemmer de ikke overens, får du «Application failed to respond» selv om
appen kjører fint. Rett porten under Networking, ikke i koden.

**`if __name__ == "__main__"` under gunicorn.** Under gunicorn er `__name__`
ikke `"__main__"`, så alt nederst i `web/app.py` kjører aldri. Dette har bitt
oss to ganger:

- **28.08:** `logging.basicConfig` lå der. Uten handler dropper Python alt
  under WARNING, og appen var blind — både konsoll-e-postene og tracebackene
  fra 500-handleren forsvant i stillhet.
- **31.08:** `init_skjema()` lå der. Webtjenesten deployet med ny kode mot et
  gammelt skjema og ga 500 på innlogging, for First House sin første tester,
  til en cron-jobb tilfeldigvis hadde lagt til kolonnen.

Begge kjører nå ved import, og en test i `test_varsling.py` leser kildekoden
og feiler hvis noe av det havner tilbake i `__main__`.

**Skjemaendringer** er derfor trygge: alle tre tjenestene kjører
`init_skjema()` ved oppstart, og all DDL er `IF NOT EXISTS`.


## regjeringen.no — URL-fella

Kilden så blokkert ut i flere dager. Den var bare feiladressert.

Den gamle løsningen brukte spørringsparameter:

```
https://www.regjeringen.no/no/rss/Rss/?id=2581966     → HTTP 404
```

Riktig format i dag er sti-segment:

```
https://www.regjeringen.no/no/rss/Rss/2581966/        → HTTP 200, 98 items
```

Det som villedet oss: vanlige HTML-sider på regjeringen.no ligger bak
Cloudflare og svarer 403 med utfordringsside. RSS-endepunktet gjør ikke det —
det svarte 404 helt uten Cloudflare. Forskjellen mellom 403-med-Cloudflare og
404-uten er hele diagnosen, og den var lett å overse fordi begge ser ut som
«kommer ikke gjennom».

Feeden er god: 98 saker, alle med ekte numerisk `<guid>`, ordentlig `pubDate`
og dyplenker. Den dekker det Stortinget ikke har — ministerbesøk,
departementsnyheter, taler og kalenderoppføringer, og saker før de fremmes for
Stortinget.

Feeden lar seg ikke filtrere på departement eller tema via URL-parametere;
filtrene på regjeringen.no sin byggerside genererer nye feed-ID-er. Det gjør
ingenting — vi filtrerer uansett på vår side med brukerens stikkord, og da
oppfører kilden seg likt som alle de andre.


## Deling av søk

Første funksjonsønske fra en bruker: «kan jeg melde opp en kollega på søket
mitt?»

Svaret er en **delelenke**, ikke påmelding. Del-knappen på et abonnement
kopierer en URL:

```
https://politisk.uppercase.no/nytt?stikkord=Havbruk+OR+Fiskeri
```

Mottakeren åpner den, ser stikkordet ferdig utfylt og hvor mange treff det
gir, og lagrer sitt **eget** abonnement.

Grunnen til at vi ikke melder andre på: de ville fått e-post de ikke ba om, og
som de ikke kunne slå av — abonnementet ville tilhørt avsenderen. Samtykket
hører hjemme hos mottakeren. Det ville også brutt regelen om at ingen ser
andres abonnement, som finnes fordi hvilke temaer en rådgiver overvåker kan
røpe hvilken kunde vedkommende jobber for.

Lenken inneholder kun stikkordet — ingen bruker-id, ingen abonnement-id, ingen
e-postadresse.

**`neste` bæres gjennom hele innloggingen.** Er mottakeren ikke innlogget,
følger stikkordet med fra delelenken, gjennom skjemaet, inn i e-postlenken og
tilbake etter pålogging. Uten det havner en ny bruker på et tomt skjema, og
delingen er bortkastet. `_trygg_sti()` godtar kun interne stier, så
parameteren ikke kan misbrukes til åpen viderekobling.
