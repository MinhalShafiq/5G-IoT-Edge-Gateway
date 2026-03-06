"""Analytics service.

Provides pre-computed anomaly summaries and per-device health scores by
querying the ``alerts`` and ``telemetry_readings`` tables in PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


async def anomaly_summary(
    start_time: datetime,
    end_time: datetime,
    session: AsyncSession,
) -> dict:
    """Return aggregated anomaly counts for the given time window.

    The response dict contains:
    - total_anomalies: int
    - by_device_type: dict[str, int]
    - by_severity: dict[str, int]
    """
    result: dict = {
        "total_anomalies": 0,
        "by_device_type": {},
        "by_severity": {},
    }

    try:
        # Total anomalies in window
        total_q = await session.execute(
            text(
                "SELECT COUNT(*) FROM alerts "
                "WHERE timestamp >= :start AND timestamp <= :end"
            ),
            {"start": start_time, "end": end_time},
        )
        result["total_anomalies"] = total_q.scalar() or 0

        # By device type
        type_q = await session.execute(
            text(
                "SELECT device_type, COUNT(*) FROM alerts "
                "WHERE timestamp >= :start AND timestamp <= :end "
                "GROUP BY device_type"
            ),
            {"start": start_time, "end": end_time},
        )
        result["by_device_type"] = {row[0]: row[1] for row in type_q.fetchall()}

        # By severity
        sev_q = await session.execute(
            text(
                "SELECT severity, COUNT(*) FROM alerts "
                "WHERE timestamp >= :start AND timestamp <= :end "
                "GROUP BY severity"
            ),
            {"start": start_time, "end": end_time},
        )
        result["by_severity"] = {row[0]: row[1] for row in sev_q.fetchall()}

    except Exception:
        logger.warning(
            "anomaly_summary query failed — alerts table may not exist yet",
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )

    return result


async def device_health_scores(session: AsyncSession) -> list[dict]:
    """Compute a health score for each device based on its recent anomaly rate.

    The health score is calculated as:
        1.0 - (anomaly_count / total_readings)

    Only the last 24 hours of data are considered. Devices with no readings
    default to a health score of 1.0 (healthy).

    Returns a list of dicts matching ``DeviceHealthEntry``.
    """
    cutoff = datetime.utcnow() - timedelta(hours=24)
    devices: list[dict] = []

    try:
        # Get reading counts per device
        readings_q = await session.execute(
            text(
                "SELECT device_id, device_type, COUNT(*) "
                "FROM telemetry_readings "
                "WHERE timestamp >= :cutoff "
                "GROUP BY device_id, device_type"
            ),
            {"cutoff": cutoff},
        )
        reading_counts: dict[str, dict] = {}
        for row in readings_q.fetchall():
            reading_counts[str(row[0])] = {
                "device_type": row[1],
                "total_readings": row[2],
            }

        # Get anomaly counts per device
        anomaly_q = await session.execute(
            text(
                "SELECT device_id, COUNT(*) "
                "FROM alerts "
                "WHERE timestamp >= :cutoff "
                "GROUP BY device_id"
            ),
            {"cutoff": cutoff},
        )
        anomaly_counts: dict[str, int] = {
            str(row[0]): row[1] for row in anomaly_q.fetchall()
        }

        # Merge into health scores
        all_device_ids = set(reading_counts.keys()) | set(anomaly_counts.keys())
        for device_id in sorted(all_device_ids):
            info = reading_counts.get(device_id, {})
            total = info.get("total_readings", 0)
            anomalies = anomaly_counts.get(device_id, 0)
            device_type = info.get("device_type", "unknown")

            if total > 0:
                score = max(0.0, 1.0 - (anomalies / total))
            else:
                score = 1.0 if anomalies == 0 else 0.0

            devices.append(
                {
                    "device_id": device_id,
                    "device_type": device_type,
                    "anomaly_count": anomalies,
                    "total_readings": total,
                    "health_score": round(score, 4),
                }
            )

    except Exception:
        logger.warning("device_health_scores query failed — tables may not exist yet")

    return devices
