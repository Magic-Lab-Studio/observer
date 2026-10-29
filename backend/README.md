# LLM Observatory Backend

FastAPI backend for LLM Observatory - Open-source LLM observability.

## Installation

```bash
pip install -e ".[dev,sqlite]"
```

## Running

```bash
# Start PostgreSQL
podman run -d --name postgres -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=observatory postgres:16

# Run migrations
alembic upgrade head

# Start server
uvicorn app.main:app --reload
```

## API Documentation

Once running, visit:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

Both are disabled when `OBSERVATORY_API_KEY` is set.

## License

Apache License 2.0
