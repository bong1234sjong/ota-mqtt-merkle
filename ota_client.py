import argparse
import json
import os
import queue
import random
import time
from paho.mqtt import client as mqtt_client
from typing import List, Union
import hashlib

BROKER = "localhost"
PORT = 1883
CLIENT_ID = f'ota-pi-{random.randint(0, 1000)}'
QOS = 2
TYPE_MANIFEST = "application/json"
EXPECTED_CHUNKS = 4
CHUNK_TYPES = ("application/octet-stream", "text/plain")

OUTPUT_FILE = "firmware_reconstructed.txt"
STATUS_FILE = "ota_status.json"

TIMEOUT = 10.0  


class Node:
    def __init__(self, left, right, value: str, content, is_copied=False) -> None:
        self.left: Node = left
        self.right: Node = right
        self.value = value
        self.content = content
        self.is_copied = is_copied

    @staticmethod
    def hash(val: Union[str, bytes]) -> str:
        if isinstance(val, str):
            val = val.encode('utf-8')
        return hashlib.sha256(val).hexdigest()

    def __str__(self):
        return str(self.value)

    def copy(self):
        return Node(self.left, self.right, self.value, self.content, True)


class MerkleTree:
    def __init__(self, values: List[Union[str, bytes]]) -> None:
        self.__buildTree(values)

    def __buildTree(self, values: List[Union[str, bytes]]) -> None:
        leaves: List[Node] = [Node(None, None, Node.hash(e), e) for e in values]
        if len(leaves) % 2 == 1:
            leaves.append(leaves[-1].copy())
        self.root: Node = self.__buildTreeRec(leaves)

    def __buildTreeRec(self, nodes: List[Node]) -> Node:
        if len(nodes) % 2 == 1:
            nodes.append(nodes[-1].copy())
        half: int = len(nodes) // 2

        if len(nodes) == 2:
            return Node(
                nodes[0], 
                nodes[1], 
                Node.hash(nodes[0].value + nodes[1].value), 
                f"{nodes[0].content}+{nodes[1].content}"
            )

        left: Node = self.__buildTreeRec(nodes[:half])
        right: Node = self.__buildTreeRec(nodes[half:])
        value: str = Node.hash(left.value + right.value)
        content: str = f"{left.content}+{right.content}"
        return Node(left, right, value, content)

    def getRootHash(self) -> str:
        return self.root.value
    
def compute_merkle_root(chunks: list[bytes]) -> str:
    merkletree = MerkleTree(chunks)
    return merkletree.getRootHash()

class Reject(Exception):
    """Raised with a reason whenever the current attempt must be rejected."""

class OtaClient:
    def __init__(self, broker = BROKER, timeout = TIMEOUT):
        self.broker = broker
        self.timeout = timeout
        self.output_path = OUTPUT_FILE

        self.done = False
        self.success = False
        # messages from MQTT
        self.inbox = queue.Queue()  

        # (filename, bytes) that came before the manifest
        self.unplaced = []
        self.reset()

    def reset(self) -> None:
        """Forget the manifest and all chunks of the current attempt."""
        # Values saved from the manifest
        self.firmware_version = None
        self.firmware_size = None
        self.total_chunks = None
        self.manifest_root = None
        self.index_of = {}

        self.chunks = {}
        self.deadline = None   # måske skulle det en timeout starter når man modtager første chunk

    def connect_mqtt(self):
        def on_connect(client, userdata, flags, reason_code, properties):
            if reason_code == 0:
                print("Connected to MQTT Broker!")
                client.subscribe("ota/firmware", qos=QOS)
            else:
                print(f"Failed to connect, return code {reason_code}")

        def on_message(client, userdata, msg):
            self.inbox.put(msg)        

        client = mqtt_client.Client(
            client_id=CLIENT_ID,
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
            protocol=mqtt_client.MQTTv5     # needed for content type and user properties
        )
        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(self.broker, PORT)
        return client

    def save_status(self, result: str, reason: str) -> None:
        status = {"result": result, "reason": reason, "version": self.firmware_version}
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f, indent=4)
        print(f"Status saved: {result} ({reason})")

    def run(self) -> None:
        self.client = self.connect_mqtt()
        self.client.loop_start()

        while not self.done:
            try:
                msg = self.inbox.get(timeout=0.2)
            except queue.Empty:
                msg = None

            try:
                if msg is not None:
                    self.handle_message(msg)
                if self.deadline is not None and time.monotonic() > self.deadline:
                    raise Reject(f"timeout")
                # if we have all the chunks, verify and install
                if self.total_chunks is not None and len(self.chunks) == self.total_chunks:
                    self.verify_and_install()
            except Reject as e:
                self.reject(str(e))
        self.client.loop_stop()
        self.client.disconnect()


    def start_timer(self) -> None:
        if self.deadline is None:
            self.deadline = time.monotonic() + self.timeout

    
    def handle_message(self, msg):
        properties = getattr(msg, "properties", None)
        content_type = getattr(properties, "ContentType", None)
        if content_type == TYPE_MANIFEST:
            self.start_timer()
            self.handle_manifest(msg.payload)
        elif content_type in CHUNK_TYPES:
            filename =self.user_property(msg, "filename")
            self.start_timer()
            self.handle_chunk(filename, msg.payload)
        else:
            print(f"Ignoring message with unknown content type {content_type}")


    @staticmethod
    def user_property(msg, name):
        # No user properties on this message at all
        if not hasattr(msg.properties, "UserProperty"):
            return None

        # UserProperty is a list of (name, value) pair
        for pair in msg.properties.UserProperty:
            key = pair[0]       # the name,  e.g. "filename"
            value = pair[1]     # the value, e.g. "firmware_chunk_2.bin"
            if key == name:
                return value
        return None
    
    @staticmethod
    def parse_manifest(payload):
        """Read the manifest and check that it has everything the client needs.
        Returns the manifest as a dict, or raises Reject saying what is wrong."""
        # check that it's a JSON object
        try:
            m = json.loads(payload)
        except ValueError:
            raise Reject("manifest is not valid JSON")
        if not isinstance(m, dict):
            raise Reject("manifest is not a JSON object")

        # All required fields must be there
        for field in ("version", "original_size", "total_chunks", "root_hash", "chunks"):
            if field not in m:
                raise Reject(f"manifest is missing '{field}'")

        # Version and size must have the right types
        if not isinstance(m["version"], str):
            raise Reject("version must be text")
        if not isinstance(m["original_size"], int):
            raise Reject("original_size must be a whole number")

        # There must be exactly 4 chunks
        if not isinstance(m["chunks"], list):
            raise Reject("chunks must be a list")
        if m["total_chunks"] != EXPECTED_CHUNKS:
            raise Reject(f"total_chunks is {m['total_chunks']}, expected {EXPECTED_CHUNKS}")
        if len(m["chunks"]) != EXPECTED_CHUNKS:
            raise Reject(f"manifest lists {len(m['chunks'])} chunks, expected {EXPECTED_CHUNKS}")

        # Every chunk entry needs an index and a filename
        for entry in m["chunks"]:
            if not isinstance(entry, dict):
                raise Reject("a chunk entry is not a JSON object")
            if "index" not in entry or "filename" not in entry:
                raise Reject("a chunk entry is missing 'index' or 'filename'")
            if not isinstance(entry["index"], int):
                raise Reject("a chunk index is not a whole number")
            if not isinstance(entry["filename"], str) or entry["filename"] == "":
                raise Reject("a chunk filename is empty or not text")

        # The indices must all be there
        indices = sorted(entry["index"] for entry in m["chunks"])
        if indices != list(range(EXPECTED_CHUNKS)):
            raise Reject(f"chunk indices are {indices}, expected [0, 1, 2, 3]")

        # No filename may appear twice
        filenames = [entry["filename"] for entry in m["chunks"]]
        if len(set(filenames)) != len(filenames):
            raise Reject("the same chunk filename appears twice in the manifest")

        # The Merkle root must be a SHA-256 hash, so 64 hex characters
        root = m["root_hash"]
        if not isinstance(root, str) or len(root) != 64:
            raise Reject("root_hash must be 64 hex characters")
        return m

    def handle_manifest(self, payload: bytes):
        # make manifest into a dict
        m = self.parse_manifest(payload)

        if self.firmware_version is not None:
            if (m["version"], m["root_hash"].lower()) == (self.firmware_version, self.manifest_root):
                print("Duplicate manifest ignored")
                return
            # new manifest, so replace
            self.reset()

        # Save the manifest's values used to do checks.
        self.firmware_version = m["version"]
        self.firmware_size = m["original_size"]
        self.total_chunks = m["total_chunks"]
        self.manifest_root = m["root_hash"].lower()
        self.index_of = {c["filename"]: c["index"] for c in m["chunks"]}
        print(f"Manifest v{self.firmware_version}: {self.firmware_size} bytes, {self.total_chunks} chunks")

        # Place chunks that arrived before the manifest.
        early = self.unplaced
        self.unplaced = []
        for filename, data in early:
            self.store_chunk(filename, data)

    def handle_chunk(self, filename, data: bytes) -> None:
        if filename is None:
            raise Reject("received a chunk without a filename")
        if self.firmware_version is None:
            if len(self.unplaced) < EXPECTED_CHUNKS:
                self.unplaced.append((filename, data))
                print(f"Chunk {filename} arrived before the manifest; holding it")
            return
        self.store_chunk(filename, data)

    def store_chunk(self, filename: str, data: bytes) -> None:
        if filename not in self.index_of:
            raise Reject(f"received chunk file {filename!r}, which is not in the manifest")

        index = self.index_of[filename]
        if index in self.chunks:
            if self.chunks[index] == data:
                print(f"Duplicate of chunk {index} ({filename}) ignored")
                return
            raise Reject(f"two different copies of chunk {index} ({filename}) received")

        self.chunks[index] = data

    def verify_and_install(self) -> None:
        # Indices are exactly 0, 1, 2, 3
        indices = sorted(self.chunks)
        if indices != list(range(EXPECTED_CHUNKS)):
            raise Reject(f"received chunk indices {indices}, expected [0, 1, 2, 3]")
    
        # Merkle root from the received chunks equals the manifest's root
        ordered = [self.chunks[i] for i in indices]
        computed_root = compute_merkle_root(ordered)        # same function the server used
        print(f"Computed root {computed_root}")
        print(f"Manifest root {self.manifest_root}")
        if computed_root != self.manifest_root:
            raise Reject("Merkle root mismatch: a chunk is corrupted or wrong")
    
        # Reconstructed size equals the manifest's firmware_size
        firmware = b"".join(ordered)
        if len(firmware) != self.firmware_size:
            raise Reject(
                f"size mismatch: reconstructed {len(firmware)} bytes, "
                f"manifest says {self.firmware_size}")
    
        with open(self.output_path, "wb") as f:
            f.write(firmware)
        print(f"Saved firmware to {self.output_path}, version {self.firmware_version}, size {len(firmware)} bytes")
        self.save_status("ok", "all checks passed")
        self.success = True
        self.done = True

    def reject(self, reason: str) -> None:
        print(f"Update REJECTED: {reason}")
        # make sure no firmware file is left behind
        if os.path.exists(self.output_path):
            os.remove(self.output_path)

        self.save_status("rejected", reason)   # record the reason
        self.reset()                           # clear the received chunks
        self.done = True     
                          # no resend is coming, so stop

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Raspberry Pi OTA client.")
    parser.add_argument("--broker", default=BROKER, help="IP address of the PC running the broker")
    args = parser.parse_args()

    client = OtaClient(broker=args.broker)
    client.run()