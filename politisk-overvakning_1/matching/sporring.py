"""Stikkordsparsing og spørringsbygging.

To problemer løses her, og de trekker i hver sin retning.

**Bredde.** Norsk stemming fjerner endelser, men splitter ikke sammensatte ord:

    havbruk            -> 'havbruk'
    havbruket          -> 'havbruk'          (endelse fjernet)
    havbruksnæringen   -> 'havbruksnæring'   (ANNET ord)
    havbruksmelding    -> 'havbruksmelding'  (ANNET ord)

Et søk på «havbruk» finner altså ikke Havbruksfondet, havbruksdirektoratet
eller havbruksnæringen. Det er derfor First House opplevde at ett stikkord
ikke var bredt nok, og selv la til «havbruksmelding» som eget søkeord.
Løsningen er prefikssøk: `havbruk:*` treffer alle tokens som begynner likt.

**Presisjon.** Prefikssøk på korte ord blir for vidt — «hav:*» treffer havari
og havn like gjerne som havbruk. Derfor krever prefikssøk minst
`MIN_PREFIKS_LENGDE` tegn.

Emner (Stortingets emnetagger) holdes utenfor søketeksten. Det er en egen sak,
se skjema.sql.
"""
from __future__ import annotations

import re

# Under denne lengden blir prefikssøk for vidt, og termen brukes eksakt.
MIN_PREFIKS_LENGDE = 4

# Skilletegn mellom stikkord. Victor skriver «Havbruk OR Fiskeri»; andre vil
# skrive komma eller «eller». Alle skal fungere.
_SKILLE = re.compile(r"\s+(?:OR|ELLER)\s+|[,;]|\s*\|\s*", re.IGNORECASE)

# tsquery-metategn må aldri nå Postgres fra brukerinput.
_UGYLDIG = re.compile(r"[^\w\sæøåÆØÅ-]", re.UNICODE)
_FLERE_MELLOMROM = re.compile(r"\s+")


class TomtSok(ValueError):
    """Stikkordstrengen inneholdt ingen brukbare termer."""


def del_opp(stikkord: str) -> list[str]:
    """Del en abonnementsstreng i enkeltermer.

    >>> del_opp("Havbruk OR Fiskeri")
    ['Havbruk', 'Fiskeri']
    >>> del_opp("havbruk, fiskeri, oppdrett")
    ['havbruk', 'fiskeri', 'oppdrett']
    >>> del_opp("Energi eller Havvind")
    ['Energi', 'Havvind']
    """
    deler = _SKILLE.split(stikkord or "")
    return [d.strip() for d in deler if d and d.strip()]


def _rens(term: str) -> str:
    """Fjern alt som kan tolkes som tsquery-syntaks."""
    t = _UGYLDIG.sub(" ", term)
    t = t.replace("-", " ")  # «fiskeri- og havministeren» -> tre ord
    return _FLERE_MELLOMROM.sub(" ", t).strip()


def _term_til_tsquery(term: str) -> str:
    """Bygg tsquery-fragment for én term.

    Flerordstermer AND-es sammen, slik at «fiskeri og havministeren» krever
    alle ordene. Siste ord får prefiks slik at bøyninger og sammensetninger
    treffer.
    """
    renset = _rens(term)
    if not renset:
        return ""
    ord_liste = [o for o in renset.split() if o]
    if not ord_liste:
        return ""

    fragmenter: list[str] = []
    for ord_ in ord_liste:
        if len(ord_) >= MIN_PREFIKS_LENGDE:
            fragmenter.append(f"{ord_}:*")
        else:
            fragmenter.append(ord_)
    return " & ".join(fragmenter)


def bygg_tsquery(stikkord: str) -> str:
    """Gjør en abonnementsstreng om til en tsquery-streng.

    Termer OR-es sammen — Victors ønske er «få opp sakene som gjelder de ulike
    stikkordene», altså union, ikke snitt.

    >>> bygg_tsquery("Havbruk OR Fiskeri")
    'havbruk:* | fiskeri:*'
    >>> bygg_tsquery("havvind")
    'havvind:*'
    >>> bygg_tsquery("EU")
    'eu'
    """
    termer = del_opp(stikkord)
    if not termer:
        raise TomtSok("Ingen stikkord oppgitt")

    fragmenter = [f for f in (_term_til_tsquery(t) for t in termer) if f]
    if not fragmenter:
        raise TomtSok(f"Fant ingen søkbare ord i {stikkord!r}")

    # Parenteser rundt flerordsfragmenter så OR-en binder riktig.
    innpakket = [f"({f})" if " & " in f else f for f in fragmenter]
    return " | ".join(innpakket).lower()


def beskriv(stikkord: str) -> str:
    """Menneskelesbar forklaring, til visning i e-post og admin.

    >>> beskriv("Havbruk OR Fiskeri")
    'Havbruk eller Fiskeri'
    """
    termer = del_opp(stikkord)
    if len(termer) <= 1:
        return stikkord.strip()
    return " eller ".join(termer)
