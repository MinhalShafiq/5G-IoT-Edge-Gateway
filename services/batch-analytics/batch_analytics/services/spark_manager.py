"""SparkSession lifecycle management.

Manages a singleton PySpark SparkSession with configurable master URL,
driver/executor memory, and JDBC connectivity to PostgreSQL.
"""

from __future__ import annotations

import structlog
from pyspark.sql import SparkSession, DataFrame

from batch_analytics.config import Settings

logger = structlog.get_logger(__name__)


class SparkManager:
    """Manages the PySpark SparkSession lifecycle and database I/O."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session: SparkSession | None = None

    @property
    def postgres_jdbc_url(self) -> str:
        """Convert the async DSN to a JDBC URL for Spark.

        The shared config uses ``postgresql+asyncpg://...`` but Spark needs
        a standard JDBC URL like ``jdbc:postgresql://host:port/db``.
        """
        dsn = self._settings.postgres_dsn
        # Strip the SQLAlchemy driver prefix
        # "postgresql+asyncpg://user:pass@host:5432/db" -> "user:pass@host:5432/db"
        if "://" in dsn:
            remainder = dsn.split("://", 1)[1]
        else:
            remainder = dsn
        return f"jdbc:postgresql://{remainder}"

    @property
    def postgres_properties(self) -> dict[str, str]:
        """Extract JDBC connection properties from the DSN."""
        dsn = self._settings.postgres_dsn
        props: dict[str, str] = {"driver": "org.postgresql.Driver"}

        if "://" in dsn:
            remainder = dsn.split("://", 1)[1]
        else:
            remainder = dsn

        # Parse user:pass@host:port/db
        if "@" in remainder:
            userinfo, _hostinfo = remainder.split("@", 1)
            if ":" in userinfo:
                props["user"], props["password"] = userinfo.split(":", 1)
            else:
                props["user"] = userinfo

        return props

    @property
    def postgres_jdbc_url_clean(self) -> str:
        """JDBC URL without credentials (host:port/db only)."""
        dsn = self._settings.postgres_dsn
        if "://" in dsn:
            remainder = dsn.split("://", 1)[1]
        else:
            remainder = dsn

        if "@" in remainder:
            hostinfo = remainder.split("@", 1)[1]
        else:
            hostinfo = remainder

        return f"jdbc:postgresql://{hostinfo}"

    def get_or_create_session(self) -> SparkSession:
        """Return the existing SparkSession or create a new one."""
        if self._session is None:
            logger.info(
                "creating spark session",
                master=self._settings.spark_master,
                app_name=self._settings.spark_app_name,
            )
            self._session = (
                SparkSession.builder
                .master(self._settings.spark_master)
                .appName(self._settings.spark_app_name)
                .config("spark.driver.memory", self._settings.spark_driver_memory)
                .config("spark.executor.memory", self._settings.spark_executor_memory)
                .config(
                    "spark.jars.packages",
                    "org.postgresql:postgresql:42.6.0",
                )
                .config("spark.sql.adaptive.enabled", "true")
                .config("spark.sql.shuffle.partitions", "8")
                .getOrCreate()
            )
            logger.info("spark session created")
        return self._session

    def read_from_postgres(
        self,
        table_name: str,
        query: str | None = None,
    ) -> DataFrame:
        """Read a table or query from PostgreSQL into a Spark DataFrame.

        Args:
            table_name: The database table to read.
            query: Optional SQL query to push down to the database.
                   If provided, this is used instead of the table name.

        Returns:
            A PySpark DataFrame with the data.
        """
        session = self.get_or_create_session()
        jdbc_url = self.postgres_jdbc_url_clean
        props = self.postgres_properties

        dbtable = f"({query}) AS subquery" if query else table_name

        logger.info("reading from postgres", table=table_name, has_query=query is not None)

        return (
            session.read
            .format("jdbc")
            .option("url", jdbc_url)
            .option("dbtable", dbtable)
            .option("user", props.get("user", ""))
            .option("password", props.get("password", ""))
            .option("driver", props.get("driver", "org.postgresql.Driver"))
            .load()
        )

    def write_to_postgres(
        self,
        df: DataFrame,
        table_name: str,
        mode: str = "append",
    ) -> None:
        """Write a Spark DataFrame to PostgreSQL.

        Args:
            df: The PySpark DataFrame to write.
            table_name: Target table name.
            mode: Write mode — 'append', 'overwrite', 'ignore', or 'error'.
        """
        jdbc_url = self.postgres_jdbc_url_clean
        props = self.postgres_properties

        logger.info("writing to postgres", table=table_name, mode=mode)

        (
            df.write
            .format("jdbc")
            .option("url", jdbc_url)
            .option("dbtable", table_name)
            .option("user", props.get("user", ""))
            .option("password", props.get("password", ""))
            .option("driver", props.get("driver", "org.postgresql.Driver"))
            .mode(mode)
            .save()
        )

    def stop(self) -> None:
        """Stop the SparkSession and release resources."""
        if self._session is not None:
            logger.info("stopping spark session")
            self._session.stop()
            self._session = None
