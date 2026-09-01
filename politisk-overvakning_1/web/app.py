"""Webgrensesnitt for Politisk overvåkning.

Tre skjermbilder: mine varsler, nytt varsel, søk i arkivet.

Innlogging skjer med engangslenke på e-post. Ingen passord lagres, så det
finnes ingen passord å lekke — og siden e-postutsending uansett er kjernen i
tjenesten, koster det ingen ny avhengighet. Tilgang krever @firsthouse.no.

Kjøres som EGEN Railway-tjeneste ved siden av cron-jobben, mot samme database.
Krasjer denne, fortsetter innhentingen. Og når First House flyttes inn i
Signalist, slettes denne tjenesten mens dokumentlageret blir stående.
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus
from functools import wraps

from flask import (
    Flask, abort, flash, redirect, render_template, request, session, url_for,
)

from db import brukere, lager
from matching.sporring import TomtSok, beskriv
from varsling import epost as epostmodul
from varsling import maler

logger = logging.getLogger(__name__)

# Logging må settes opp ved IMPORT, ikke inne i __main__.
#
# Under gunicorn er __name__ ikke "__main__", så en basicConfig der nede
# kjører aldri. Uten handler dropper Python alt under WARNING — og da
# forsvinner både konsoll-e-postene og tracebackene fra 500-handleren i
# stillhet. Appen ser ut til å virke, men er blind.
logging.basicConfig(
    level=os.environ.get("LOGGNIVA", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SIKKER_COOKIE", "1") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

# Enkel ratebegrensning på innlogging. Uten den kan hvem som helst spamme
# e-postboksen til en kollega ved å be om lenker i loop.
_forsok: dict[str, list[datetime]] = {}
_MAKS_FORSOK = 5
_VINDU = timedelta(minutes=15)


def _for_mange_forsok(nokkel: str) -> bool:
    na = datetime.now(timezone.utc)
    tidligere = [t for t in _forsok.get(nokkel, []) if na - t < _VINDU]
    _forsok[nokkel] = tidligere + [na]
    if len(_forsok) > 500:  # enkel opprydding
        for k in [k for k, v in _forsok.items() if not any(na - t < _VINDU for t in v)]:
            _forsok.pop(k, None)
    return len(tidligere) >= _MAKS_FORSOK


def _trygg_sti(sti: str) -> str:
    """Godta kun interne stier som viderekobling.

    Uten denne kan hvem som helst lage en innloggingslenke som sender
    brukeren videre til sin egen side etter pålogging — klassisk åpen
    viderekobling, og et fint verktøy for phishing.
    """
    if not sti.startswith("/") or sti.startswith("//") or "\\" in sti:
        return ""
    return sti


def _domenetekst() -> str:
    """Hvilke domener som slipper inn, til visning i skjemaet."""
    d = brukere.tillatte_domener()
    if len(d) == 1:
        return f"Bare @{d[0]}-adresser har tilgang"
    return "Adresser på " + " eller ".join(f"@{x}" for x in d) + " har tilgang"


def krev_innlogging(f):
    @wraps(f)
    def innpakket(*a, **kw):
        bruker_id = session.get("bruker_id")
        if not bruker_id:
            # full_path, ikke path: ellers mister vi spørringen. En delt
            # varsellenke (/nytt?stikkord=...) ville da sendt mottakeren til
            # et tomt skjema etter innlogging, og delingen var bortkastet.
            neste = request.full_path.rstrip("?")
            return redirect(url_for("logg_inn", neste=neste))
        bruker = brukere.hent_bruker(bruker_id)
        if not bruker:
            session.clear()
            return redirect(url_for("logg_inn"))
        request.bruker = bruker
        return f(*a, **kw)
    return innpakket


# ── Innlogging ───────────────────────────────────────────────────────────
@app.route("/logg-inn", methods=["GET", "POST"])
def logg_inn():
    if session.get("bruker_id"):
        return redirect(url_for("mine_varsler"))

    # Hvor brukeren skulle. Må bæres hele veien gjennom skjemaet og inn i
    # e-postlenken, ellers mister en delt varsellenke stikkordet sitt.
    neste = _trygg_sti(request.values.get("neste", ""))

    if request.method == "POST":
        epost_inn = (request.form.get("epost") or "").strip()
        if _for_mange_forsok(epost_inn.lower() or request.remote_addr or "?"):
            flash("For mange forsøk. Vent et kvarter og prøv igjen.", "feil")
            return render_template("logg_inn.html", domenetekst=_domenetekst(),
                                   neste=neste)
        try:
            token, adresse = brukere.lag_innloggingslenke(epost_inn)
        except brukere.UgyldigEpost as exc:
            flash(str(exc), "feil")
            return render_template("logg_inn.html", domenetekst=_domenetekst(),
                                   neste=neste)

        basis = os.environ.get("BASIS_URL", request.url_root).rstrip("/")
        lenke = f"{basis}{url_for('lenke_innlogging')}?token={token}"
        if neste:
            lenke += f"&neste={quote_plus(neste)}"
        emne, html, tekst = maler.innlogging(lenke)
        try:
            epostmodul.send(epostmodul.Epost(til=adresse, emne=emne, html=html, tekst=tekst))
        except epostmodul.EpostFeil as exc:
            logger.error("Innloggingsmail til %s feilet: %s", adresse, exc)
            flash("Klarte ikke sende e-posten. Prøv igjen om litt.", "feil")
            return render_template("logg_inn.html", domenetekst=_domenetekst(),
                                   neste=neste)
        return render_template("lenke_sendt.html", epost=adresse)

    return render_template("logg_inn.html", domenetekst=_domenetekst(), neste=neste)


@app.route("/lenke")
def lenke_innlogging():
    bruker_id = brukere.los_inn_lenke(request.args.get("token", ""))
    if not bruker_id:
        flash("Lenken er utløpt. Skriv inn adressen din, så sender vi en ny.", "feil")
        return redirect(url_for("logg_inn"))
    session.clear()
    session["bruker_id"] = bruker_id
    session.permanent = True
    neste = _trygg_sti(request.args.get("neste", ""))
    return redirect(neste or url_for("mine_varsler"))


@app.route("/logg-ut", methods=["POST"])
def logg_ut():
    session.clear()
    return redirect(url_for("logg_inn"))


# ── Skjermbilde 1: mine varsler ──────────────────────────────────────────
@app.route("/")
@krev_innlogging
def mine_varsler():
    basis = os.environ.get("BASIS_URL", request.url_root).rstrip("/")
    abonnementer = brukere.hent_abonnement(request.bruker["id"])
    for ab in abonnementer:
        try:
            ab["treff_totalt"] = lager.tell_treff(ab["stikkord"])
        except TomtSok:
            ab["treff_totalt"] = 0
        ab["visning"] = beskriv(ab["stikkord"])
        # Delelenke: mottakeren får stikkordet ferdig utfylt og lagrer sitt
        # EGET abonnement. Vi melder aldri andre på — da hadde de fått e-post
        # de ikke ba om, og som de ikke kunne slå av selv.
        ab["delelenke"] = (
            f"{basis}{url_for('nytt_varsel')}"
            f"?stikkord={quote_plus(ab['stikkord'])}"
        )
    return render_template(
        "mine_varsler.html",
        abonnementer=abonnementer,
        bruker=request.bruker,
        epost_aktiv=epostmodul.er_konfigurert(),
    )


# ── Skjermbilde 2: nytt varsel ───────────────────────────────────────────
@app.route("/nytt", methods=["GET", "POST"])
@krev_innlogging
def nytt_varsel():
    stikkord = (request.form.get("stikkord") or request.args.get("stikkord") or "").strip()
    forhandsvisning = None
    antall = None

    if stikkord:
        try:
            antall = lager.tell_treff(stikkord)
            forhandsvisning = lager.sok(stikkord, grense=5)
        except TomtSok as exc:
            flash(str(exc), "feil")
            stikkord = ""

    # «Vis treff» forhåndsviser; «Lagre» oppretter. To knapper, ett skjema —
    # så man ser hva man får før man abonnerer.
    if request.method == "POST" and request.form.get("handling") == "lagre" and stikkord:
        try:
            brukere.opprett_abonnement(request.bruker["id"], stikkord)
        except (ValueError, TomtSok) as exc:
            flash(str(exc), "feil")
        else:
            flash(f"Varsel opprettet for «{beskriv(stikkord)}».", "ok")
            return redirect(url_for("mine_varsler"))

    return render_template(
        "nytt_varsel.html",
        stikkord=stikkord,
        antall=antall,
        forhandsvisning=forhandsvisning,
        # Kom stikkordet fra en delt lenke? Da trenger mottakeren å vite
        # hvorfor feltet er utfylt, og at han lager sitt eget varsel.
        fra_deling=bool(request.args.get("stikkord")) and request.method == "GET",
    )


@app.route("/slett/<int:abonnement_id>", methods=["POST"])
@krev_innlogging
def slett(abonnement_id: int):
    if brukere.slett_abonnement(request.bruker["id"], abonnement_id):
        flash("Varselet er slått av.", "ok")
    else:
        abort(404)
    return redirect(url_for("mine_varsler"))


# ── Skjermbilde 3: søk i arkivet ─────────────────────────────────────────
@app.route("/sok")
@krev_innlogging
def sok():
    q = (request.args.get("q") or "").strip()
    treff, antall = [], None
    if q:
        try:
            antall = lager.tell_treff(q)
            treff = lager.sok(q, grense=50)
        except TomtSok as exc:
            flash(str(exc), "feil")
    return render_template("sok.html", q=q, treff=treff, antall=antall)


@app.route("/helse")
def helse():
    return {"status": "ok"}, 200


@app.errorhandler(404)
def ikke_funnet(e):
    return render_template("feil.html", kode=404,
                           melding="Siden finnes ikke."), 404


@app.errorhandler(500)
def serverfeil(e):
    logger.exception("Uventet feil: %s", e)
    return render_template("feil.html", kode=500,
                           melding="Noe gikk galt. Prøv igjen."), 500


def _boot() -> None:
    """Oppstart. Kjøres ved IMPORT, ikke bare under __main__.

    Under gunicorn er `__name__` ikke `"__main__"`, så alt som ligger nederst
    i fila kjører aldri. Det har bitt oss to ganger:

      * logging.basicConfig lå der — appen var blind i produksjon
      * init_skjema() lå der — webtjenesten møtte et gammelt skjema og ga
        500 på innlogging til en cron-jobb tilfeldigvis hadde kjørt først

    Skjemaet er idempotent (all DDL er IF NOT EXISTS), så det koster noen
    millisekunder per oppstart og fjerner en hel klasse av feil.

    Feiler det, skal appen likevel starte: da svarer /helse fortsatt, Railway
    river ikke ned containeren, og feilen står i loggen i stedet for å bli en
    crash loop.
    """
    try:
        lager.init_skjema()
    except Exception as exc:
        logger.exception("init_skjema() feilet ved oppstart: %s", exc)


_boot()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
