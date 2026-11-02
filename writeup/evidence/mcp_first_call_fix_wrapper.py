"""Test only: import the Snowflake connector before the MCP stdio loop starts, then run the real server."""
import runpy
import sys

import snowflake.connector  # noqa: F401  (load before stdin is being read by another thread)

server_path = sys.argv[1]
sys.path.insert(0, str(__import__("pathlib").Path(server_path).parent))
sys.argv = [server_path]
runpy.run_path(server_path, run_name="__main__")
