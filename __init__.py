"""Standalone Hermes plugin entry point."""


def register(ctx):
    from .search_sources.hooks import Hooks

    hooks = Hooks()
    ctx.register_hook("post_tool_call", hooks.on_post_tool_call)
    ctx.register_hook("subagent_start", hooks.on_subagent_start)
    ctx.register_hook("subagent_stop", hooks.on_subagent_stop)
    ctx.register_hook("transform_llm_output", hooks.on_transform_llm_output)
