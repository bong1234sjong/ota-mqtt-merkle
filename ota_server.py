import time
from paho.mqtt import client as mqtt_client
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes
import json
from typing import List, Union
import hashlib


FIRMWARE_TOPIC = "ota/firmware" 
NUM_CHUNKS = 4    
PORT = 1883

client_id = "ota-workstation-publisher"


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


def split_into_chunks(data: bytes, n: int = NUM_CHUNKS):
    size = len(data)
    if size < n:
        raise ValueError(
            f"Firmware is {size} bytes, but at least {n} bytes are needed "
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

def read_file_to_binary(filename):
    with open(filename, "rb") as file:
        data = file.read()
    return data

def compute_merkle_root(chunks: list[bytes]) -> str:
    merkletree = MerkleTree(chunks)
    return merkletree.getRootHash()

def process_firmware(data: bytes) -> tuple[list[bytes], str]:
    chunks = split_into_chunks(data)
    merkle_root = compute_merkle_root(chunks)
    return chunks, merkle_root

def generate_chunk_files(chunks):
    for i, chunk in enumerate(chunks):
        with open(f"chunk{i}", "wb") as file:
            file.write(chunk)


def manifest_create(version: str, original_size: int, root_hash: str, chunks: list, filenames: list):

    manifest = dict()
    
    manifest["version"] = str(version)
    manifest["original_size"] = original_size
    manifest["total_chunks"] = len(chunks)
    manifest["root_hash"] = root_hash
    
    if not (len(chunks) == len(filenames)):
        print("not matching length in filenames and chunks")
        return
    
    manifest["chunks"] = []
    
    for i in range(len(chunks)):
        
        chunk_data = {}
        chunk_data["index"], chunk_data["filename"]  = i, filenames[i]
        
        manifest["chunks"].append(chunk_data)
          
    return manifest
        
    
def json_manifest(manifest: dict):
    
    try:
        with open("manifest.json", "w") as file:
            json.dump(manifest, file, indent=4)
    
        return True
    except Exception as e:
        print("Error occured: ", e)
        return False
   

def connect_mqtt():
    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print("Connected to MQTT Broker!")
            
        else:
            print(f"Failed to connect, return code {reason_code}")
    

    client = mqtt_client.Client(
        client_id= "ota-workstation-publisher",
        callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
        protocol=mqtt_client.MQTTv5 # så vi kan bruge properties i publish
    )
    client.on_connect = on_connect
    client.connect("localhost", PORT)
    return client

def publish_manifest(client, manifest):
    properties = Properties(PacketTypes.PUBLISH)
    properties.ContentType = "application/json"
    payload = json.dumps(manifest)
    result = client.publish(FIRMWARE_TOPIC, payload, qos=2, properties=properties)
    result.wait_for_publish()

def publish_chunk(client, filename, data):
    properties = Properties(PacketTypes.PUBLISH)
    properties.ContentType = "application/octet-stream"
    properties.UserProperty = [("filename", filename)]
    result = client.publish(FIRMWARE_TOPIC, data, qos=2, properties=properties)
    result.wait_for_publish()
 
def publish(client, manifest, chunks):
    publish_manifest(client, manifest)
 
    # publish chunks with filename in UserProperty
    for entry in manifest["chunks"]:
        publish_chunk(client, entry["filename"], chunks[entry["index"]])

 
def send_firmware(manifest, chunks):
    client = connect_mqtt()
    client.loop_start()
    time.sleep(1) # give on_connect time to subscribe to ota/status
    publish(client, manifest, chunks)
    client.loop_stop()
    client.disconnect()