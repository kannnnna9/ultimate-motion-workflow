"""Native Hermes plugin entrypoint."""
from functools import partial
from pathlib import Path

from .ump_core.tools import SCHEMAS, dispatch, inbound


def register(ctx):
    from .ump_core.worker import seed_fixtures

    # Fixtures live in the data root, not in this (replaceable) tree, and are
    # written once. A failure here must not stop the plugin from loading: the
    # tools still work, and the worker reports a missing fixture when used.
    try:
        seed_fixtures()
    except Exception:
        pass
    for name, schema in SCHEMAS.items():
        ctx.register_tool(name, 'ump', {'name': name, **schema}, partial(dispatch, name))
    ctx.register_hook('pre_gateway_dispatch', inbound)
    ctx.register_skill('motion-pipeline', Path(__file__).parent / 'skills/motion-pipeline/SKILL.md',
                       description='Produce and review vertical motion videos.')
