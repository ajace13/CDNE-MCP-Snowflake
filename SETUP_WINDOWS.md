# Run the MCP server on Windows and connect Claude to it

The server is a small Python program on this laptop. Claude Desktop or Claude Code starts it and talks to it over stdio, so no network port is opened. It connects out to Snowflake (`LKPJNOU-KMC07329`) as `CLAUDE_AGENT` with a key pair, under the SELECT-only role `A5_APP_RO`.

## 1. Python environment (in the Assignment5 folder, PowerShell)

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
```

Python 3.13 is used because 3.14 is newer than some Snowflake wheels.

## 2. Key and connection (one time)

* Private key: `%LOCALAPPDATA%\snowflake\a5_agent.p8`, readable only by your Windows account. Never commit or upload it.
* Connection: `[connections.claude]` in `%LOCALAPPDATA%\snowflake\config.toml`, the file the Snowflake CLI uses on this PC. Its fields are account, user `CLAUDE_AGENT`, authenticator `SNOWFLAKE_JWT`, `private_key_file`, role `A5_APP_RO` and warehouse `CLASS_WH`.
* Snowsight, as ACCOUNTADMIN, Run All once: `sql/register_key_and_role.sql`, with your public key filled in (the filled copy is `local/register_key.sql`). It attaches the key to `CLAUDE_AGENT` as `RSA_PUBLIC_KEY_2`, so a Cloud Shell key in slot 1 keeps working. It also creates `A5_APP_RO` with SELECT on SAKILA and CLASSICMODELS.

## 3. Prove it works

```powershell
.venv\Scripts\python check_connection.py
```

It prints your user and role, the SAKILA.DW tables, revenue by store, and what that query cost. The two stores should add up to 67,406.56, the Week 4 check. Last, it tries to create a temporary table, which should be **refused**.

## 4a. Claude Desktop

Settings → Developer → Edit Config opens `claude_desktop_config.json`. Add the `sakila-warehouse` entry from `claude_desktop_config.example.json`, with paths matching this folder. Fully quit Claude Desktop (right-click the tray icon, then Quit) and reopen it. **Settings → Developer → Local MCP servers** should list `sakila-warehouse` as running. If it doesn't, check the log at `%APPDATA%\Claude\logs\mcp-server-sakila-warehouse.log`.

## 4b. Claude Code

From the Assignment5 folder:

```powershell
claude mcp add --scope project sakila-warehouse --env SNOWFLAKE_CONNECTION=claude --env SNOWFLAKE_ROLE=A5_APP_RO -- "<this folder>\.venv\Scripts\python.exe" "<this folder>\mcp_server.py"
claude mcp list
```

That writes `.mcp.json` in this folder. Inside `claude`, `/mcp` shows the server and its four tools.

## Try it

* "What tables are in the warehouse and what does one row of each represent?"
* "How many customers rented Action films in July 2005?" This should use `distinct_customers`, not `rental_count`.
* "What was revenue by month at store 2?" Then: "What did that query cost?"
* "Why did revenue drop in 2007?" The data ends in February 2006, so the answer should say there's no 2007 data.
