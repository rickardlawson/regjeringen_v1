# Webgrensesnittet — egen Railway-tjeneste ved siden av cron-jobbene.
# Krasjer denne, fortsetter innhentingen. Og når First House flyttes inn i
# Signalist, slettes denne tjenesten mens dokumentlageret blir stående.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

# Én worker holder til 30 brukere, og holder minnebruken lav.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 2 --timeout 60 web.app:app"]
