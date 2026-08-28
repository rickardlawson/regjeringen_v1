-- Politisk overvåkning — dokumentlager
--
-- Ett bord for alle kilder. Matchings- og varslingslaget skal aldri trenge å
-- vite om et dokument kom fra Stortingets API eller en RSS-feed.
--
-- Dette skjemaet er ment å overleve inn i Signalist. Varslingsdelen
-- (abonnement/varsel) er midlertidig og ligger derfor i egne tabeller som kan
-- droppes uten å røre dokumentlageret.

CREATE TABLE IF NOT EXISTS dokument (
    id              BIGSERIAL PRIMARY KEY,

    -- Unik nøkkel. For API-kilder er kilde_id en ekte, stabil ID fra
    -- Stortinget. Det er dette som gjør deduplisering pålitelig — den gamle
    -- løsningen manglet det og sendte «Statsbudsjettet 2026» tre ganger.
    kilde           TEXT NOT NULL,
    kilde_id        TEXT NOT NULL,
    kildenavn       TEXT NOT NULL DEFAULT '',

    tittel          TEXT NOT NULL,
    sammendrag      TEXT NOT NULL DEFAULT '',
    dokumenttype    TEXT NOT NULL DEFAULT '',
    henvisning      TEXT NOT NULL DEFAULT '',
    url             TEXT NOT NULL DEFAULT '',
    publisert       TIMESTAMPTZ,

    -- Politisk kontekst
    avsender        TEXT NOT NULL DEFAULT '',
    parti           TEXT NOT NULL DEFAULT '',
    mottaker        TEXT NOT NULL DEFAULT '',
    besvart_av      TEXT NOT NULL DEFAULT '',
    komite          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT '',

    -- Emner holdes atskilt fra søketeksten med vilje. Den gamle løsningen
    -- søkte i emnelisten sammen med tittel og brødtekst, og da traff
    -- stikkordet «havbruk» på Statsbudsjettet 2026 fordi emnelisten
    -- inneholdt «Fiskerier». Emner er et svakere signal og skal vektes lavere.
    emner           TEXT[] NOT NULL DEFAULT '{}',

    -- TRUE når kilde_id er hashet fordi kilden mangler stabil ID (RSS).
    id_er_syntetisk BOOLEAN NOT NULL DEFAULT FALSE,

    -- Hash av innholdsbærende felter. Brukes til å skille «endret» fra «nytt».
    innholdshash    TEXT NOT NULL,

    -- Ubearbeidet post, så vi kan re-normalisere uten å hente alt på nytt.
    raadata         JSONB NOT NULL DEFAULT '{}'::jsonb,

    forst_sett      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sist_sett       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sist_endret     TIMESTAMPTZ,

    UNIQUE (kilde, kilde_id)
);

CREATE INDEX IF NOT EXISTS dokument_publisert_idx
    ON dokument (publisert DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS dokument_forst_sett_idx
    ON dokument (forst_sett DESC);
CREATE INDEX IF NOT EXISTS dokument_kilde_idx
    ON dokument (kilde);
CREATE INDEX IF NOT EXISTS dokument_emner_idx
    ON dokument USING GIN (emner);

-- Fulltekstsøk på norsk. Tittel vektes over sammendrag (A over B) slik at et
-- treff i tittelen rangeres høyere enn et treff langt nede i brødteksten.
ALTER TABLE dokument
    ADD COLUMN IF NOT EXISTS sok_vektor tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('norwegian', coalesce(tittel, '')), 'A') ||
        setweight(to_tsvector('norwegian', coalesce(sammendrag, '')), 'B') ||
        setweight(to_tsvector('norwegian', coalesce(henvisning, '')), 'C')
    ) STORED;

CREATE INDEX IF NOT EXISTS dokument_sok_idx
    ON dokument USING GIN (sok_vektor);

-- Logg over innhentinger. Gjør det mulig å se «når kjørte den sist, og hva
-- kom inn» uten å grave i applikasjonsloggen.
CREATE TABLE IF NOT EXISTS innhentingslogg (
    id              BIGSERIAL PRIMARY KEY,
    startet         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ferdig          TIMESTAMPTZ,
    antall_hentet   INTEGER NOT NULL DEFAULT 0,
    antall_nye      INTEGER NOT NULL DEFAULT 0,
    antall_endrede  INTEGER NOT NULL DEFAULT 0,
    kilder_ok       TEXT[] NOT NULL DEFAULT '{}',
    kilder_feilet   TEXT[] NOT NULL DEFAULT '{}',
    feilmelding     TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS innhentingslogg_startet_idx
    ON innhentingslogg (startet DESC);

-- ═══════════════════════════════════════════════════════════════════════════
-- VARSLINGSLAGET
--
-- Alt under dette skillet er midlertidig og slettes når First House flyttes
-- inn i Signalist. Tabellene over (dokument, innhentingslogg) blir stående.
-- Ingen fremmednøkler går fra dokumentlageret og ned hit, nettopp for at
-- dette skal kunne droppes uten å røre ryggraden.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS bruker (
    id              BIGSERIAL PRIMARY KEY,
    epost           TEXT NOT NULL UNIQUE,
    opprettet       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sist_innlogget  TIMESTAMPTZ,
    aktiv           BOOLEAN NOT NULL DEFAULT TRUE
);

-- Innloggingslenker. Ingen passord lagres noe sted — da finnes det heller
-- ingen passord å lekke. Selve tokenet lagres kun som hash, slik at et innsyn
-- i databasen ikke gir noen tilgang til kontoene.
CREATE TABLE IF NOT EXISTS innloggingslenke (
    id          BIGSERIAL PRIMARY KEY,
    bruker_id   BIGINT NOT NULL REFERENCES bruker(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    utlopt      TIMESTAMPTZ NOT NULL,
    brukt       TIMESTAMPTZ,
    opprettet   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS innloggingslenke_bruker_idx
    ON innloggingslenke (bruker_id, opprettet DESC);

CREATE TABLE IF NOT EXISTS abonnement (
    id          BIGSERIAL PRIMARY KEY,
    bruker_id   BIGINT NOT NULL REFERENCES bruker(id) ON DELETE CASCADE,
    stikkord    TEXT NOT NULL,
    opprettet   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    aktiv       BOOLEAN NOT NULL DEFAULT TRUE,
    -- Velkomstmail sendes én gang ved opprettelse, med et utvalg treff.
    -- Deretter kun nye dokumenter.
    velkomst_sendt TIMESTAMPTZ,
    UNIQUE (bruker_id, stikkord)
);

CREATE INDEX IF NOT EXISTS abonnement_aktiv_idx ON abonnement (aktiv)
    WHERE aktiv;

-- Kvittering på hva som faktisk er sendt. Dette er den viktigste tabellen i
-- hele varslingslaget.
--
-- Unikhetskravet (abonnement_id, dokument_id) gjør det FYSISK UMULIG å sende
-- samme dokument to ganger til samme abonnement, uansett hva som skjer i
-- diff-logikken. Da RSS-feeden byttet ID-ordning 27.08.2026 og 262 dokumenter
-- så nye ut, ville denne tabellen ha stoppet utsendingen — de gamle radene
-- ville fortsatt ha ligget her.
--
-- Diffen avgjør HVA som er nytt. Denne tabellen avgjør hva som er SENDT.
-- To uavhengige mekanismer, fordi den gamle løsningen bare hadde den første.
CREATE TABLE IF NOT EXISTS varsel_sendt (
    abonnement_id BIGINT NOT NULL REFERENCES abonnement(id) ON DELETE CASCADE,
    dokument_id   BIGINT NOT NULL,
    sendt         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (abonnement_id, dokument_id)
);

CREATE INDEX IF NOT EXISTS varsel_sendt_tid_idx ON varsel_sendt (sendt DESC);

CREATE TABLE IF NOT EXISTS utsendingslogg (
    id            BIGSERIAL PRIMARY KEY,
    abonnement_id BIGINT REFERENCES abonnement(id) ON DELETE SET NULL,
    epost         TEXT NOT NULL,
    emne          TEXT NOT NULL,
    antall_treff  INTEGER NOT NULL DEFAULT 0,
    type          TEXT NOT NULL DEFAULT 'varsel',
    status        TEXT NOT NULL DEFAULT 'sendt',
    feilmelding   TEXT NOT NULL DEFAULT '',
    tidspunkt     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS utsendingslogg_tid_idx ON utsendingslogg (tidspunkt DESC);
