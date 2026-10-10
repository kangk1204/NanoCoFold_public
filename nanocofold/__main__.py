"""Allow the documented CLI through the active Python environment as well."""
from .entrypoint import main

raise SystemExit(main())
