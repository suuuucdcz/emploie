FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Port par defaut ; Render impose le sien via $PORT.
ENV PORT=8787
EXPOSE 8787

CMD ["python", "server.py"]
