import json

def manifest_create(version: str, original_size: float, root_hash: str, chunks: list, filenames: list):

    manifest = dict()
    
    manifest["version"] = version
    manifest["original_size"] = original_size
    manifest["total_chunks"] = len(chunks)
    manifest["root_hash"] = root_hash
    
    if not (len(chunks) == len(filenames)):
        print("not matching length in filenames and chunks")
        return
    
    manifest["data"] = []
    
    for i in range(len(chunks)):
        
        chunk_data = {}
        chunk_data["index"], chunk_data["filename"]  = i, filenames[i]
        
        manifest["data"].append(chunk_data)
          
    return manifest
        
    
def json_manifest(manifest: dict):
    
    try:
        with open("manifest.json", "w") as file:
            json.dump(manifest, file, indent=4)
    
        return True
    except Exception as e:
        print("Error occured: ", e)
        return False
    
def example():
    
    manifest = manifest_create("1.2", 2.2, "hajsdkh", ["aksdsa", "asddas", "asdsdasd"], ["joe", "jim", "peter"])
    return json_manifest(manifest)
