FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" pydantic \
    "sqlalchemy[asyncio]" asyncpg alembic "celery[redis]" httpx python-dotenv
COPY app ./app
RUN useradd -m -u 10002 erp
USER erp
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
