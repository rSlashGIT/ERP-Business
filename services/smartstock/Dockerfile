FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml ./
RUN pip install --no-cache-dir numpy fastapi "uvicorn[standard]" pydantic
COPY smartstock ./smartstock
RUN useradd -m -u 10001 app && mkdir -p /var/lib/smartstock && chown -R app /var/lib/smartstock
USER app
EXPOSE 8100
CMD ["uvicorn", "smartstock.api.main:app", "--host", "0.0.0.0", "--port", "8100"]
