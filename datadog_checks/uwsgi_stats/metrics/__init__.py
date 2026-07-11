from .aggregate import collect as collect_aggregate
from .apps import collect as collect_apps
from .sockets import collect as collect_sockets
from .workers import collect as collect_workers

COLLECTORS = [
    collect_aggregate,
    collect_sockets,
    collect_workers,
    collect_apps,
]
