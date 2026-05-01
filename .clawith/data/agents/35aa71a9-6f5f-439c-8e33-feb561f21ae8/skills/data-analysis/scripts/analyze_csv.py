#!/usr/bin/env python3
"""Utility for quick CSV data analysis."""

import csv
import statistics
from collections import Counter


def analyze_column(data: list[dict], column: str) -> dict:
    """Analyze a single column from CSV data."""
    values = [row.get(column) for row in data if row.get(column) is not None]
    if not values:
        return {"column": column, "count": 0, "error": "No data"}

    result = {"column": column, "count": len(values), "unique": len(set(values))}

    # Try numeric analysis
    try:
        nums = [float(v) for v in values]
        result.update({
            "type": "numeric",
            "min": min(nums), "max": max(nums),
            "mean": round(statistics.mean(nums), 2),
            "median": round(statistics.median(nums), 2),
        })
    except (ValueError, TypeError):
        freq = Counter(values).most_common(5)
        result.update({"type": "categorical", "top_values": freq})

    return result


def quick_summary(filepath: str) -> str:
    """Generate a quick summary of a CSV file."""
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        data = list(reader)
    columns = data[0].keys() if data else []
    return f'Rows: {len(data)}, Columns: {len(columns)}'
