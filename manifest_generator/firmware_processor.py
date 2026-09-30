"""
Steps:
  1. Read firmware.txt as raw bytes.
  2. Split it into exactly four ordered, non-empty chunks C0..C3.
  3. Hash each chunk with SHA-256 -> leaf nodes H0..H3.
  4. Build a binary Merkle tree from the leaves (ordered pairs) up to a single root.
  5. Write the chunk files and a manifest.json describing them.

Chunking rule (file size not divisible by four):
Let N be the file size in bytes, q = N // 4 and r = N % 4 (r is 0..3).
The first r chunks get q + 1 bytes and the remaining 4 - r chunks get q bytes.
Chunks are contiguous and in file order, so C0 + C1 + C2 + C3 == original file.

  Example: N = 10 -> q = 2, r = 2 -> sizes [3, 3, 2, 2]

Every chunk is non-empty only when N >= 4, so smaller files are rejected.

Merkle tree
-----------
  Leaf:    H_i   = SHA256(C_i)
  Parent:  H_ab  = SHA256(H_a || H_b)   

            root = SHA256(H01 || H23)
               /                   \\
    H01 = SHA256(H0||H1)   H23 = SHA256(H2||H3)
        /      \\               /      \\
      H0        H1           H2        H3
"""


import hashlib
import json
import os
import sys
from merkletree import MerkleTree
NUM_CHUNKS = 4


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def split_into_chunks(data: bytes, n: int = NUM_CHUNKS):
    size = len(data)
    if size < n:
        raise ValueError(
            f"Firmware is {size} bytes; at least {n} bytes are needed "
            f"to produce {n} non-empty chunks."
        )
    q = size // n
    r = size % n
    chunks = []
    offset = 0
    for i in range(n):
        length = q + 1 if i < r else q
        chunks.append(data[offset:offset + length])
        offset += length
    return chunks

def build_merkle_tree(leaves: list[bytes]) -> MerkleTree:
    return MerkleTree(leaves)
    

def read_file_to_binary(filename):
    with open(filename, "rb") as file:
        data = file.read()
    return data

def process_firmware(data: bytes) -> tuple[list[bytes], str]:
    chunks = split_into_chunks(data)
    leaves = [sha256(c) for c in chunks]
    merkletree: MerkleTree = build_merkle_tree(leaves)
    merkle_root = merkletree.getRootHash()
    return chunks, merkle_root

def generate_chunk_files(chunks):
    for i, chunk in enumerate(chunks):
        with open(f"chunk{i}", "wb") as file:
            file.write(chunk)
            
        