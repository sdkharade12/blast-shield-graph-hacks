"""Connection utilities for the BlastShield graph database."""

import os
from contextlib import contextmanager
from typing import Any, Iterator

from falkordb import FalkorDB


DEFAULT_GRAPH_NAME = "blastshield"


def create_client() -> FalkorDB:
    """Create a FalkorDB client using the configured connection settings."""

    host = os.getenv("FALKORDB_HOST", "localhost")
    port = int(os.getenv("FALKORDB_PORT", "6379"))

    return FalkorDB(host=host, port=port)


@contextmanager
def graph_connection(
    graph_name: str | None = None,
) -> Iterator[Any]:
    """Provide access to a graph and close the client afterwards."""

    client = create_client()

    try:
        selected_graph = (
            graph_name
            or os.getenv("FALKORDB_GRAPH_NAME", DEFAULT_GRAPH_NAME)
        )

        graph = client.select_graph(selected_graph)
        yield graph

    finally:
        client.close()