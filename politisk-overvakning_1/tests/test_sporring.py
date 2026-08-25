"""Tester for stikkordsparsing.

Flere av disse dekker Victors konkrete tilbakemelding: «Havbruk ikke var bredt
nok» og «Supert om det er mulig å kunne inkludere flere stikkord ala; Havbruk
OR Fiskeri eller Energi OR Havvind».
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matching.sporring import (  # noqa: E402
    MIN_PREFIKS_LENGDE,
    TomtSok,
    beskriv,
    bygg_tsquery,
    del_opp,
)


# ── Oppdeling ────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "inn,ut",
    [
        ("Havbruk OR Fiskeri", ["Havbruk", "Fiskeri"]),
        ("Energi OR Havvind", ["Energi", "Havvind"]),
        ("energi eller havvind", ["energi", "havvind"]),
        ("havbruk, fiskeri, oppdrett", ["havbruk", "fiskeri", "oppdrett"]),
        ("havbruk; fiskeri", ["havbruk", "fiskeri"]),
        ("havbruk | fiskeri", ["havbruk", "fiskeri"]),
        ("havbruk", ["havbruk"]),
    ],
)
def test_deler_paa_alle_skilletegn(inn: str, ut: list[str]) -> None:
    """Brukerne skriver ikke likt. OR, eller, komma og semikolon skal virke."""
    assert del_opp(inn) == ut


def test_or_er_ikke_kasusfolsomt() -> None:
    assert del_opp("havbruk or fiskeri") == del_opp("havbruk OR fiskeri")


# ── Prefiks: bredden Victor etterlyste ───────────────────────────────────
def test_lange_ord_far_prefiks() -> None:
    """Norsk stemming splitter ikke sammensatte ord.

    'havbruk' og 'havbruksnæringen' blir to ulike tokens, så et eksakt søk på
    «havbruk» finner ikke Havbruksfondet eller havbruksdirektoratet. Prefiks
    løser det — og er grunnen til at First House opplevde ett stikkord som
    for smalt og selv la til «havbruksmelding» ved siden av «havbruk».
    """
    assert bygg_tsquery("havbruk") == "havbruk:*"


def test_korte_ord_far_ikke_prefiks() -> None:
    """«EU:*» ville truffet europavei, eutanasi og alt annet på EU-."""
    kort = "a" * (MIN_PREFIKS_LENGDE - 1)
    assert bygg_tsquery(kort) == kort
    assert ":*" not in bygg_tsquery("EU")


def test_or_liste_gir_union() -> None:
    """Victor vil ha union, ikke snitt: «få opp sakene som gjelder de ulike
    stikkordene»."""
    assert bygg_tsquery("Havbruk OR Fiskeri") == "havbruk:* | fiskeri:*"
    assert bygg_tsquery("Energi OR Havvind") == "energi:* | havvind:*"


def test_flerordsterm_and_es_og_pakkes_inn() -> None:
    """«fiskeri- og havministeren» skal kreve alle ordene, og parentesen må
    stå så OR-en utenfor binder riktig."""
    q = bygg_tsquery("fiskeri- og havministeren OR energi")
    assert q.startswith("(")
    assert "&" in q and "|" in q


def test_victors_faktiske_abonnement() -> None:
    q = bygg_tsquery(
        "havbruk, fiskeri, oppdrett, havbruksmelding, fiskeri- og havministeren"
    )
    for ord_ in ["havbruk:*", "fiskeri:*", "oppdrett:*", "havbruksmelding:*"]:
        assert ord_ in q
    assert q.count("|") == 4  # fem termer


# ── Sikkerhet ────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "ondsinnet",
    [
        "havbruk & fiskeri'; DROP TABLE dokument;--",
        "havbruk:*:*:*",
        "((((havbruk",
        "havbruk !& | <-> fiskeri",
    ],
)
def test_tsquery_metategn_slipper_ikke_gjennom(ondsinnet: str) -> None:
    """Brukerinput går inn i to_tsquery. Ugyldig syntaks der gir feil på
    databasenivå, så metategn må renses bort først."""
    q = bygg_tsquery(ondsinnet)
    kropp = q.replace(":*", "").replace(" | ", "").replace(" & ", "")
    kropp = kropp.replace("(", "").replace(")", "").replace(" ", "")
    assert kropp.replace("-", "").isalnum() or kropp == ""


def test_tomt_sok_gir_tydelig_feil() -> None:
    for tomt in ["", "   ", ",,,", "!!!"]:
        with pytest.raises(TomtSok):
            bygg_tsquery(tomt)


def test_bindestrek_blir_ordskille_ikke_negasjon() -> None:
    """I tsquery kan bindestrek tolkes som operator. «fiskeri- og
    havministeren» må bli tre ord, ikke en syntaksfeil."""
    q = bygg_tsquery("fiskeri- og havministeren")
    assert "fiskeri:*" in q
    assert "havministeren:*" in q


# ── Visning ──────────────────────────────────────────────────────────────
def test_beskriv_er_lesbar_i_epost() -> None:
    assert beskriv("Havbruk OR Fiskeri") == "Havbruk eller Fiskeri"
    assert beskriv("havbruk") == "havbruk"
