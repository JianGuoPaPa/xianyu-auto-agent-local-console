FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py XianyuAgent.py XianyuApis.py context_manager.py auto_delivery.py cookie_sync.py monitor_panel.py ./
COPY utils/ utils/
COPY prompts/*_example.txt prompts/
COPY .env.example .env.example

RUN mkdir -p data logs prompts

CMD ["python", "main.py"]

