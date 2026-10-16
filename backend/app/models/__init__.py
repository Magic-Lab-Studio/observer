"""SQLAlchemy models for LLM Observatory."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


from app.models.alerts import AlertEvent, AlertRule  # noqa: E402
from app.models.datasets import Dataset, DatasetItem  # noqa: E402
from app.models.evaluation import Evaluation  # noqa: E402
from app.models.ingestion import IngestionReceipt  # noqa: E402
from app.models.trace import Span, Trace  # noqa: E402

__all__ = [
    "Base",
    "Trace",
    "Span",
    "Evaluation",
    "IngestionReceipt",
    "AlertRule",
    "AlertEvent",
    "Dataset",
    "DatasetItem",
]
