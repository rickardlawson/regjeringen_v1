"""Tester for innhentingslaget.

Flere av disse dekker konkrete feil i den forrige løsningen. Kommentarene
sier hvilke — ikke slett dem uten å lese hvorfor.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from innhenting import rss, stortinget_api  # noqa: E402
from innhenting.diff import Diff, finn_nye, innholdshash  # noqa: E402
from innhenting.kilder import RssKilde  # noqa: E402
from innhenting.modell import Dokument  # noqa: E402
from innhenting.normalisering import (  # noqa: E402
    parse_dato,
    rydd_tekst,
    syntetisk_id,
    utled_dokumenttype,
)


# ── Datoer ───────────────────────────────────────────────────────────────
def test_parser_dotnet_dato() -> None:
    """Stortingets API bruker /Date(millis+offset)/, ikke ISO."""
    d = parse_dato("/Date(1787223052180+0200)/")
    assert d is not None
    assert d.year == 2026 and d.month == 8 and d.day == 20


def test_parser_iso_dato_som_reserve() -> None:
    d = parse_dato("2026-08-20T12:00:00+02:00")
    assert d is not None and d.year == 2026


@pytest.mark.parametrize("verdi", [None, "", "tull", "/Date(ikke-tall)/"])
def test_ugyldig_dato_gir_none(verdi) -> None:
    assert parse_dato(verdi) is None


# ── Dokumenttype ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "henvisning,forventet",
    [
        ("Meld. St. 13 (2025-2026)", "Melding"),
        ("Prop. 5 S (2025-2026)", "Proposisjon"),
        ("Innst. 66 S (2025-2026)", "Innstilling"),
        ("Dokument 8:12 S (2025-2026)", "Representantforslag"),
        ("Dokument 12:3 (2025-2026)", "Grunnlovsforslag"),
        ("Dokument 3:5 (2025-2026)", "Riksrevisjonen"),
    ],
)
def test_dokumenttype_fra_henvisning(henvisning: str, forventet: str) -> None:
    """`henvisning` er fasit for hva et dokument er.

    Den gamle løsningen leste feltet `type` (1/2/3) som dokumenttype. Det er
    saksklasse — budsjett/alminnelig/lov. Resultatet var at «Innst. 3 S
    Skatte-, avgifts- og tollinntekter» ble merket «Sakstype: Spørsmål» i
    varselet til First House.
    """
    assert utled_dokumenttype(henvisning) == forventet


def test_dokumentgruppe_som_reserve() -> None:
    assert utled_dokumenttype("", 2) == "Melding"
    assert utled_dokumenttype(None, 4) == "Representantforslag"


def test_ukjent_type_gir_tom_streng_ikke_gjetning() -> None:
    assert utled_dokumenttype("Noe helt annet") == ""


# ── RSS ──────────────────────────────────────────────────────────────────
_KILDE = RssKilde(navn="test", kildenavn="Test", url="https://eksempel.no/rss")

_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Stortinget: Test</title>
    <item>
      <title>Skriftlig spørsmål fra A til fiskeri- og havministeren</title>
      <link>https://www.stortinget.no/no/Saker-og-publikasjoner/Sporsmal/</link>
      <description>Om havbruk i Oslofjorden.</description>
      <dc:date>2026-08-20T10:00:00+02:00</dc:date>
    </item>
    <item>
      <title>Skriftlig spørsmål fra B til energiministeren</title>
      <link>https://www.stortinget.no/no/Saker-og-publikasjoner/Sporsmal/</link>
      <description>Om strømpriser.</description>
      <dc:date>2026-08-19T10:00:00+02:00</dc:date>
    </item>
  </channel>
</rss>"""


def test_rss_leser_dc_date_naar_pubdate_mangler() -> None:
    """Stortinget har ingen <pubDate> — datoen ligger i <dc:date>.

    En parser som bare ser etter pubDate får None på alt, og da havner hele
    feeden i samme tidsbøtte.
    """
    docs = rss.parse_feed(_FEED, _KILDE)
    assert len(docs) == 2
    assert docs[0].publisert is not None
    assert docs[0].publisert.day == 20


def test_rss_syntetisk_id_naar_guid_mangler() -> None:
    """Stortingets feeder har tom <guid> og lik <link> for alle poster."""
    docs = rss.parse_feed(_FEED, _KILDE)
    assert all(d.id_er_syntetisk for d in docs)
    # Lik lenke skal IKKE gi lik ID — ellers kolliderer alle poster.
    assert docs[0].url == docs[1].url
    assert docs[0].kilde_id != docs[1].kilde_id


def test_rss_id_er_stabil_mellom_kjoringer() -> None:
    """Samme innhold må gi samme ID, ellers varsles alt på nytt hver time."""
    a = rss.parse_feed(_FEED, _KILDE)
    b = rss.parse_feed(_FEED, _KILDE)
    assert [d.kilde_id for d in a] == [d.kilde_id for d in b]


def test_rss_bruker_guid_naar_den_finnes() -> None:
    feed = _FEED.replace(
        "<description>Om havbruk i Oslofjorden.</description>",
        "<description>Om havbruk.</description><guid>ekte-id-123</guid>",
    )
    docs = rss.parse_feed(feed, _KILDE)
    assert docs[0].kilde_id == "ekte-id-123"
    assert docs[0].id_er_syntetisk is False


def test_ugyldig_xml_gir_tydelig_feil() -> None:
    with pytest.raises(rss.RssFeil):
        rss.parse_feed("<rss><channel><item>", _KILDE)


def test_syntetisk_id_er_deterministisk() -> None:
    assert syntetisk_id("A", "B") == syntetisk_id("A", "B")
    assert syntetisk_id("A", "B") != syntetisk_id("A", "C")


# ── Normalisering fra API ────────────────────────────────────────────────
def test_normaliser_sak_bruker_ekte_id_og_riktig_type() -> None:
    post = {
        "id": 200389,
        "korttittel": "Datatilsynets årsrapport",
        "tittel": "Datatilsynets og Personvernnemndas årsrapportar for 2025",
        "henvisning": "Meld. St. 13 (2025-2026)",
        "dokumentgruppe": 2,
        "type": 2,
        "status": 3,
        "sist_oppdatert_dato": "/Date(1787305500000+0200)/",
        "emne_liste": [{"navn": "Personvern"}],
    }
    d = stortinget_api.normaliser_sak(post)
    assert d is not None
    assert d.kilde_id == "200389"
    assert d.dokumenttype == "Melding"
    assert d.status == "Behandlet"
    assert d.emner == ["Personvern"]
    assert "?p=200389" in d.url


def test_normaliser_sporsmal_henter_avsender_og_mottaker() -> None:
    post = {
        "id": 127036,
        "tittel": "Mener statsråden ...",
        "sporsmal_fra": {
            "fornavn": "Joel", "etternavn": "Ystebø",
            "parti": {"navn": "Kristelig Folkeparti"},
        },
        "sporsmal_til_minister_tittel": "justis- og beredskapsministeren",
        "besvart_dato": "/Date(1787223052180+0200)/",
    }
    d = stortinget_api.normaliser_skriftlig_sporsmal(post)
    assert d is not None
    assert d.avsender == "Joel Ystebø"
    assert d.parti == "Kristelig Folkeparti"
    assert d.mottaker == "justis- og beredskapsministeren"
    assert d.status == "Besvart"


def test_post_uten_id_hoppes_over_ikke_krasjer() -> None:
    assert stortinget_api.normaliser_sak({"korttittel": "Uten id"}) is None
    assert stortinget_api.normaliser_sak({"id": 1}) is None  # uten tittel


# ── Søketekst og emner ───────────────────────────────────────────────────
def test_emner_er_ikke_med_i_soketeksten() -> None:
    """Dette er fiksen på det største presisjonsproblemet.

    Den gamle løsningen søkte i emne_liste sammen med tittel og brødtekst.
    Derfor traff stikkordet «havbruk» på «Statsbudsjettet 2026», fordi
    emnelisten der inneholder «Fiskerier». Fem av femten treff i
    oppstartsmailen til First House var slik støy.
    """
    d = Dokument(
        kilde="test", kilde_id="1",
        tittel="Statsbudsjettet 2026",
        sammendrag="Om bevilgninger.",
        emner=["Fiskerier", "Havbruk", "Energi"],
    )
    assert "havbruk" not in d.sok_tekst.lower()
    assert "Statsbudsjettet" in d.sok_tekst


def test_dokument_krever_kilde_og_id() -> None:
    with pytest.raises(ValueError):
        Dokument(kilde="", kilde_id="1", tittel="x")
    with pytest.raises(ValueError):
        Dokument(kilde="test", kilde_id="", tittel="x")


# ── Diff ─────────────────────────────────────────────────────────────────
def _dok(kid: str, tittel: str = "Tittel", status: str = "") -> Dokument:
    return Dokument(kilde="test", kilde_id=kid, tittel=tittel, status=status)


def test_alt_er_nytt_ved_kaldstart() -> None:
    diff = finn_nye([_dok("1"), _dok("2")], {})
    assert len(diff.nye) == 2


def test_kjente_dokumenter_varsles_ikke_paa_nytt() -> None:
    """Kjernen i tjenesten. Sender du samme varsel to ganger, merkes det."""
    docs = [_dok("1"), _dok("2")]
    kjente = {d.nokkel: innholdshash(d) for d in docs}
    diff = finn_nye(docs, kjente)
    assert diff.nye == []
    assert diff.uendrede == 2


def test_endret_status_gir_endret_ikke_nytt() -> None:
    gammel = _dok("1", status="Til behandling")
    kjente = {gammel.nokkel: innholdshash(gammel)}
    ny = _dok("1", status="Besvart")
    diff = finn_nye([ny], kjente)
    assert diff.nye == []
    assert len(diff.endrede) == 1


def test_dubletter_i_samme_kjoring_fjernes() -> None:
    """Stortingets API kan returnere samme sak i flere lister.

    Den gamle løsningen sendte «Statsbudsjettet 2026» tre ganger i samme
    e-post, én av dem med sammenkjedet tittel.
    """
    diff = finn_nye([_dok("1"), _dok("1"), _dok("1")], {})
    assert len(diff.nye) == 1


def test_innholdshash_ignorerer_ren_reformatering() -> None:
    a = Dokument(kilde="t", kilde_id="1", tittel="Hei  verden")
    b = Dokument(kilde="t", kilde_id="1", tittel="Hei verden")
    assert innholdshash(a) == innholdshash(b)


def test_rydd_tekst_fjerner_html_og_entiteter() -> None:
    assert rydd_tekst("<p>Hei&nbsp;&amp; ha det</p>") == "Hei & ha det"
    assert rydd_tekst(None) == ""
