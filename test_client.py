"""Simple tests for ota_client.py. No MQTT broker needed.

Run:  python test_client.py
"""
import json
import os
import tempfile
import time
import types

import ota_client
from ota_client import OtaClient, Reject
from firmware_processor import compute_merkle_root

# ---------- helpers ----------

FIRMWARE = b"This is a small test firmware file. " * 5 + b"END"   # size not divisible by 4
TMP = tempfile.mkdtemp()
ota_client.STATUS_FILE = os.path.join(TMP, "ota_status.json")   # don't touch real status file


def split4(data):
    n = len(data) // 4
    return [data[0:n], data[n:2*n], data[2*n:3*n], data[3*n:]]


CHUNKS = split4(FIRMWARE)
NAMES = [f"firmware_chunk_{i}.bin" for i in range(4)]
ROOT = compute_merkle_root(CHUNKS)


def make_manifest(**changes):
    m = {
        "version": "1.0.0",
        "original_size": len(FIRMWARE),
        "total_chunks": 4,
        "root_hash": ROOT,
        "chunks": [{"index": i, "filename": NAMES[i]} for i in range(4)],
    }
    m.update(changes)
    return m


def manifest_bytes(**changes):
    return json.dumps(make_manifest(**changes)).encode()


def new_client():
    c = OtaClient(timeout=10)
    c.output_path = os.path.join(TMP, "firmware_reconstructed.txt")
    if os.path.exists(c.output_path):
        os.remove(c.output_path)
    return c


def step(c, action):
    """Do one action the same way run() does: handle errors, then check if done."""
    try:
        action()
        if c.deadline is not None and time.monotonic() > c.deadline:
            raise Reject("timeout")
        if c.total_chunks is not None and len(c.chunks) == c.total_chunks:
            c.verify_and_install()
    except Reject as e:
        c.reject(str(e))


def send_manifest(c, payload=None):
    step(c, lambda: c.handle_manifest(payload if payload is not None else manifest_bytes()))


def send_chunk(c, i, data=None, name=None):
    step(c, lambda: c.handle_chunk(name if name is not None else NAMES[i],
                                   data if data is not None else CHUNKS[i]))


def fake_msg(content_type, payload, filename=None):
    props = types.SimpleNamespace(ContentType=content_type)
    if filename is not None:
        props.UserProperty = [("filename", filename)]
    return types.SimpleNamespace(payload=payload, properties=props)


def status():
    with open(ota_client.STATUS_FILE) as f:
        return json.load(f)


results = []


def test(name):
    def deco(fn):
        print(f"\nTesting: {name}")
        try:
            fn()
            print("  -> test passed")
            results.append(True)
        except AssertionError as e:
            print(f"  -> TEST FAILED: {e}")
            results.append(False)
        except Exception as e:
            print(f"  -> TEST CRASHED: {type(e).__name__}: {e}")
            results.append(False)
        return fn
    return deco


def assert_rejected(c, reason_part=None):
    assert not os.path.exists(c.output_path), "firmware file was created"
    assert c.done, "client did not stop"
    assert not c.success, "client reported success"
    assert status()["result"] == "rejected", "status is not 'rejected'"
    if reason_part:
        assert reason_part in status()["reason"], f"reason was: {status()['reason']!r}"
    assert c.chunks == {}, "chunks were not cleared"


# ---------- tests ----------

@test("happy path: manifest + 4 chunks in order")
def _():
    c = new_client()
    send_manifest(c)
    for i in range(4):
        send_chunk(c, i)
    assert c.success, "update was not accepted"
    with open(c.output_path, "rb") as f:
        assert f.read() == FIRMWARE, "reconstructed file differs from original"
    assert status()["result"] == "ok"


@test("chunks arrive out of order (stored by index)")
def _():
    c = new_client()
    send_manifest(c)
    for i in (2, 0, 3, 1):
        send_chunk(c, i)
    assert c.success
    with open(c.output_path, "rb") as f:
        assert f.read() == FIRMWARE, "chunks were joined in arrival order"


@test("chunks arrive BEFORE the manifest")
def _():
    c = new_client()
    send_chunk(c, 1)
    send_chunk(c, 3)
    assert len(c.chunks) == 0 and len(c.unplaced) == 2, "early chunks not held"
    send_manifest(c)
    send_chunk(c, 0)
    send_chunk(c, 2)
    assert c.success


@test("duplicate chunk is not counted twice")
def _():
    c = new_client()
    send_manifest(c)
    send_chunk(c, 0)
    send_chunk(c, 0)
    send_chunk(c, 0)
    assert len(c.chunks) == 1, f"counted {len(c.chunks)} chunks"
    assert not c.done
    for i in (1, 2, 3):
        send_chunk(c, i)
    assert c.success


@test("duplicate manifest is ignored")
def _():
    c = new_client()
    send_manifest(c)
    send_chunk(c, 0)
    send_manifest(c)                      # same manifest again
    assert 0 in c.chunks, "duplicate manifest wiped the chunks"
    for i in (1, 2, 3):
        send_chunk(c, i)
    assert c.success


@test("missing chunk -> timeout -> reject")
def _():
    c = new_client()
    send_manifest(c)
    for i in (0, 1, 2):
        send_chunk(c, i)
    assert not c.done
    c.deadline = time.monotonic() - 1     # pretend the timer ran out
    step(c, lambda: None)
    assert_rejected(c, "timeout")


@test("corrupted chunk -> Merkle root mismatch -> reject")
def _():
    c = new_client()
    send_manifest(c)
    bad = bytearray(CHUNKS[2])
    bad[0] ^= 0x01                        # flip one bit, same length
    for i in range(4):
        send_chunk(c, i, data=bytes(bad) if i == 2 else None)
    assert_rejected(c, "Merkle root mismatch")


@test("two different copies of the same chunk -> reject")
def _():
    c = new_client()
    send_manifest(c)
    send_chunk(c, 0)
    send_chunk(c, 0, data=b"something else")
    assert_rejected(c, "two different copies")


@test("chunk filename not in manifest -> reject")
def _():
    c = new_client()
    send_manifest(c)
    send_chunk(c, 0, name="evil_chunk.bin")
    assert_rejected(c, "not in the manifest")


@test("chunk without filename -> reject")
def _():
    c = new_client()
    send_manifest(c)
    step(c, lambda: c.handle_chunk(None, CHUNKS[0]))
    assert_rejected(c, "without a filename")


@test("size in manifest is wrong -> reject")
def _():
    c = new_client()
    send_manifest(c, manifest_bytes(original_size=len(FIRMWARE) + 10))
    for i in range(4):
        send_chunk(c, i)
    assert_rejected(c, "size mismatch")


@test("manifest has wrong Merkle root -> reject")
def _():
    c = new_client()
    send_manifest(c, manifest_bytes(root_hash="0" * 64))
    for i in range(4):
        send_chunk(c, i)
    assert_rejected(c, "Merkle root mismatch")


@test("manifest is not valid JSON -> reject")
def _():
    c = new_client()
    send_manifest(c, b"this is not json {{{")
    assert_rejected(c, "not valid JSON")


@test("manifest is missing a field -> reject")
def _():
    c = new_client()
    m = make_manifest()
    del m["root_hash"]
    send_manifest(c, json.dumps(m).encode())
    assert_rejected(c, "missing 'root_hash'")


@test("manifest with only 3 chunks -> reject")
def _():
    c = new_client()
    m = make_manifest(total_chunks=3, chunks=make_manifest()["chunks"][:3])
    send_manifest(c, json.dumps(m).encode())
    assert_rejected(c, "expected 4")


@test("manifest with bad chunk indices (0,1,2,2) -> reject")
def _():
    c = new_client()
    chunks = make_manifest()["chunks"]
    chunks[3]["index"] = 2
    send_manifest(c, manifest_bytes(chunks=chunks))
    assert_rejected(c, "chunk indices")


@test("manifest with duplicate filenames -> reject")
def _():
    c = new_client()
    chunks = make_manifest()["chunks"]
    chunks[3]["filename"] = chunks[0]["filename"]
    send_manifest(c, manifest_bytes(chunks=chunks))
    assert_rejected(c, "appears twice")


@test("manifest with root_hash of wrong length -> reject")
def _():
    c = new_client()
    send_manifest(c, manifest_bytes(root_hash="abc123"))
    assert_rejected(c, "64 hex")


@test("full MQTT-style messages through handle_message (content type + user property)")
def _():
    c = new_client()
    c.handle_message(fake_msg("application/json", manifest_bytes()))
    for i in (3, 1, 0, 2):
        step(c, lambda i=i: c.handle_message(
            fake_msg("application/octet-stream", CHUNKS[i], filename=NAMES[i])))
    assert c.success


@test("message with unknown content type is ignored")
def _():
    c = new_client()
    c.handle_message(fake_msg("image/png", b"junk"))
    assert not c.done and c.firmware_version is None and c.chunks == {}
    assert c.deadline is None, "unknown message started the timer"


@test("timer starts on first message and is not extended by later ones")
def _():
    c = new_client()
    assert c.deadline is None, "timer already running before any message"
    c.handle_message(fake_msg("application/octet-stream", CHUNKS[0], filename=NAMES[0]))
    first = c.deadline
    assert first is not None, "timer did not start on first chunk"
    c.handle_message(fake_msg("application/json", manifest_bytes()))
    assert c.deadline == first, "later message moved the deadline"


@test("timer also starts when the first message is the manifest")
def _():
    c = new_client()
    c.handle_message(fake_msg("application/json", manifest_bytes()))
    assert c.deadline is not None, "timer did not start on manifest"


@test("no firmware file is left behind after a reject")
def _():
    c = new_client()
    with open(c.output_path, "wb") as f:       # pretend an old file exists
        f.write(b"old")
    send_manifest(c, manifest_bytes(root_hash="f" * 64))
    for i in range(4):
        send_chunk(c, i)
    assert_rejected(c)


# ---------- summary ----------

print("\n" + "=" * 40)
print(f"{sum(results)}/{len(results)} tests passed")
print("ALL TESTS PASSED" if all(results) else "SOME TESTS FAILED")