import socket
import json

from .initialize import *

from .metrics import *

def main(address='127.0.0.1', port=1717, gulp=4096):
    with socket.create_connection((address, port)) as s:
        data = b''
        while received := s.recv(gulp):
            data += received

    structure = json.loads(data.decode())

    total_queue_depth(structure)

if __name__ == "__main__":
    main()
