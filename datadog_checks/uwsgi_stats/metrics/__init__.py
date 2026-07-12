from .aggregate import collect as collect_aggregate
from .apps import collect as collect_apps
from .caches import collect as collect_caches
from .cores import collect as collect_cores
from .sockets import collect as collect_sockets
from .spoolers import collect as collect_spoolers
from .workers import collect as collect_workers

COLLECTORS = [
    collect_aggregate,
    collect_sockets,
    collect_workers,
    collect_apps,
    collect_caches,
    collect_spoolers,
    collect_cores,
]
