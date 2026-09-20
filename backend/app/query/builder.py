# ruff: noqa: E501
"""Trusted SQL templates for approved definitions. User values are parameters."""

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.ai.client import Intent
from app.auth.service import allows
from app.core.dates import month_start, resolve_period

TERMINAL = """
WITH terminal AS (
 SELECT DISTINCT ON (workorderid) workorderid, locationid
 FROM production.workorderrouting
 ORDER BY workorderid,operationsequence DESC,locationid
), fact AS (
 SELECT w.*,w.orderqty-w.scrappedqty AS stockedqty,t.locationid,lf.factory_id
 FROM production.workorder w
 LEFT JOIN terminal t ON t.workorderid=w.workorderid
 LEFT JOIN acbi_demo.location_factory lf ON lf.locationid=t.locationid
)
"""


@dataclass(frozen=True)
class QueryPlan:
    sql: str
    params: dict[str, Any]
    metric_id: str
    start: date
    end: date
    version: int = 1


def resolve_dates(intent: Intent, anchor: date) -> tuple[date, date]:
    if intent.period == "explicit":
        if not intent.start_date or not intent.end_date:
            raise ValueError("Specify both start and end dates")
        start, end = date.fromisoformat(intent.start_date), date.fromisoformat(
            intent.end_date
        )
    elif intent.period:
        start, end = resolve_period(intent.period, anchor)
    else:
        raise ValueError("Specify a reporting period")
    if start >= end or (end - start).days > 3660:
        raise ValueError("Invalid reporting period")
    return start, end


def authorize(intent: Intent, role: str) -> int | None:
    metric = intent.metric_id
    if metric not in {
        "revenue",
        "sales_growth",
        "production_output",
        "defect_rate",
        "on_time_rate",
    }:
        raise ValueError("Unknown metric")
    domain = {
        "revenue": "sales",
        "sales_growth": "sales",
        "production_output": "production",
        "defect_rate": "quality",
        "on_time_rate": "production",
    }[metric]
    factory_id: int | None
    if role == "production":
        if intent.factory_id not in (None, 1):
            raise PermissionError("Factory outside your scope")
        factory_id = 1
    else:
        factory_id = intent.factory_id
    if not allows(role, domain, factory_id if role == "production" else None):
        raise PermissionError("Metric outside your scope")
    if domain == "sales" and factory_id is not None:
        raise ValueError("Revenue has no factory relationship")
    if domain != "sales" and intent.territory:
        raise ValueError("Production has no sales territory relationship")
    return factory_id


def build(intent: Intent, role: str, anchor: date) -> QueryPlan:
    plan = prepare(intent, role, anchor)
    factory_id = authorize(intent, role)
    metric = intent.metric_id
    assert metric is not None
    domain = "sales" if metric in {"revenue", "sales_growth"} else "production"
    start, end, params = plan.start, plan.end, plan.params
    if domain == "sales":
        sql = sales_sql(intent, params, start)
    else:
        sql = production_sql(intent, params, factory_id)
    return QueryPlan(sql=sql, params=params, metric_id=metric, start=start, end=end)


def prepare(intent: Intent, role: str, anchor: date) -> QueryPlan:
    factory_id = authorize(intent, role)
    assert intent.metric_id is not None
    start, end = resolve_dates(intent, anchor)
    params: dict[str, Any] = {"start": start, "end": end, "limit": intent.limit}
    if factory_id is not None:
        params["factory_id"] = factory_id
    if intent.territory:
        territory_names = intent.territory.split("|")
        if len(territory_names) == 1:
            params["territory"] = intent.territory
        else:
            params.update(
                {
                    f"territory_{index}": name
                    for index, name in enumerate(territory_names)
                }
            )
    if intent.metric_id == "sales_growth":
        if intent.period not in {"last_month", "last_quarter"}:
            raise ValueError("Growth requires a complete previous month or quarter")
        params["baseline_start"] = month_start(
            start, -3 if intent.period == "last_quarter" else -1
        )
        params["baseline_end"] = start
    return QueryPlan("", params, intent.metric_id, start, end)


def supports(intent: Intent) -> bool:
    if intent.metric_id == "on_time_rate":
        return True  # template only; an unsupported grouping is refused, not guessed
    if intent.dimension == "week":
        return False
    if intent.metric_id == "sales_growth" and intent.dimension != "none":
        return False
    return not (
        intent.metric_id in {"production_output", "defect_rate"}
        and intent.dimension == "day"
    )


def sales_sql(intent: Intent, params: dict[str, Any], start: date) -> str:
    metric, dimension = intent.metric_id, intent.dimension
    if dimension not in {
        "none",
        "sales_territory",
        "month",
        "day",
        "product",
        "product_category",
    }:
        raise ValueError("Unsupported sales dimension")
    if metric == "sales_growth" and dimension != "none":
        raise ValueError("Sales growth breakdown is not yet supported")
    if intent.zero_scrap_only:
        raise ValueError("Zero scrap only applies to defect rate")
    if intent.territory and not any(
        name == "territory" or name.startswith("territory_") for name in params
    ):
        territory_names = intent.territory.split("|")
        if len(territory_names) == 1:
            params["territory"] = intent.territory
        else:
            params.update(
                {
                    f"territory_{index}": name
                    for index, name in enumerate(territory_names)
                }
            )
    territory_join = (
        " JOIN sales.salesterritory t ON t.territoryid=h.territoryid"
        if intent.territory or dimension == "sales_territory"
        else ""
    )
    if intent.territory and "|" in intent.territory:
        territory_where = (
            " AND t.name IN ("
            + ",".join(
                f":territory_{index}"
                for index, _ in enumerate(intent.territory.split("|"))
            )
            + ")"
        )
    else:
        territory_where = " AND t.name=:territory" if intent.territory else ""
    if metric == "sales_growth":
        if intent.period not in {"last_month", "last_quarter"}:
            raise ValueError("Growth requires a complete previous month or quarter")
        params["baseline_start"] = month_start(
            start, -3 if intent.period == "last_quarter" else -1
        )
        params["baseline_end"] = start
        return f"""
WITH periods AS (
 SELECT SUM(h.subtotal) FILTER (WHERE h.orderdate>=:start AND h.orderdate<:end) AS current_revenue,
 SUM(h.subtotal) FILTER (WHERE h.orderdate>=:baseline_start AND h.orderdate<:baseline_end) AS previous_revenue,
 COUNT(*) FILTER (WHERE h.orderdate>=:start AND h.orderdate<:end) AS current_count,
 COUNT(*) FILTER (WHERE h.orderdate>=:baseline_start AND h.orderdate<:baseline_end) AS previous_count
 FROM sales.salesorderheader h{territory_join} WHERE true{territory_where}
)
SELECT current_revenue,previous_revenue,
 (current_revenue-previous_revenue)/NULLIF(previous_revenue,0) AS sales_growth
FROM periods WHERE current_count>0 AND previous_count>0 LIMIT :limit
"""
    if intent.series_dimension != "none":
        if (metric, dimension, intent.series_dimension) != (
            "revenue",
            "month",
            "sales_territory",
        ):
            raise ValueError("This combination of breakdowns is not approved")
        return f"""SELECT date_trunc('month',h.orderdate)::date AS month,t.name AS territory,SUM(h.subtotal) AS revenue,COUNT(*) AS sample_count
FROM sales.salesorderheader h JOIN sales.salesterritory t ON t.territoryid=h.territoryid
WHERE h.orderdate>=:start AND h.orderdate<:end{territory_where}
GROUP BY 1,2 ORDER BY 1,2 LIMIT :limit"""
    if dimension in {"product", "product_category"}:
        selected = (
            "p.productid,p.name AS product"
            if dimension == "product"
            else "pc.productcategoryid,COALESCE(pc.name,'Uncategorized') AS category"
        )
        joins = "JOIN production.product p ON p.productid=l.productid"
        if dimension == "product_category":
            joins += " LEFT JOIN production.productsubcategory ps ON ps.productsubcategoryid=p.productsubcategoryid LEFT JOIN production.productcategory pc ON pc.productcategoryid=ps.productcategoryid"
        group = (
            "p.productid,p.name"
            if dimension == "product"
            else "pc.productcategoryid,pc.name"
        )
        return f"""
WITH lines AS (
 SELECT h.salesorderid,h.subtotal,d.productid,
 d.orderqty*d.unitprice*(1-d.unitpricediscount) AS net,
 SUM(d.orderqty*d.unitprice*(1-d.unitpricediscount)) OVER (PARTITION BY h.salesorderid) AS order_net
 FROM sales.salesorderheader h
 JOIN sales.salesorderdetail d ON d.salesorderid=h.salesorderid
 {territory_join}
 WHERE h.orderdate>=:start AND h.orderdate<:end{territory_where}
)
SELECT {selected},SUM(l.subtotal*l.net/NULLIF(l.order_net,0)) AS revenue,
 SUM(CASE WHEN l.order_net=0 THEN 1 ELSE 0 END) AS invalid_detail_count
FROM lines l {joins} GROUP BY {group}
ORDER BY revenue DESC LIMIT :limit
"""
    if dimension == "none":
        return f"""SELECT SUM(h.subtotal) AS revenue,COUNT(*) AS sample_count
FROM sales.salesorderheader h{territory_join}
WHERE h.orderdate>=:start AND h.orderdate<:end{territory_where}
HAVING COUNT(*)>0 LIMIT :limit"""
    selected = {
        "sales_territory": "t.territoryid,t.name AS territory",
        "month": "date_trunc('month',h.orderdate)::date AS month",
        "day": "h.orderdate::date AS day",
    }[dimension]
    group = "t.territoryid,t.name" if dimension == "sales_territory" else "1"
    order = "revenue DESC,t.territoryid" if dimension == "sales_territory" else "1"
    return f"""SELECT {selected},SUM(h.subtotal) AS revenue,COUNT(*) AS sample_count
FROM sales.salesorderheader h{territory_join}
WHERE h.orderdate>=:start AND h.orderdate<:end{territory_where}
GROUP BY {group} ORDER BY {order} LIMIT :limit"""


def production_sql(
    intent: Intent, params: dict[str, Any], factory_id: int | None
) -> str:
    metric, dimension = intent.metric_id, intent.dimension
    if dimension not in {
        "none",
        "month",
        "product",
        "product_category",
        "production_line",
        "factory",
        "scrap_reason",
    }:
        raise ValueError("Unsupported production dimension")
    if intent.zero_scrap_only and (metric != "defect_rate" or dimension != "product"):
        raise ValueError("Zero-scrap filter requires defect rate by product")
    series = intent.series_dimension
    stack_dims = {"production_line", "factory", "product_category"}
    if series != "none" and (
        metric != "production_output"
        or series not in stack_dims
        or dimension not in stack_dims | {"month"}
        or dimension == series
    ):
        raise ValueError("This combination of breakdowns is not approved")
    dims = {dimension, series}
    if factory_id is not None:
        params["factory_id"] = factory_id
    where = "w.enddate>=:start AND w.enddate<:end"
    if factory_id is not None:
        where += " AND w.factory_id=:factory_id"
    joins = ""
    fields = {
        "none": ("", "", ""),
        "month": ("date_trunc('month',w.enddate)::date AS month", "1", "1"),
        "product": ("p.productid,p.name AS product", "p.productid,p.name", "2"),
        "product_category": (
            "pc.productcategoryid,COALESCE(pc.name,'Uncategorized') AS category",
            "pc.productcategoryid,pc.name",
            "1",
        ),
        "production_line": (
            "w.locationid,COALESCE(l.name,'Unassigned') AS production_line",
            "w.locationid,l.name",
            "1 NULLS LAST",
        ),
        "factory": (
            "w.factory_id,COALESCE(f.name,'Unassigned') AS factory",
            "w.factory_id,f.name",
            "1 NULLS LAST",
        ),
        "scrap_reason": (
            "s.scrapreasonid,COALESCE(s.name,'Unspecified') AS reason",
            "s.scrapreasonid,s.name",
            "1 NULLS LAST",
        ),
    }
    selected, group, order = fields[dimension]
    if dims & {"product", "product_category"}:
        joins += " JOIN production.product p ON p.productid=w.productid"
    if "product_category" in dims:
        joins += " LEFT JOIN production.productsubcategory ps ON ps.productsubcategoryid=p.productsubcategoryid LEFT JOIN production.productcategory pc ON pc.productcategoryid=ps.productcategoryid"
    if "production_line" in dims:
        joins += " LEFT JOIN production.location l ON l.locationid=w.locationid"
    if "factory" in dims:
        joins += " LEFT JOIN acbi_demo.factory f ON f.factory_id=w.factory_id"
    if "scrap_reason" in dims:
        joins += (
            " LEFT JOIN production.scrapreason s ON s.scrapreasonid=w.scrapreasonid"
        )
    if metric == "production_output":
        value = "SUM(w.stockedqty) AS production_output"
    elif metric == "on_time_rate":
        value = (
            "COUNT(*) FILTER (WHERE w.enddate<=w.duedate)::numeric"
            "/NULLIF(COUNT(*),0) AS on_time_rate"
        )
    else:
        value = "SUM(w.scrappedqty)::numeric/NULLIF(SUM(w.orderqty),0) AS defect_rate"
        if dimension in {"product", "scrap_reason"}:
            value += (
                ",SUM(w.orderqty) AS ordered_units,SUM(w.scrappedqty) AS scrapped_units"
            )
    if series != "none":
        s2, g2, _ = fields[series]
        return (
            TERMINAL
            + f"SELECT {selected},{s2},{value} FROM fact w{joins} WHERE {where} GROUP BY {group},{g2} ORDER BY {group},{g2} LIMIT :limit"
        )
    if dimension == "none":
        return (
            TERMINAL
            + f"SELECT {value},COUNT(*) AS sample_count FROM fact w{joins} WHERE {where} HAVING COUNT(*)>0 LIMIT :limit"
        )
    having = (
        " HAVING SUM(w.scrappedqty)=0 AND COUNT(*)>0" if intent.zero_scrap_only else ""
    )
    order_by = (
        "defect_rate DESC,p.productid"
        if dimension == "product"
        and metric == "defect_rate"
        and not intent.zero_scrap_only
        else order
    )
    if dimension == "product" and metric == "production_output":
        order_by = "production_output DESC,p.productid"
    if intent.zero_scrap_only:
        order_by = "p.productid"
        value += ",COUNT(*) AS sample_count"
    return (
        TERMINAL
        + f"SELECT {selected},{value} FROM fact w{joins} WHERE {where} GROUP BY {group}{having} ORDER BY {order_by} LIMIT :limit"
    )
