"""E-postmaler.

Formen er hentet fra varslene den gamle tjenesten sendte: First House-logo
øverst, en tynn rød strek, eyebrow «POLITISK OVERVÅKNING», stikkordet som
overskrift, en merkelapp for type, og deretter ett kort per sak med tittel,
kildelinje og utdrag.

Utformingen beholdes med vilje. Brukerne kjenner den igjen, og gjenkjennelse
er halve tilliten når et verktøy har vært nede en stund.

E-post krever tabellbasert layout og inline stiler. Moderne CSS fungerer ikke
i Outlook, som er det de fleste her leser i.
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Iterable

RØD = "#c8102e"
BLEKK = "#1a1a1a"
GRÅ = "#6b6b6b"
LYSGRÅ = "#f2f2f2"
KANT = "#e0e0e0"
GRØNN = "#1f7a4d"

_SERIF = "Georgia, 'Times New Roman', serif"
_SANS = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"

# Logoen legges ved e-posten som innebygd bilde (CID), ikke lenket fra en URL.
# Outlook blokkerer eksterne bilder som standard, og First House leser i
# Outlook. Et innebygd bilde vises uten at mottakeren må klikke «last ned
# bilder». epost.py legger ved filen når HTML-en refererer til denne ID-en.
LOGO_CID = "fh-logo"
LOGO_FIL = "firsthouse-logo.png"

# Filen er 600 × 70 px; vises i halv størrelse for skarphet på retina.
_LOGO = (
    f'<img src="cid:{LOGO_CID}" width="300" height="35" alt="FIRST HOUSE" '
    f'style="display:block;border:0;outline:none;text-decoration:none;'
    f'width:300px;height:35px;font-family:{_SERIF};font-size:26px;'
    f'letter-spacing:3px;color:{BLEKK};margin:0 0 20px">'
)


def _e(t: Any) -> str:
    return html.escape(str(t or ""), quote=True)


def _dato(d: datetime | None) -> str:
    return f"{d:%d.%m.%Y}" if d else ""


def _kort(dok: dict[str, Any]) -> str:
    """Ett saks-kort. Rød venstrekant, tittel, kildelinje, utdrag."""
    kildelinje = " · ".join(
        x for x in (
            _e(dok.get("kildenavn")),
            _e(dok.get("dokumenttype")),
            _dato(dok.get("publisert")),
        ) if x
    )

    # Hvem spurte hvem — det er ofte det mest informative i et politisk varsel.
    aktorer = ""
    if dok.get("avsender"):
        parti = f" ({_e(dok['parti'])})" if dok.get("parti") else ""
        til = f" til {_e(dok['mottaker'])}" if dok.get("mottaker") else ""
        aktorer = (
            f'<div style="font-family:{_SANS};font-size:13px;color:{BLEKK};'
            f'margin:0 0 8px">{_e(dok["avsender"])}{parti}{til}</div>'
        )

    utdrag = _e(dok.get("sammendrag") or "")
    if len(utdrag) > 320:
        utdrag = utdrag[:320].rsplit(" ", 1)[0] + "…"

    tittel = _e(dok.get("tittel"))
    if dok.get("url"):
        tittel = (
            f'<a href="{_e(dok["url"])}" style="color:{BLEKK};text-decoration:none">'
            f"{tittel}</a>"
        )

    kantfarge = dok.get("_kantfarge") or RØD

    return f"""
    <tr><td style="padding:0 0 14px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="border:1px solid {KANT};border-left:3px solid {kantfarge};
                    border-radius:3px">
        <tr><td style="padding:16px 18px">
          <div style="font-family:{_SANS};font-size:15px;font-weight:600;
                      line-height:1.4;color:{BLEKK};margin:0 0 6px">{tittel}</div>
          <div style="font-family:{_SANS};font-size:12px;color:{GRÅ};
                      margin:0 0 10px">{kildelinje}</div>
          {aktorer}
          <div style="font-family:{_SANS};font-size:14px;line-height:1.55;
                      color:#333">{utdrag}</div>
        </td></tr>
      </table>
    </td></tr>"""


def _ramme(stikkord: str, merkelapp: str, merkefarge: str,
           intro: str, kort: str, antall: int, bunntekst: str,
           enhet: str = "treff") -> str:
    n = f"{antall} {enhet}"
    return f"""<!DOCTYPE html>
<html lang="no"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Politisk overvåkning</title></head>
<body style="margin:0;padding:0;background:{LYSGRÅ}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:{LYSGRÅ};padding:24px 12px">
<tr><td align="center">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0"
         style="max-width:600px;width:100%;background:#fff;border-radius:4px;
                overflow:hidden">
    <tr><td style="height:4px;background:{RØD};font-size:0;line-height:0">&nbsp;</td></tr>
    <tr><td style="padding:32px 32px 0">
      {_LOGO}
      <div style="font-family:{_SANS};font-size:11px;letter-spacing:1.6px;
                  color:{GRÅ};margin:0 0 6px">POLITISK OVERVÅKNING</div>
      <h1 style="font-family:{_SANS};font-size:23px;font-weight:700;
                 line-height:1.3;color:{BLEKK};margin:0 0 12px">{_e(stikkord)}</h1>
      <div style="margin:0 0 22px">
        <span style="font-family:{_SANS};font-size:11px;font-weight:700;
                     letter-spacing:0.8px;color:#fff;background:{merkefarge};
                     padding:4px 9px;border-radius:3px">{merkelapp}</span>
        <span style="font-family:{_SANS};font-size:12px;color:{GRÅ};
                     padding-left:8px">{n}</span>
      </div>
      {intro}
    </td></tr>
    <tr><td style="padding:0 32px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        {kort}
      </table>
    </td></tr>
    <tr><td style="padding:8px 32px 32px">
      <div style="border-top:1px solid {KANT};padding-top:18px;
                  font-family:{_SANS};font-size:12px;line-height:1.6;
                  color:{GRÅ}">{bunntekst}</div>
    </td></tr>
  </table>
</td></tr></table></body></html>"""


def _bunntekst(stikkord: str, basis_url: str) -> str:
    lenke = (
        f'<a href="{_e(basis_url)}" style="color:{GRÅ}">Endre eller slå av varselet</a>'
        if basis_url else "Logg inn for å endre eller slå av varselet"
    )
    return (
        f"Du mottar denne e-posten fordi du overvåker "
        f"<strong>{_e(stikkord)}</strong>.<br>{lenke}"
    )


def _tekstversjon(stikkord: str, dokumenter: Iterable[dict[str, Any]],
                  overskrift: str = "POLITISK OVERVÅKNING") -> str:
    """Ren tekst for lesere som ikke viser HTML, og for konsollmodus."""
    linjer = [f"{overskrift} — {stikkord}", ""]
    for d in dokumenter:
        linjer.append(f"* {d.get('tittel','')}")
        kilde = " · ".join(
            x for x in (d.get("kildenavn"), d.get("dokumenttype"),
                        _dato(d.get("publisert"))) if x
        )
        if kilde:
            linjer.append(f"  {kilde}")
        if d.get("sammendrag") and d.get("_kantfarge"):
            linjer.append(f"  {d['sammendrag']}")
        if d.get("url"):
            linjer.append(f"  {d['url']}")
        linjer.append("")
    return "\n".join(linjer)


def varsel(stikkord: str, dokumenter: list[dict[str, Any]],
           basis_url: str = "") -> tuple[str, str, str]:
    """Løpende varsel. Returnerer (emne, html, tekst)."""
    n = len(dokumenter)
    emne = f"Politisk overvåkning: {n} {'nytt treff' if n == 1 else 'nye treff'} for '{stikkord}'"
    html_ = _ramme(
        stikkord, "NYTT VARSEL", RØD, "",
        "".join(_kort(d) for d in dokumenter), n,
        _bunntekst(stikkord, basis_url),
    )
    return emne, html_, _tekstversjon(stikkord, dokumenter)


def svar(stikkord: str, sporsmal: list[dict[str, Any]],
         basis_url: str = "") -> tuple[str, str, str]:
    """Varsel om at spørsmål du tidligere er varslet om, er besvart.

    Hvert element er en dokumentrad (spørsmålet) med `besvart_av`. Kortene får
    grønn kant og «Besvart av …» som utdrag, slik at de skiller seg tydelig
    fra nye treff når man skummer innboksen.
    """
    kort_data = []
    for s in sporsmal:
        d = dict(s)
        d["_kantfarge"] = GRØNN
        d["sammendrag"] = (
            f"Besvart av {s['besvart_av']}" if s.get("besvart_av") else "Besvart"
        )
        kort_data.append(d)

    n = len(kort_data)
    emne = f"Politisk overvåkning: svar på {n} spørsmål for '{stikkord}'"
    intro = (
        f'<div style="font-family:{_SANS};font-size:14px;line-height:1.6;'
        f'color:{BLEKK};margin:0 0 22px">'
        f"{'Dette spørsmålet' if n == 1 else 'Disse spørsmålene'} har du fått "
        f"varsel om tidligere, og nå har statsråden svart. Trykk på tittelen "
        f"for å lese svaret."
        f"</div>"
    )
    html_ = _ramme(
        stikkord, "SVAR MOTTATT", GRØNN, intro,
        "".join(_kort(d) for d in kort_data), n,
        _bunntekst(stikkord, basis_url), enhet="svar",
    )
    return emne, html_, _tekstversjon(stikkord, kort_data, "SVAR MOTTATT")


def velkomst(stikkord: str, dokumenter: list[dict[str, Any]],
             totalt: int, basis_url: str = "") -> tuple[str, str, str]:
    """Førstegangsmail når et abonnement opprettes.

    Viser et utvalg, ikke alt. Et nytt abonnement på «energi» treffer nesten
    hundre saker i inneværende sesjon — sendes de som enkeltvarsler, er
    verktøyet dødt før det er tatt i bruk.
    """
    n = len(dokumenter)
    emne = f"Politisk overvåkning: oppstartsmail for '{stikkord}' – {totalt} treff"
    mer = (
        f" Vi viser de {n} nyeste her; resten finner du i arkivet."
        if totalt > n else ""
    )
    intro = (
        f'<div style="background:#f4f2fb;border-left:3px solid #5b4bc4;'
        f'padding:14px 16px;border-radius:3px;margin:0 0 22px;'
        f'font-family:{_SANS};font-size:14px;line-height:1.6;color:{BLEKK}">'
        f"Overvåkningen for <strong>{_e(stikkord)}</strong> er nå aktiv. "
        f"Vi fant {totalt} relevante saker i inneværende sesjon.{mer} "
        f"Fremover får du varsel når det kommer noe nytt."
        f"</div>"
    )
    html_ = _ramme(
        stikkord, "VELKOMSTMAIL", "#5b4bc4", intro,
        "".join(_kort(d) for d in dokumenter), totalt,
        _bunntekst(stikkord, basis_url),
    )
    return emne, html_, _tekstversjon(stikkord, dokumenter)


def innlogging(lenke: str) -> tuple[str, str, str]:
    """Innloggingslenke."""
    emne = "Logg inn i Politisk overvåkning"
    html_ = f"""<!DOCTYPE html>
<html lang="no"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:{LYSGRÅ}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:{LYSGRÅ};padding:24px 12px">
<tr><td align="center">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0"
         style="max-width:600px;width:100%;background:#fff;border-radius:4px;
                overflow:hidden">
    <tr><td style="height:4px;background:{RØD};font-size:0;line-height:0">&nbsp;</td></tr>
    <tr><td style="padding:32px">
      {_LOGO}
      <div style="font-family:{_SANS};font-size:11px;letter-spacing:1.6px;
                  color:{GRÅ};margin:0 0 20px">POLITISK OVERVÅKNING</div>
      <p style="font-family:{_SANS};font-size:15px;line-height:1.6;color:{BLEKK};
                margin:0 0 24px">Trykk på knappen for å logge inn.
        Lenken virker i 30 minutter.</p>
      <a href="{_e(lenke)}" style="display:inline-block;background:{RØD};
         color:#fff;font-family:{_SANS};font-size:15px;font-weight:600;
         text-decoration:none;padding:13px 28px;border-radius:3px">Logg inn</a>
      <p style="font-family:{_SANS};font-size:12px;line-height:1.6;color:{GRÅ};
                margin:26px 0 0">Ba du ikke om denne lenken, kan du se bort fra
        e-posten. Ingen får tilgang uten å trykke på den.</p>
    </td></tr>
  </table>
</td></tr></table></body></html>"""
    tekst = f"Logg inn i Politisk overvåkning:\n\n{lenke}\n\nLenken virker i 30 minutter."
    return emne, html_, tekst
    
