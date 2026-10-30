"""Apply Token Graph's offline policy to spawned Python workers."""
import os
if os.environ.get('TOKEN_GRAPH_OFFLINE') == '1':
    from offline_policy import install
    install(os.environ.get('TOKEN_GRAPH_ALLOW_OLLAMA') == '1')
