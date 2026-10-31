FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip && python -m pip install --no-cache-dir -r /app/requirements.txt
COPY bot.py /app/bot.py
CMD ["python", "-u", "/app/bot.py"]
