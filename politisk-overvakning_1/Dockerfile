# Slank container — innhentingen er en batch-jobb, ikke en webtjeneste.
# Ingen Playwright, ingen browser: alt hentes via HTTP.
FROM python:3.12-slim

WORKDIR /app

# Avhengigheter først, så laget caches mellom kodeendringer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
