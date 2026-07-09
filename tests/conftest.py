import os
import sys

# apollo_shell/'s modules use absolute imports (e.g. "from database import
# OutageDatabase", not "from apollo_shell.database import ...") - they
# expect apollo_shell/ itself on sys.path, the same way main.py and
# dashboard.py already add it. Done once here so every test file can just
# import database/correlate/historical_import/storm_severity directly.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apollo_shell"))
