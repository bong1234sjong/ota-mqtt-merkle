from manifest_generator import *
import time
from paho.mqtt import client as mqtt_client
import hashlib, os, random, json
    

""" Send File Using MQTT """
broker="localhost"
topic="firmwaretest/files"
port=1883
client_id = f'python-mqtt-{random.randint(0, 1000)}'

def chunkdata(firmware : str):
    data = read_file_to_binary("firmware.txt")
    chunks, merkle_root = process_firmware(data)
    filenames = [f"chunk{i}" for i in range(len(chunks))]
    manifest = manifest_create("1.3", len(data), merkle_root, chunks, filenames)
    json_manifest(manifest)
    generate_chunk_files(chunks)

def connect_mqtt():
    def on_connect(client, userdata, flags, reason_code, properties):
        # For paho-mqtt 1.x, the signature is: on_connect(client, userdata, flags, rc)
        if reason_code == 0:
            print("Connected to MQTT Broker!")
        else:
            print(f"Failed to connect, return code {reason_code}")

    client = mqtt_client.Client(
        client_id=client_id,
        callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
    )
    client.on_connect = on_connect
    client.connect(broker, port)
    return client

def publish(client):
    msg_count = 1
    while True:
        time.sleep(1)
        msg = ""
        result = client.publish(topic, msg)
        # result: [0, 1]
        status = result[0]
        if status == 0:
            print(f"Sent `{msg}` to topic `{topic}`")
        else:
            print(f"Failed to send message to topic {topic}")
        msg_count += 1
        if msg_count > 5:
            break

def run(filename:str):
    chunkdata(filename)
    client = connect_mqtt()
    client.loop_start()
    publish(client)
    client.loop_stop()
    client.disconnect()


if __name__ == '__main__':
    run("firmware.txt")