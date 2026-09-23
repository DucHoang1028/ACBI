from datetime import date
from typing import Any

from sqlalchemy import Engine, create_engine, text

from app.core.config import Settings


def warehouse_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.warehouse_url(),
        pool_pre_ping=True,
        # No server-side prepared statements: hosted poolers (Neon) do not keep them.
        connect_args={"connect_timeout": 5, "prepare_threshold": None},
        hide_parameters=True,
    )


def inspect_anchor(engine: Engine, override: date | None = None) -> dict[str, Any]:
    with engine.connect() as connection, connection.begin():
        identity = connection.execute(text("""
            SELECT current_user AS username,
                   current_setting('default_transaction_read_only') AS readonly,
                   current_setting('statement_timeout') AS timeout
        """)).mappings().one()
        if dict(identity) != {
            "username": "acbi_ro",
            "readonly": "on",
            "timeout": "15s",
        }:
            raise RuntimeError("Warehouse read-only configuration is not valid")
        connection.execute(text("SET TRANSACTION READ ONLY"))
        row = connection.execute(text("""
            SELECT min(orderdate)::date AS minimum,
                   max(orderdate)::date AS maximum
            FROM sales.salesorderheader
        """)).mappings().one()
        if row["minimum"] is None or row["maximum"] is None:
            raise RuntimeError("Warehouse has no sales dates")
        anchor = override or row["maximum"]
        if not row["minimum"] <= anchor <= row["maximum"]:
            raise ValueError("DATA_AS_OF must be inside the observed sales date range")
        return {
            "data_as_of": anchor.isoformat(),
            "sales_min_date": row["minimum"].isoformat(),
            "sales_max_date": row["maximum"].isoformat(),
            "anchor_source": "configured" if override else "sales_max_orderdate",
        }
