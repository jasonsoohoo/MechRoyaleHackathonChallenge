FROM python:3.13-slim@sha256:48d884813cf44e927cf64c5529dd630dd3b18338c6dc727991eef9b71e934a20
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /bot
COPY bot.py mechfight.py arena.py navigation.py tactics.py policy.json ./
USER 65534:65534
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
