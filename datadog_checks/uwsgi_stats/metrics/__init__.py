from .aggregate import collect as collect_aggregate
from .sockets import collect as collect_sockets

COLLECTORS = [
    collect_aggregate,
    collect_sockets,
]
