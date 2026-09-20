"""One fail-closed SQL gate for templates and model candidates."""

import re
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from app.ai.client import Intent
from app.query.builder import QueryPlan, authorize, production_sql, sales_sql

TABLES: dict[str, dict[str, str]] = {
    "sales.salesorderheader": {
        "salesorderid": "INT",
        "orderdate": "TIMESTAMP",
        "territoryid": "INT",
        "subtotal": "DECIMAL",
    },
    "sales.salesorderdetail": {
        "salesorderid": "INT",
        "salesorderdetailid": "INT",
        "productid": "INT",
        "orderqty": "INT",
        "unitprice": "DECIMAL",
        "unitpricediscount": "DECIMAL",
    },
    "sales.salesterritory": {"territoryid": "INT", "name": "TEXT"},
    "production.workorder": {
        "workorderid": "INT",
        "productid": "INT",
        "orderqty": "INT",
        "scrappedqty": "INT",
        "enddate": "TIMESTAMP",
        "scrapreasonid": "INT",
    },
    "production.workorderrouting": {
        "workorderid": "INT",
        "productid": "INT",
        "operationsequence": "INT",
        "locationid": "INT",
    },
    "production.location": {"locationid": "INT", "name": "TEXT"},
    "production.product": {
        "productid": "INT",
        "name": "TEXT",
        "productsubcategoryid": "INT",
    },
    "production.productsubcategory": {
        "productsubcategoryid": "INT",
        "name": "TEXT",
        "productcategoryid": "INT",
    },
    "production.productcategory": {"productcategoryid": "INT", "name": "TEXT"},
    "production.scrapreason": {"scrapreasonid": "INT", "name": "TEXT"},
    "acbi_demo.factory": {"factory_id": "INT", "name": "TEXT"},
    "acbi_demo.location_factory": {"locationid": "INT", "factory_id": "INT"},
}
PRODUCT_TABLES = {
    "production.product",
    "production.productsubcategory",
    "production.productcategory",
}
FUNCTIONS = {
    "SUM",
    "COUNT",
    "MIN",
    "MAX",
    "AVG",
    "NULLIF",
    "COALESCE",
    "CAST",
    "TIMESTAMP_TRUNC",
    "DATE_TRUNC",
    "ROUND",
    "ABS",
    "CASE",
    "IF",
    "ROW_NUMBER",
    "RANK",
    "DENSE_RANK",
    "LAG",
    "LEAD",
    "AND",
    "OR",
}


class SQLPolicyError(PermissionError):
    """Policy violations are never retried or routed through another path."""


class SQLCorrectionError(ValueError):
    """A syntax error may be corrected under the original request budget."""


@dataclass(frozen=True)
class ValidatedQuery(QueryPlan):
    pass


def allowed_tables(role: str) -> dict[str, dict[str, str]]:
    if role == "manager":
        return dict(TABLES)
    if role == "sales":
        return {
            k: v
            for k, v in TABLES.items()
            if k.startswith("sales.") or k in PRODUCT_TABLES
        }
    if role == "production":
        return {k: v for k, v in TABLES.items() if not k.startswith("sales.")}
    return {}


def factory_workorders() -> str:
    # Scope is applied to the terminal operation, not every routing step.
    return """SELECT terminal.workorderid FROM (
        SELECT DISTINCT ON (workorderid) workorderid,locationid
        FROM production.workorderrouting
        ORDER BY workorderid,operationsequence DESC,locationid
    ) terminal JOIN acbi_demo.location_factory lf USING(locationid)
    WHERE lf.factory_id=:_scope_factory"""


def scoped_source(
    table: str, factory: int | None, params: dict[str, Any]
) -> exp.Select:
    conditions: list[str] = []
    if table == "sales.salesorderheader":
        conditions = ["orderdate>=:_scope_start", "orderdate<:_scope_end"]
        if "territory" in params:
            conditions.append(
                "territoryid IN (SELECT territoryid FROM sales.salesterritory "
                "WHERE name=:territory)"
            )
    elif table == "sales.salesorderdetail":
        header = scoped_source("sales.salesorderheader", None, params)
        header.set("expressions", [exp.column("salesorderid")])
        header_sql = header.sql()
        conditions = [f"salesorderid IN ({header_sql})"]
    elif table == "production.workorder":
        conditions = ["enddate>=:_scope_start", "enddate<:_scope_end"]
        if factory is not None:
            conditions.append(f"workorderid IN ({factory_workorders()})")
    elif table == "production.workorderrouting":
        workorders = (
            "SELECT workorderid FROM production.workorder "
            "WHERE enddate>=:_scope_start AND enddate<:_scope_end"
        )
        if factory is not None:
            workorders += f" AND workorderid IN ({factory_workorders()})"
        conditions = [f"workorderid IN ({workorders})"]
    elif factory is not None and table in {
        "acbi_demo.factory",
        "acbi_demo.location_factory",
    }:
        conditions = ["factory_id=:_scope_factory"]
    elif factory is not None and table == "production.location":
        conditions = [
            "locationid IN (SELECT locationid FROM acbi_demo.location_factory "
            "WHERE factory_id=:_scope_factory)"
        ]
    query = f"SELECT {','.join(TABLES[table])} FROM {table}"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    result = sqlglot.parse_one(query, read="postgres")
    assert isinstance(result, exp.Select)
    return result


def expression_key(expression: exp.Expression) -> str:
    cloned = expression.copy()
    for column in cloned.find_all(exp.Column):
        column.set("table", None)
        column.set("db", None)
        column.set("catalog", None)
    return cloned.sql(dialect="postgres").lower().replace('"', "")


def verify_metric_formula(
    tree: exp.Select, intent: Intent, physical_names: set[str]
) -> None:
    """The declared RAG scope accepts only the approved formulas."""
    metric = intent.metric_id
    assert metric is not None
    supported_sources = {
        "sales_growth": {"sales.salesorderheader", "sales.salesterritory"},
        "revenue": {"sales.salesorderheader"},
        "production_output": {"production.workorder"},
        "defect_rate": {"production.workorder"},
    }
    if physical_names != supported_sources[str(metric)]:
        raise SQLPolicyError("The query uses tables outside this approved mapping")
    if metric != "sales_growth" and tree.args.get("with_"):
        raise SQLPolicyError("This mapping does not need a generated CTE")
    output = next(
        (part for part in tree.expressions if part.alias_or_name == metric), None
    )
    if not isinstance(output, exp.Alias):
        raise SQLPolicyError("The approved metric output is required")
    expected = {
        "revenue": "SUM(subtotal)",
        "production_output": "SUM(orderqty-scrappedqty)",
        "defect_rate": "SUM(scrappedqty)::numeric/NULLIF(SUM(orderqty),0)",
        "sales_growth": "(current_revenue-previous_revenue)/NULLIF(previous_revenue,0)",
    }[str(metric)]
    approved_expression = sqlglot.parse_one(
        f"SELECT {expected} AS metric", read="postgres"
    )
    assert isinstance(approved_expression, exp.Select)
    if expression_key(output.this) != expression_key(
        approved_expression.expressions[0].this
    ):
        raise SQLPolicyError("The proposed SQL changes an approved metric formula")
    if metric == "sales_growth":
        if intent.dimension != "sales_territory":
            raise SQLPolicyError("Growth grouping is not approved")
        for alias, start, end, aggregate in (
            ("current_revenue", "start", "end", exp.Sum),
            ("previous_revenue", "baseline_start", "baseline_end", exp.Sum),
            ("current_count", "start", "end", exp.Count),
            ("previous_count", "baseline_start", "baseline_end", exp.Count),
        ):
            matches = [
                n
                for n in tree.find_all(exp.Alias)
                if n.alias == alias and isinstance(n.this, exp.Filter)
            ]
            if len(matches) != 1:
                raise SQLPolicyError(
                    "Growth periods must follow the approved definition"
                )
            filtered = matches[0].this
            if not isinstance(filtered.this, aggregate):
                raise SQLPolicyError("Growth totals must use approved aggregates")
            if (
                aggregate is exp.Sum
                and expression_key(filtered.this.this) != "subtotal"
            ):
                raise SQLPolicyError("Growth revenue must use header subtotal")
            if aggregate is exp.Count and not isinstance(filtered.this.this, exp.Star):
                raise SQLPolicyError("Growth period counts must count orders")
            bound = {p.name for p in filtered.find_all(exp.Placeholder)}
            if bound != {start, end}:
                raise SQLPolicyError("Growth periods must use the resolved dates")
            filter_condition = filtered.args.get("expression")
            condition_key = expression_key(filter_condition) if filter_condition else ""
            reference = sqlglot.parse_one(
                f"SELECT COUNT(*) FILTER(WHERE orderdate>=:{start} "
                f"AND orderdate<:{end}) AS n",
                read="postgres",
            )
            assert isinstance(reference, exp.Select)
            expected_filter = reference.expressions[0].this.args["expression"]
            if condition_key != expression_key(expected_filter):
                raise SQLPolicyError("Growth periods must use exact date boundaries")
        condition = tree.args.get("where")
        count_guard = sqlglot.parse_one(
            "SELECT 1 WHERE current_count>0 AND previous_count>0", read="postgres"
        )
        assert isinstance(count_guard, exp.Select)
        if not condition or expression_key(condition) != expression_key(
            count_guard.args["where"]
        ):
            raise SQLPolicyError("Growth needs records in both periods")
        cte = tree.args.get("with_")
        if not cte or len(cte.expressions) != 1:
            raise SQLPolicyError("Growth requires the approved two-period calculation")
        period_query = cte.expressions[0].this
        if not isinstance(period_query, exp.Select) or period_query.args.get("where"):
            raise SQLPolicyError("Growth cannot add an unapproved filter")
        grouping = period_query.args.get("group")
        if (
            not grouping
            or len(grouping.expressions) != 1
            or expression_key(grouping.expressions[0]) != "territoryid"
        ):
            raise SQLPolicyError("Growth must group by territory")
        joins = tree.args.get("joins") or []
        if len(joins) != 1 or not isinstance(joins[0].args.get("on"), exp.EQ):
            raise SQLPolicyError("Growth needs the territory mapping")
        joined_columns = list(joins[0].args["on"].find_all(exp.Column))
        if (
            len(joined_columns) != 2
            or any(c.name != "territoryid" for c in joined_columns)
            or joined_columns[0].table == joined_columns[1].table
        ):
            raise SQLPolicyError("Growth needs the territory key join")
        approved_columns = {
            "territoryid",
            "territory",
            "current_revenue",
            "previous_revenue",
            "sales_growth",
        }
        if any(part.alias_or_name not in approved_columns for part in tree.expressions):
            raise SQLPolicyError(
                "Growth output columns must follow the approved mapping"
            )
        territory = next(
            (p for p in tree.expressions if p.alias_or_name == "territory"), None
        )
        if (
            not isinstance(territory, exp.Alias)
            or not isinstance(territory.this, exp.Column)
            or territory.this.name != "name"
        ):
            raise SQLPolicyError("Territory name must use the approved dimension")
    elif not tree.args.get("group") and any(
        isinstance(n, exp.AggFunc) for n in output.this.walk()
    ):
        having = tree.args.get("having")
        count_guard = sqlglot.parse_one("SELECT 1 HAVING COUNT(*)>0", read="postgres")
        assert isinstance(count_guard, exp.Select)
        if not having or expression_key(having) != expression_key(
            count_guard.args["having"]
        ):
            raise SQLPolicyError("Empty aggregate results require a count guard")
    if metric != "sales_growth":
        date_column = "orderdate" if metric == "revenue" else "enddate"
        where = tree.args.get("where")
        reference = sqlglot.parse_one(
            f"SELECT 1 WHERE {date_column}>=:start AND {date_column}<:end",
            read="postgres",
        )
        assert isinstance(reference, exp.Select)
        if where and expression_key(where) != expression_key(reference.args["where"]):
            raise SQLPolicyError("Extra or changed reporting filters are not approved")
        count_aliases = [
            n for n in tree.expressions if n.alias_or_name == "sample_count"
        ]
        if count_aliases and (
            len(count_aliases) != 1
            or not isinstance(count_aliases[0], exp.Alias)
            or not isinstance(count_aliases[0].this, exp.Count)
            or not isinstance(count_aliases[0].this.this, exp.Star)
        ):
            raise SQLPolicyError("Sample count must count source records")
        approved_columns = {metric, "sample_count", intent.dimension}
        if any(part.alias_or_name not in approved_columns for part in tree.expressions):
            raise SQLPolicyError("Output columns must follow the approved mapping")
    if intent.dimension in {"day", "week"}:
        dimension = next(
            (
                part
                for part in tree.expressions
                if part.alias_or_name == intent.dimension
            ),
            None,
        )
        if not isinstance(dimension, exp.Alias):
            raise SQLPolicyError("Requested date grouping is required")
        date_column = "orderdate" if metric == "revenue" else "enddate"
        expected_date = (
            f"date_trunc('week',{date_column})::date"
            if intent.dimension == "week"
            else f"{date_column}::date"
        )
        expected_query = sqlglot.parse_one(
            f"SELECT {expected_date} AS date_bucket", read="postgres"
        )
        assert isinstance(expected_query, exp.Select)
        if expression_key(dimension.this) != expression_key(
            expected_query.expressions[0].this
        ):
            raise SQLPolicyError("Date grouping differs from the approved mapping")
        grouping = tree.args.get("group")
        if (
            not grouping
            or len(grouping.expressions) != 1
            or expression_key(grouping.expressions[0]) != expression_key(dimension.this)
        ):
            raise SQLPolicyError("Trend must group by its date bucket")
        if tree.args.get("having"):
            raise SQLPolicyError("Trend cannot remove date buckets")


def validate(
    plan: QueryPlan,
    intent: Intent,
    role: str,
    *,
    generated: bool = False,
    trusted_template: bool = False,
) -> ValidatedQuery:
    factory = authorize(intent, role)
    if len(plan.sql) > 24000:
        raise SQLPolicyError("SQL exceeds the supported size")
    try:
        statements = sqlglot.parse(plan.sql, read="postgres")
    except ParseError as error:
        raise SQLCorrectionError("SQL syntax is invalid") from error
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise SQLPolicyError("Only one SELECT statement is permitted")
    tree = statements[0]
    for node in tree.walk():
        if node.comments:
            raise SQLPolicyError("SQL comments are not permitted")
        if isinstance(
            node,
            (
                exp.DDL,
                exp.DML,
                exp.Command,
                exp.Union,
                exp.Intersect,
                exp.Except,
                exp.Into,
                exp.Lock,
                exp.Lateral,
            ),
        ):
            raise SQLPolicyError("Operation is not permitted")
        if isinstance(node, exp.Select) and any(
            node.args.get(k) for k in ("into", "locks")
        ):
            raise SQLPolicyError("SELECT operation is not permitted")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise SQLPolicyError("Recursive queries are not permitted")
        if isinstance(node, exp.Func) and node.sql_name() not in FUNCTIONS:
            raise SQLPolicyError("Function is not permitted")
        if isinstance(node, exp.Cast):
            dtype = node.args["to"].sql().upper()
            if not re.fullmatch(
                r"(?:INT|BIGINT|SMALLINT|DECIMAL(?:\(\d+(?:, \d+)?\))?"
                r"|DOUBLE|FLOAT|DATE|TIMESTAMP|TEXT|VARCHAR|BOOLEAN)",
                dtype,
            ):
                raise SQLPolicyError("Type conversion is not permitted")
        if isinstance(node, exp.Identifier) and not re.fullmatch(
            r"[A-Za-z_][A-Za-z_0-9]*", node.name
        ):
            raise SQLPolicyError("Invalid SQL identifier")
    permitted = allowed_tables(role)
    physical: list[exp.Table] = []
    for scope in traverse_scope(tree):
        for source in scope.sources.values():
            if isinstance(source, exp.Table):
                name = f"{source.db}.{source.name}"
                if source.catalog or name not in permitted:
                    raise SQLPolicyError("Table is outside the reporting scope")
                if source.args.get("pivots") or source.args.get("sample"):
                    raise SQLPolicyError("Table operation is not permitted")
                physical.append(source)
    required = (
        "sales.salesorderheader"
        if plan.metric_id in {"revenue", "sales_growth"}
        else "production.workorder"
    )
    if required not in {f"{t.db}.{t.name}" for t in physical}:
        raise SQLPolicyError("The approved metric source is required")
    params = dict(plan.params)
    for placeholder in tree.find_all(exp.Placeholder):
        if placeholder.name not in params or placeholder.name.startswith("_"):
            raise SQLPolicyError("Unknown query parameter")
    schema: dict[str, Any] = {}
    for name, columns in permitted.items():
        namespace, table = name.split(".")
        schema.setdefault(namespace, {})[table] = columns
    try:
        tree = qualify(
            tree, dialect="postgres", schema=schema, validate_qualify_columns=True
        )
    except OptimizeError as error:
        raise SQLPolicyError("Column is outside the reporting scope") from error
    if generated:
        verify_metric_formula(tree, intent, {f"{t.db}.{t.name}" for t in physical})
    if trusted_template:
        # The builder already binds dates and the authorized factory to its
        # terminal-location fact CTE. Rewriting each nested table would repeat
        # the factory subquery and exceed the warehouse's 15-second limit.
        expected_params: dict[str, Any] = {
            "start": plan.start,
            "end": plan.end,
            "limit": intent.limit,
        }
        if factory is not None:
            expected_params["factory_id"] = factory
        if intent.territory:
            expected_params["territory"] = intent.territory
        if plan.metric_id in {"revenue", "sales_growth"}:
            expected_sql = sales_sql(intent, expected_params, plan.start)
        else:
            expected_sql = production_sql(intent, expected_params, factory)
        if generated or plan.sql != expected_sql or params != expected_params:
            raise SQLPolicyError("Template scope does not match the authorized user")
    else:
        # Generated SQL gets source-level predicates before user expressions.
        params["_scope_start"] = params.get("baseline_start", plan.start)
        params["_scope_end"] = plan.end
        if factory is not None:
            params["_scope_factory"] = factory
        physical = []
        for scope in traverse_scope(tree):
            physical.extend(
                s for s in scope.sources.values() if isinstance(s, exp.Table)
            )
        for source in physical:
            name = f"{source.db}.{source.name}"
            scoped = scoped_source(name, factory, params)
            source.replace(scoped.subquery(source.alias_or_name))
    # Bind literals as data too, including values hallucinated into a SQL candidate.
    literal_names: dict[tuple[str, bool], str] = {}
    for literal in list(tree.find_all(exp.Literal)):
        # GROUP/ORDER BY ordinal references and window bounds are SQL structure.
        if (
            isinstance(literal.parent, (exp.Group, exp.Ordered, exp.WindowSpec))
            and not literal.is_string
        ):
            continue
        if isinstance(literal.parent, exp.DataTypeParam):
            continue
        key = (literal.this, literal.is_string)
        if key not in literal_names:
            name = f"_value_{len(literal_names)}"
            literal_names[key] = name
            params[name] = (
                literal.this
                if literal.is_string
                else int(literal.this) if literal.is_int else float(literal.this)
            )
        literal.replace(exp.Placeholder(this=literal_names[key]))
    tree.set("limit", exp.Limit(expression=exp.Literal.number(min(intent.limit, 100))))
    sql = tree.sql(dialect="postgres")
    # SQLAlchemy text() uses :name, whereas SQLGlot's Postgres output uses %(name)s.
    sql = re.sub(r"%\(([A-Za-z_][A-Za-z_0-9]*)\)s", r":\1", sql)
    return ValidatedQuery(
        sql, params, plan.metric_id, plan.start, plan.end, plan.version
    )
