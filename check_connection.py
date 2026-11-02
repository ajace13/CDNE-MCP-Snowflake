"""Run once on your laptop to prove the setup works:  python check_connection.py

1. Connects with your existing Week 4 key pair and prints who/where you are.
2. Lists the SAKILA.DW tables and runs one real aggregate (revenue by store).
3. Looks up what that query cost, through the same code the MCP tool uses.
4. Tries to create a TEMPORARY table, which should be REFUSED under the read-only role.
   (A temporary table disappears when the session ends, so even if it isn't refused, nothing lasts.)
"""
from __future__ import annotations

from warehouse import db, tools


def main() -> None:
    s = db.settings()
    print(f"Settings from: {s['_source']}")
    who = db.run("SELECT CURRENT_USER() AS U, CURRENT_ROLE() AS R, CURRENT_SECONDARY_ROLES() AS S2, "
                 "CURRENT_WAREHOUSE() AS W")
    r = who.rows[0]
    print(f"User {r['U']} | role {r['R']} | secondary roles {r['S2']} | warehouse {r['W']}\n")

    schema = tools.describe_schema()
    print(f"{len(schema['tables'])} tables in {schema['database']}.{schema['schema']}:")
    for t in schema["tables"]:
        print(f"  {t['table']:<28} {t['kind']:<10} {t['row_count']!s:>10} rows   {t['columns']}")

    print("\n" + schema["data_covers"])
    agg = tools.aggregate("revenue", "2005-05-24", "2006-02-14", "store")
    print("\nRevenue by store (aggregate tool):")
    for row in agg["rows"]:
        print(f"  {row['group_value']}: ${row['value']:,.2f} from {row['fact_rows']} payments, "
              f"{row['distinct_customers']} customers")
    print(f"  total = ${sum(r['value'] for r in agg['rows']):,.2f} (Week 4 check: 67,406.56)")

    cost = tools.query_cost(agg["query_id"])
    if cost["found"]:
        print(f"\nThat aggregate query took {cost['elapsed_seconds']} s, scanned {cost['bytes_scanned']} bytes, "
              f"estimated {cost['estimated_warehouse_credits']} warehouse credits "
              f"+ {cost['cloud_services_credits']} cloud-services credits.")
    else:
        print("\nCost lookup: " + cost["message"])

    print("\nRead-only check: trying CREATE TEMPORARY TABLE ...")
    try:
        db.run("CREATE TEMPORARY TABLE A5_READ_ONLY_PROBE (x INT)")
        print("  NOT refused. This role can create tables, so it is not read-only. "
              "Run sql/01_app_readonly_role.sql and set SNOWFLAKE_ROLE=A5_APP_RO.")
    except Exception as exc:  # noqa: BLE001
        print(f"  Refused, as expected: {str(exc).splitlines()[0]}")


if __name__ == "__main__":
    main()
