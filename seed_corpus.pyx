import os
import requests

FOLDER = "canonical_scrolls"
URL = "http://127.0.0.1:8000/upload_scroll"

for filename in os.listdir(FOLDER):

    path = os.path.join(FOLDER, filename)

    if not os.path.isfile(path):
        continue

    print("Uploading:", filename)

    with open(path, "rb") as f:

        files = {"scroll": (filename, f)}
        data = {"corpus_layer": "canonical"}

        r = requests.post(URL, files=files, data=data)

        print("Response:", r.text)
