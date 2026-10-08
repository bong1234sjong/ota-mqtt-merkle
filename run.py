import argparse
from ota_server import send_firmware, manifest_create, json_manifest, read_file_to_binary, process_firmware, generate_chunk_files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("firmware")
    parser.add_argument("--version")
    args = parser.parse_args()

    # Read and split the firmware and compute the Merkle root
    data = read_file_to_binary(args.firmware)
    chunks, merkle_root = process_firmware(data)

    # Build the manifest
    filenames = [f"chunk{i}" for i in range(len(chunks))]
    manifest = manifest_create(args.version, len(data), merkle_root, chunks, filenames)
    if manifest is None:
        return
    json_manifest(manifest)

    # Save chunk files
    generate_chunk_files(chunks)
    send_firmware(manifest, chunks)


if __name__ == "__main__":
    main()