"""Local test-only shims - neither has any effect on deployment.

1. Makes the real, single ckanext-ndp/package_validation.py importable as
   bare `package_validation`, matching what test_ndp_validation.py (vendored
   unmodified) expects.

2. Registers `ckanext`/`ckanext.ndpcatalogadditions` as namespace packages pointing at our
   actual ckanext-ndp/ checkout, so convert.py's deployment-correct import
   (`from ckanext.ndpcatalogadditions.package_validation import ndp_package_validate`) also
   resolves here - without needing the real `ckan` package installed, since
   package_validation.py itself has no CKAN dependency (plain stdlib +
   requests). keycloak_token.py (which does depend on `ckan.plugins`) is
   imported lazily inside convert.py's view function for the same reason, so
   it's never touched by these tests.
"""
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]  # .../ckanext-ndp

sys.path.insert(0, str(ROOT))

if 'ckanext' not in sys.modules:
    ckanext_pkg = types.ModuleType('ckanext')
    ckanext_pkg.__path__ = []
    sys.modules['ckanext'] = ckanext_pkg
if 'ckanext.ndpcatalogadditions' not in sys.modules:
    ndp_pkg = types.ModuleType('ckanext.ndpcatalogadditions')
    ndp_pkg.__path__ = [str(ROOT)]
    sys.modules['ckanext.ndpcatalogadditions'] = ndp_pkg
    sys.modules['ckanext'].ndp = ndp_pkg
