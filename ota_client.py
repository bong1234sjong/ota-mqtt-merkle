import json
import os


FIRMWARE_TOPIC = "ota/firmware"
STATUS_TOPIC = "ota/status"
QOS = 1
TYPE_MANIFEST = "application/json"
EXPECTED_CHUNKS = 4
CHUNK_TYPES = ("application/octet-stream", "text/plain")

OUTPUT_FILE = "firmware_reconstructed.txt"
STATUS_FILE = "ota_status.json"
 
MAX_ATTEMPTS = 3          

class Reject(Exception):
    """Raised with a reason whenever the current attempt must be rejected."""

# ------------------------------------------------------------------ hashing
class OtaClient:
    def __init__(self, max_attempts = MAX_ATTEMPTS):
        self.max_attempts = max_attempts

        self.attempt = 1
        self.done = False
        self.success = False

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


    def handle_message(self, msg):
        properties = getattr(msg, "properties", None)
        content_type = getattr(properties, "ContentType", None)
        if content_type == TYPE_MANIFEST:
            self.handle_manifest(msg.payload)
        elif content_type in CHUNK_TYPES:
            filename =self.user_property(msg, "filename")
            self.handle_chunk(filename, msg.payload)
        else:
            print("Ignoring message with unknown content type %r", content_type)



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
        for field in ("firmware_version", "firmware_size", "total_chunks", "merkle_root", "chunks"):
            if field not in m:
                raise Reject(f"manifest is missing '{field}'")

        # Version and size must have the right types
        if not isinstance(m["firmware_version"], str):
            raise Reject("firmware_version must be text")
        if not isinstance(m["firmware_size"], int):
            raise Reject("firmware_size must be a whole number")

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
        root = m["merkle_root"]
        if not isinstance(root, str) or len(root) != 64:
            raise Reject("merkle_root must be 64 hex characters")
        return m

    def handle_manifest(self, payload: bytes):
        # make manifest into a dict
        m = self.parse_manifest(payload)
        if m is None:
            raise Reject("invalid manifest")

        if self.firmware_version is not None:
            if (m["firmware_version"], m["merkle_root"].lower()) == (self.firmware_version, self.manifest_root):
                print("Duplicate manifest ignored")
                return
            # new manifest, so replace
            self.reset()

        # Save the manifest's values used to do checks.
        self.firmware_version = m["firmware_version"]
        self.firmware_size = m["firmware_size"]
        self.total_chunks = m["total_chunks"]
        self.manifest_root = m["merkle_root"].lower()
        self.index_of = {c["filename"]: c["index"] for c in m["chunks"]}
        

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
                print("Chunk %s arrived before the manifest; holding it", filename)
            return
        self.store_chunk(filename, data)

    def store_chunk(self, filename: str, data: bytes) -> None:
        if filename not in self.index_of:
            raise Reject(f"received chunk file {filename!r}, which is not in the manifest")

        index = self.index_of[filename]
        if index in self.chunks:
            if self.chunks[index] == data:
                print("Duplicate of chunk %d (%s) ignored", index, filename)
                return
            raise Reject(f"two different copies of chunk {index} ({filename}) received")

        self.chunks[index] = data
        print("Chunk %d stored from %s (%d bytes) - %d/%d received",
                 index, filename, len(data), len(self.chunks), self.total_chunks)


    def verify_and_install(self) -> None:
        # Indices are exactly 0, 1, 2, 3
        indices = sorted(self.chunks)
        if indices != list(range(EXPECTED_CHUNKS)):
            raise Reject(f"received chunk indices {indices}, expected [0, 1, 2, 3]")

        # Merkle root from the received chunks equals the manifest's root
        ordered = [self.chunks[i] for i in indices]
        computed_root = merkle_root([sha256(c) for c in ordered]).hex()
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

    def reject(self, reason: str) -> None:
        # remove recieved files
        for path in (self.output_path, self.output_path + ".part"):
            if os.path.exists(path):
                os.remove(path)

        self.send_status("rejected", reason)
        self.reset()   # clear the received chunks
