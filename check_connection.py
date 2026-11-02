"""Run once on your laptop to prove the setup works:  python check_connection.py

1. Connects with your existing Week 4 key pair and prints who/where you are.
2. Lists the SAKILA.DW tables and runs one real aggregate (revenue by store).
3. Looks up what that query cost, through the same code the MCP tool uses.
4. Tries an UPDATE that touches no rows, which must be REFUSED under the read-only role,
   and lists the role's privileges (expect only USAGE and SELECT).
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

    # A write the role must not be allowed to make. WHERE 1 = 0 means that even if the privilege
    # existed, no row would change; Snowflake checks privileges before it looks at any row.
    # (A TEMPORARY table is not a valid test: any role with USAGE on a schema may create one.)
    print("\nRead-only check: trying UPDATE SAKILA.DW.DIM_STORE ... WHERE 1 = 0 ...")
    try:
        db.run("UPDATE SAKILA.DW.DIM_STORE SET manager_name = manager_name WHERE 1 = 0")
        print("  NOT refused. This role has UPDATE on SAKILA.DW, so it is not read-only. "
              "Check SNOWFLAKE_ROLE=A5_APP_RO and sql/register_key_and_role.sql.")
    except Exception as exc:  # noqa: BLE001
        print(f"  Refused, as expected: {str(exc).splitlines()[0]}")

    role = s["role"] if s["role"].replace("_", "").isalnum() else "A5_APP_RO"
    grants = db.run(f"SHOW GRANTS TO ROLE {role}")
    privs = sorted({str(g.get('privilege')) for g in grants.rows})
    print(f"  Privileges held by {role}: {', '.join(privs)}")


if __name__ == "__main__":
    main()
