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
      <guid>tag:stortinget.no,2000:innhold.aaa</guid>
      <dc:date>2026-08-20T10:00:00+02:00</dc:date>
    </item>
    <item>
      <title>Skriftlig spørsmål fra B til energiministeren</title>
      <link>https://www.stortinget.no/no/Saker-og-publikasjoner/Sporsmal/</link>
      <description>Om strømpriser.</description>
      <guid>tag:stortinget.no,2000:innhold.bbb</guid>
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


def test_rss_bruker_guid_som_id() -> None:
    docs = rss.parse_feed(_FEED, _KILDE)
    assert docs[0].kilde_id == "tag:stortinget.no,2000:innhold.aaa"
    assert docs[0].id_er_syntetisk is False


def test_post_uten_guid_forkastes() -> None:
    """Dette er fiksen på produksjonsfeilen 27.08.2026.

    Koden falt tidligere tilbake på en innholdshash når <guid> manglet.
    Stortingets feeder leverte ingen guid 26.08, og guid 27.08. ID-ordningen
    byttet dermed under føttene på oss, og alle 262 RSS-dokumenter ble
    registrert som nye på nytt i én kjøring. Hadde varslingslaget vært i
    drift, ville hver First House-bruker fått 262 duplikatvarsler.

    En post uten stabil ID skal forkastes, ikke få en ID som kan bytte
    ordning senere.
    """
    feed = _FEED.replace(
        "<guid>tag:stortinget.no,2000:innhold.bbb</guid>", ""
    )
    docs = rss.parse_feed(feed, _KILDE)
    assert len(docs) == 1
    assert all(d.kilde_id.startswith("tag:") for d in docs)


def test_feed_helt_uten_guid_avvises_hoylytt() -> None:
    """Stille degradering er verre enn en tydelig feil. Kommer det en feed
    uten stabile ID-er, skal kilden feile — ikke levere data som ikke kan
    dedupliseres."""
    feed = _FEED.replace("<guid>tag:stortinget.no,2000:innhold.aaa</guid>", "")
    feed = feed.replace("<guid>tag:stortinget.no,2000:innhold.bbb</guid>", "")
    with pytest.raises(rss.RssFeil):
        rss.parse_feed(feed, _KILDE)


def test_id_er_stabil_mellom_kjoringer() -> None:
    a = rss.parse_feed(_FEED, _KILDE)
    b = rss.parse_feed(_FEED, _KILDE)
    assert [d.kilde_id for d in a] == [d.kilde_id for d in b]


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


# ---------------------------------------------------------------------------
# Høringer fra API — erstattet den skjøre RSS-kilden
# ---------------------------------------------------------------------------
_HORING = {
    "id": 10005544,
    "skriftlig": False,
    "horing_status": "Avholdt",
    "start_dato": "/Date(1761550200000+0100)/",
    "innspillsfrist": "/Date(1761083940000+0200)/",
    "komite": {"id": "ARBSOS", "navn": "Arbeids- og sosialkomiteen"},
    "horingstidspunkt_liste": [
        {"sted": "Stortingets komitéhus, Høringssal 1",
         "tidspunkt": "/Date(1761550200000+0100)/"}
    ],
    "horing_sak_info_liste": [
        {"sak_id": 104908,
         "sak_korttittel": "Statsbudsjettet 2026 (arbeids- og sosialkomiteen)",
         "sak_henvisning": "Prop. 1 S (2025-2026), Innst. 15 S (2025-2026)"}
    ],
}


def test_horing_bruker_ekte_id_fra_api() -> None:
    """Høringer hentes fra APIet, ikke RSS.

    RSS-versjonen hadde tom <description> på samtlige poster, så ID-en hvilte
    på tittel og dato alene. APIet gir 351 høringer med stabile ID-er.
    """
    d = stortinget_api.normaliser_horing(_HORING)
    assert d is not None
    assert d.kilde_id == "10005544"
    assert d.id_er_syntetisk is False
    assert d.dokumenttype == "Høring"
    assert d.komite == "Arbeids- og sosialkomiteen"
    assert d.status == "Avholdt"


def test_horing_bygger_tittel_fra_saken() -> None:
    """En høring har ingen egen tittel — den identifiseres av saken."""
    d = stortinget_api.normaliser_horing(_HORING)
    assert d is not None
    assert d.tittel.startswith("Høring: Statsbudsjettet 2026")
    assert "?p=104908" in d.url


def test_skriftlig_horing_merkes_som_det() -> None:
    d = stortinget_api.normaliser_horing({**_HORING, "skriftlig": True})
    assert d is not None
    assert d.dokumenttype == "Skriftlig høring"


def test_horing_sammendrag_har_komite_sted_og_frist() -> None:
    """Sted og frist er det som gjør en høring handlingsrelevant."""
    d = stortinget_api.normaliser_horing(_HORING)
    assert d is not None
    assert "Arbeids- og sosialkomiteen" in d.sammendrag
    assert "Høringssal 1" in d.sammendrag
    assert "Innspillsfrist" in d.sammendrag


def test_horing_uten_tilknyttet_sak_hoppes_over() -> None:
    assert stortinget_api.normaliser_horing({**_HORING, "horing_sak_info_liste": []}) is None
    assert stortinget_api.normaliser_horing({"horing_sak_info_liste": []}) is None


# ---------------------------------------------------------------------------
# Foreldreløse dokumenter
# ---------------------------------------------------------------------------
def test_forsvunne_telles_for_api_kilder() -> None:
    """Ekte ID-er skal aldri forsvinne. Gjør de det, har noe skiftet ID."""
    kjente = {("stortinget_sak", "1"): "h1", ("stortinget_sak", "2"): "h2"}
    diff = finn_nye([_dok("3")], {("test", "x"): "h"} | kjente)
    # _dok bruker kilde="test", så stortinget_sak er ikke hentet denne runden
    assert diff.forsvunne == 0, "kilder som ikke ble hentet skal ikke telles"


def test_forsvunne_ignorerer_rullerende_rss() -> None:
    """«Aktuelt» viser bare siste saker — eldre forsvinner naturlig ut av
    feeden og skal ikke telles som tapt."""
    gammel = Dokument(kilde="rss", kilde_id="a", tittel="A", id_er_syntetisk=True)
    kjente = {("rss", "a"): innholdshash(gammel), ("rss", "b"): "borte"}
    ny = Dokument(kilde="rss", kilde_id="a", tittel="A", id_er_syntetisk=True)
    diff = finn_nye([ny], kjente)
    assert diff.forsvunne == 0


# ---------------------------------------------------------------------------
# regjeringen.no
# ---------------------------------------------------------------------------
_REGJERINGEN_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>RSS Regjeringen.no</title>
    <item>
      <title>Fiskeri- og havministeren besøker Brønnøysund</title>
      <link>https://www.regjeringen.no/no/aktuelt/x/id3171341/</link>
      <description>Ho skal møte aktørar innan havbruk.</description>
      <guid>3171341</guid>
      <pubDate>Thu, 17 Sep 2026 11:00:00 +0200</pubDate>
    </item>
  </channel>
</rss>"""


def test_regjeringen_bruker_ekte_guid() -> None:
    """regjeringen.no gir numerisk side-id som guid — stabil og unik."""
    kilde = RssKilde(navn="regjeringen", kildenavn="Regjeringen.no",
                     url="https://example.no/rss")
    docs = rss.parse_feed(_REGJERINGEN_FEED, kilde)
    assert len(docs) == 1
    assert docs[0].kilde_id == "3171341"
    assert docs[0].id_er_syntetisk is False
    assert docs[0].publisert is not None and docs[0].publisert.day == 17
    assert "Brønnøysund" in docs[0].tittel


def test_regjeringen_url_bruker_sti_ikke_parameter() -> None:
    """URL-formatet er en felle som kostet oss en uke.

    Den gamle løsningen brukte `/no/rss/Rss/?id=2581966` — spørrings-
    parameter — og det gir i dag HTTP 404. Riktig format er sti-segment:
    `/no/rss/Rss/2581966/`. Fordi 404-en kom uten Cloudflare-utfordring,
    mens vanlige HTML-sider på regjeringen.no svarer 403 MED Cloudflare,
    så kilden ut som om den var blokkert. Den var bare feiladressert.
    """
    from innhenting.kilder import RSS_KILDER

    reg = [k for k in RSS_KILDER if k.navn == "regjeringen"]
    assert reg, "regjeringen.no må være en aktiv kilde"
    url = reg[0].url
    assert "?id=" not in url, "spørringsparameter gir 404"
    assert url.rstrip("/").endswith("/Rss/2581966"), "feed-id må stå i stien"
