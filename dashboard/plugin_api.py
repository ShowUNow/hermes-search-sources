"""Backend routes for the Desktop half (`/api/plugins/search-sources/...`).

Loaded by path, outside the agent loader's module namespace, like hermes-memory-pane.
"""

import importlib.util
import sys
from pathlib import Path

package = Path(__file__).resolve().parents[1] / "search_sources"
name = "_hermes_search_sources_http"
spec = importlib.util.spec_from_file_location(name, package / "__init__.py", submodule_search_locations=[str(package)])
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
spec.loader.exec_module(module)
api = importlib.import_module(name + ".api")
router = api.router
