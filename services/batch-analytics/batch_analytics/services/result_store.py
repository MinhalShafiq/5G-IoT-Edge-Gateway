"""In-memory result store for Spark job outputs.

Stores the full result dictionaries returned by Spark jobs and provides
retrieval and CSV export functionality.
"""

from __future__ import annotations

import csv
import io


class ResultStore:
    """Thread-safe in-memory store for job results."""

    def __init__(self) -> None:
        self._results: dict[str, dict] = {}

    def store(self, job_id: str, results: dict) -> None:
        """Store the full results for a completed job.

        Args:
            job_id: Unique job identifier.
            results: The result dictionary returned by the spark job.
        """
        self._results[job_id] = results

    def get(self, job_id: str) -> dict | None:
        """Retrieve results for a job, or None if not found.

        Args:
            job_id: Unique job identifier.

        Returns:
            The stored result dictionary, or None.
        """
        return self._results.get(job_id)

    def to_csv(self, job_id: str) -> str | None:
        """Convert job results to a CSV string.

        Looks for a ``rows`` key in the result dictionary. Each row is
        expected to be a dict with uniform keys. Falls back to writing
        the top-level summary keys as a single-row CSV if ``rows`` is
        not present.

        Args:
            job_id: Unique job identifier.

        Returns:
            A CSV-formatted string, or None if no results exist.
        """
        results = self._results.get(job_id)
        if results is None:
            return None

        output = io.StringIO()

        # Try to use the "rows" key for tabular data
        rows = results.get("rows")
        if rows and isinstance(rows, list) and len(rows) > 0:
            if isinstance(rows[0], dict):
                fieldnames = list(rows[0].keys())
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
            else:
                # Flat list of values — write as a single column
                writer = csv.writer(output)
                writer.writerow(["value"])
                for row in rows:
                    writer.writerow([row])
        else:
            # Fall back: write the summary as a single-row CSV
            summary = results.get("summary", results)
            if isinstance(summary, dict):
                fieldnames = list(summary.keys())
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerow(summary)
            else:
                writer = csv.writer(output)
                writer.writerow(["result"])
                writer.writerow([str(summary)])

        return output.getvalue()

    def delete(self, job_id: str) -> bool:
        """Remove stored results for a job.

        Args:
            job_id: Unique job identifier.

        Returns:
            True if results were found and deleted, False otherwise.
        """
        if job_id in self._results:
            del self._results[job_id]
            return True
        return False
