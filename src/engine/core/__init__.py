"""Pure domain layer: entities, ports and policies.

Nothing in this package performs I/O or imports a framework, and the rule is
enforced by ``.importlinter``. That constraint is what keeps the business logic
portable across the CLI it runs on today and the multi-tenant service it is
intended to become.
"""

from __future__ import annotations
