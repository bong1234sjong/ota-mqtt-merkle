from firmware_processor import * 
from manifest import *

def main():
    data = read_file_to_binary("firmware.txt")
    
    chunks, merkle_root = process_firmware(data)
    
    filenames = [f"chunk{i}" for i in range(len(chunks))]
    
    manifest = manifest_create("1.3", len(data), merkle_root, chunks, filenames)
    
    json_manifest(manifest)
    
main()