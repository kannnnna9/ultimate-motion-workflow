"""Native Hermes plugin entrypoint."""
from functools import partial
from pathlib import Path

from .ump_core.tools import SCHEMAS, dispatch, inbound


def register(ctx):
    for name, schema in SCHEMAS.items():
        ctx.register_tool(name, 'ump', {'name': name, **schema}, partial(dispatch, name))
    ctx.register_hook('pre_gateway_dispatch', inbound)
    ctx.register_skill('motion-pipeline', Path(__file__).parent / 'skills/motion-pipeline/SKILL.md',
                       description='Produce and review vertical motion videos.')
