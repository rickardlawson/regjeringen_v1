"""Tester for varslingslaget.

Tyngdepunktet ligger på duplikatvern og tilgangskontroll. Det er de to tingene
som ødelegger tilliten til et varslingsverktøy raskest.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.brukere import (  # noqa: E402
    STANDARD_DOMENER, UgyldigEpost, normaliser_epost, tillatte_domener,
)
from varsling import maler  # noqa: E402
from varsling.epost import Epost, EpostFeil, er_konfigurert, send  # noqa: E402


# ── Tilgangsstyring ──────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "inn,ut",
    [
        ("vmg@firsthouse.no", "vmg@firsthouse.no"),
        ("  VMG@FirstHouse.NO  ", "vmg@firsthouse.no"),
        ("Steinar.Flaa@firsthouse.no", "steinar.flaa@firsthouse.no"),
    ],
)
def test_gyldige_adresser_normaliseres(inn: str, ut: str) -> None:
    assert normaliser_epost(inn) == ut


@pytest.mark.parametrize(
    "epost",
    [
        "noen@gmail.com",
        "angriper@firsthouse.no.evil.com",
        "@firsthouse.no",
        "uten-krøllalfa",
        "",
        None,
    ],
)
def test_adresser_utenfor_domenet_avvises(epost) -> None:
    """Domenet ER tilgangsstyringen. Det finnes ingen brukerliste å vedlikeholde.

    Merk at `firsthouse.no.evil.com` må avvises — en enkel `endswith`-sjekk
    ville sluppet den gjennom.
    """
    with pytest.raises(UgyldigEpost):
        normaliser_epost(epost)


def test_uppercase_slipper_inn_som_driftsansvarlig() -> None:
    """Uppercase drifter tjenesten og må kunne teste og feilsøke.

    Ingen ser andres abonnement uansett, så det gir ikke innsyn i First House
    sine overvåkninger — men driftstilgangen bør stå i databehandleravtalen.
    """
    assert normaliser_epost("rickard@uppercase.no") == "rickard@uppercase.no"
    assert "uppercase.no" in STANDARD_DOMENER


def test_domener_kan_settes_med_miljovariabel(monkeypatch) -> None:
    monkeypatch.setenv("TILLATTE_DOMENER", "kunde.no, @annen.no")
    assert tillatte_domener() == ("kunde.no", "annen.no")
    assert normaliser_epost("a@kunde.no") == "a@kunde.no"
    with pytest.raises(UgyldigEpost):
        normaliser_epost("a@firsthouse.no")


def test_tom_miljovariabel_faller_tilbake_til_standard(monkeypatch) -> None:
    """En tom variabel skal ikke låse alle ute."""
    monkeypatch.setenv("TILLATTE_DOMENER", "   ")
    assert tillatte_domener() == STANDARD_DOMENER


# ── E-postleverandør ─────────────────────────────────────────────────────
def test_standard_er_konsoll_ikke_utsending(monkeypatch) -> None:
    """Et feilkonfigurert miljø skal logge, ikke sende ut noe uventet."""
    monkeypatch.delenv("EPOST_LEVERANDOR", raising=False)
    assert send(Epost(til="x@firsthouse.no", emne="Test", html="<p>hei</p>")) == "konsoll"
    assert er_konfigurert() is False


def test_resend_uten_nokkel_feiler_tydelig(monkeypatch) -> None:
    monkeypatch.setenv("EPOST_LEVERANDOR", "resend")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(EpostFeil, match="RESEND_API_KEY"):
        send(Epost(til="x@firsthouse.no", emne="Test", html="<p>hei</p>"))


def test_ukjent_leverandor_feiler(monkeypatch) -> None:
    monkeypatch.setenv("EPOST_LEVERANDOR", "tullball")
    with pytest.raises(EpostFeil):
        send(Epost(til="x@firsthouse.no", emne="T", html="<p>h</p>"))


def test_er_konfigurert_krever_bade_leverandor_og_nokkel(monkeypatch) -> None:
    monkeypatch.setenv("EPOST_LEVERANDOR", "resend")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert er_konfigurert() is False
    monkeypatch.setenv("RESEND_API_KEY", "re_xxx")
    assert er_konfigurert() is True


# ── Maler ────────────────────────────────────────────────────────────────
_DOK = {
    "id": 1,
    "tittel": "Skriftlig spørsmål om havbruk",
    "sammendrag": "Om grunnrenteskatt.",
    "kildenavn": "Stortinget: Skriftlige spørsmål",
    "dokumenttype": "Skriftlig spørsmål",
    "url": "https://www.stortinget.no/x",
    "publisert": datetime(2026, 8, 20, tzinfo=timezone.utc),
    "avsender": "Bengt Rune Strifeldt",
    "parti": "Fremskrittspartiet",
    "mottaker": "fiskeri- og havministeren",
}


def test_varsel_har_emne_med_antall_og_stikkord() -> None:
    emne, html, tekst = maler.varsel("havbruk", [_DOK])
    assert "1 nytt treff" in emne and "havbruk" in emne
    assert "FIRST HOUSE" in html
    assert "Skriftlig spørsmål om havbruk" in html
    assert tekst.strip()


def test_flertall_i_emnet() -> None:
    emne, _, _ = maler.varsel("havbruk", [_DOK, dict(_DOK, id=2)])
    assert "2 nye treff" in emne


def test_velkomst_viser_totalen_ikke_bare_utvalget() -> None:
    """Viser 3, men sier at det finnes 58. Ellers tror brukeren arkivet er tomt."""
    emne, html, _ = maler.velkomst("havbruk", [_DOK] * 3, totalt=58)
    assert "58" in emne
    assert "58 relevante saker" in html
    assert "VELKOMSTMAIL" in html


def test_maler_rømmer_html_i_brukerdata() -> None:
    """Stikkord kommer fra brukeren og havner rett i e-posten."""
    _, html, _ = maler.varsel("<script>alert(1)</script>", [_DOK])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_innloggingsmal_har_lenken_og_ingen_lekkasje() -> None:
    _, html, tekst = maler.innlogging("https://x.no/lenke?token=hemmelig")
    assert "token=hemmelig" in html and "token=hemmelig" in tekst
    assert "30 minutter" in html


def test_lang_beskrivelse_forkortes() -> None:
    lang = dict(_DOK, sammendrag="ord " * 300)
    _, html, _ = maler.varsel("test", [lang])
    assert "…" in html


# ── Duplikatvern ─────────────────────────────────────────────────────────
def test_velkomst_kvitterer_ut_alt_ikke_bare_det_viste() -> None:
    """Uten dette får brukeren tre e-poster på rad ved nytt abonnement.

    Velkomstmailen viser 15 av f.eks. 58 treff. De 43 andre må kvitteres ut
    likevel, ellers går de ut som «nye varsler» i neste kjøring — med saker
    fra i vår.
    """
    from varsling import utsending

    vist = [dict(_DOK, id=i) for i in range(1, 16)]
    alle = [dict(_DOK, id=i) for i in range(1, 59)]
    kvittert: list[int] = []

    with patch.object(utsending.brukere, "usendte_treff",
                      side_effect=[vist, alle]), \
         patch.object(utsending.brukere, "marker_sendt",
                      side_effect=lambda ab, ider: kvittert.extend(ider)), \
         patch.object(utsending.brukere, "marker_velkomst_sendt"), \
         patch.object(utsending.brukere, "logg_utsending"), \
         patch.object(utsending.epostmodul, "send", return_value="ok"):
        utfall = utsending.behandle_abonnement(
            {"id": 1, "stikkord": "havbruk", "epost": "x@firsthouse.no",
             "velkomst_sendt": None}
        )

    assert utfall == "velkomst"
    assert len(kvittert) == 58, "alle treff må kvitteres ut, ikke bare de viste"


def test_kvitterer_ikke_ut_ved_feilet_utsending() -> None:
    """Feiler e-posten, skal varselet forsøkes på nytt — ikke tapes i stillhet."""
    from varsling import utsending

    kvittert: list[int] = []
    with patch.object(utsending.brukere, "usendte_treff", return_value=[_DOK]), \
         patch.object(utsending.brukere, "marker_sendt",
                      side_effect=lambda ab, ider: kvittert.extend(ider)), \
         patch.object(utsending.brukere, "logg_utsending"), \
         patch.object(utsending.epostmodul, "send",
                      side_effect=EpostFeil("Resend nede")):
        utfall = utsending.behandle_abonnement(
            {"id": 1, "stikkord": "havbruk", "epost": "x@firsthouse.no",
             "velkomst_sendt": datetime.now(timezone.utc)}
        )

    assert utfall == "feil"
    assert kvittert == [], "ingenting skal kvitteres ut når sendingen feilet"


def test_ingen_treff_gir_ingen_epost() -> None:
    from varsling import utsending

    sendte = MagicMock()
    with patch.object(utsending.brukere, "usendte_treff", return_value=[]), \
         patch.object(utsending.brukere, "marker_velkomst_sendt"), \
         patch.object(utsending.epostmodul, "send", sendte):
        utfall = utsending.behandle_abonnement(
            {"id": 1, "stikkord": "havbruk", "epost": "x@firsthouse.no",
             "velkomst_sendt": None}
        )

    assert utfall == "tom"
    sendte.assert_not_called()


def test_ugyldig_stikkord_stopper_ikke_de_andre() -> None:
    """Ett ødelagt abonnement skal ikke velte hele utsendingen."""
    from matching.sporring import TomtSok
    from varsling import utsending

    def treff(ab_id, stikkord, grense=50):
        if stikkord == "!!!":
            raise TomtSok("Fant ingen søkbare ord")
        return [_DOK]

    with patch.object(utsending.brukere, "aktive_abonnement", return_value=[
        {"id": 1, "stikkord": "!!!", "epost": "a@firsthouse.no", "velkomst_sendt": None},
        {"id": 2, "stikkord": "havbruk", "epost": "b@firsthouse.no", "velkomst_sendt": None},
    ]), \
         patch.object(utsending.brukere, "usendte_treff", side_effect=treff), \
         patch.object(utsending.brukere, "marker_sendt"), \
         patch.object(utsending.brukere, "marker_velkomst_sendt"), \
         patch.object(utsending.brukere, "logg_utsending"), \
         patch.object(utsending.epostmodul, "send", return_value="ok"):
        res = utsending.send_alle()

    assert res.feilet == 1, "det ugyldige abonnementet skal telles som feilet"
    assert res.velkomster == 1, "det gyldige skal gå gjennom likevel"


# ── Logging ──────────────────────────────────────────────────────────────
def test_web_konfigurerer_logging_ved_import() -> None:
    """Logging må settes opp ved import, ikke inne i __main__.

    Under gunicorn er __name__ ikke "__main__", så en basicConfig der nede
    kjører aldri. Uten handler dropper Python alt under WARNING — og da
    forsvinner både konsoll-e-postene og tracebackene fra 500-handleren i
    stillhet. Appen ser ut til å virke, men er blind.

    Dette traff i produksjon: innloggingslenken kom aldri i Railway-loggen,
    og en skjult 500-feil ble først synlig etter at loggingen ble fikset.
    """
    import logging as _logging
    from pathlib import Path

    kilde = (Path(__file__).resolve().parents[1] / "web" / "app.py").read_text(
        encoding="utf-8"
    )
    hoveddel, _, main_blokk = kilde.partition('if __name__ == "__main__":')
    assert "logging.basicConfig" in hoveddel, "basicConfig må stå på modulnivå"
    assert "logging.basicConfig" not in main_blokk, "ikke bare i __main__"

    import web.app  # noqa: F401  — importen skal ha satt opp en handler
    assert _logging.getLogger().handlers, "rot-loggeren mangler handler"
