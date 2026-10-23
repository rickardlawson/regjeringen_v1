# Innhentingen er en batch-jobb, ikke en webtjeneste.
# Chromium (via Playwright) er med fordi regjeringen.no ligger bak Cloudflare
# og blokkerer vanlige forespørsler fra Railway. DIO-redaksjonen anbefaler
# headless browser for budsjettspørsmålene. Alt annet hentes fortsatt via HTTP.
# Låst til bookworm (Debian 12): Playwright 1.52 støtter ikke trixie.
FROM python:3.12-slim-bookworm

WORKDIR /app

# Nettleseren legges utenfor root sin hjemmemappe, så app-brukeren når den.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Avhengigheter først, så laget caches mellom kodeendringer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium \
    && chmod -R a+rx /ms-playwright

COPY . .

# Ingen buffering — Railway-loggen skal vise fremdrift underveis, ikke først
# når jobben er ferdig.
ENV PYTHONUNBUFFERED=1

# Kjør som ikke-root.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

# Cron-jobb: gjør jobben, avslutt. Railway starter containeren på timeplan.
# Kjøres den som en alltid-på-tjeneste, avslutter den umiddelbart — det er
# meningen.
CMD ["python3", "hent.py"]
