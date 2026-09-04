"""Dataset specification for research experiments.

Defines the data used for training and testing, ensuring:
- No future data leaks into training
- Test data never influences parameters
- Train/test splits are explicit and reproducible
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class DatasetSpec:
    """Specification for a research dataset.

    Attributes:
        venue: Exchange venue (e.g., "kucoin")
        symbol: Trading pair (e.g., "ERG/USDT")
        timeframe: Candle timeframe (e.g., "1h")
        train_start: Training period start (YYYY-MM-DD)
        train_end: Training period end (YYYY-MM-DD)
        test_start: Test period start (YYYY-MM-DD)
        test_end: Test period end (YYYY-MM-DD)
        n_folds: Number of walk-forward folds
        id: Unique identifier
    """
    venue: str
    symbol: str
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    timeframe: str = "1h"
    n_folds: int = 5
    id: str = field(default_factory=lambda: f"ds_{uuid.uuid4().hex[:8]}")

    def validate(self) -> Optional[str]:
        """Validate the dataset specification.

        Returns error message if invalid, None if valid.
        """
        # Check for future data leak: train must end before test starts
        if self.train_end >= self.test_start:
            return f"Future data leak: train_end ({self.train_end}) >= test_start ({self.test_start})"

        # Check for gaps or overlaps
        if self.train_start >= self.train_end:
            return f"Invalid train period: start ({self.train_start}) >= end ({self.train_end})"

        if self.test_start >= self.test_end:
            return f"Invalid test period: start ({self.test_start}) >= end ({self.test_end})"

        if self.n_folds < 2:
            return f"Need at least 2 folds, got {self.n_folds}"

        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "venue": self.venue,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
            "n_folds": self.n_folds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DatasetSpec:
        """Deserialize from dictionary."""
        return cls(
            venue=data["venue"],
            symbol=data["symbol"],
            train_start=data["train_start"],
            train_end=data["train_end"],
            test_start=data["test_start"],
            test_end=data["test_end"],
            timeframe=data.get("timeframe", "1h"),
            n_folds=data.get("n_folds", 5),
            id=data.get("id", f"ds_{uuid.uuid4().hex[:8]}"),
        )