import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4


pdf_path = Path("/input.pdf")
boundary = f"paper-parser-{uuid4().hex}"
pdf_bytes = pdf_path.read_bytes()
body = b"".join(
    [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="file"; filename="minimal-paper.pdf"\r\n',
        b"Content-Type: application/pdf\r\n\r\n",
        pdf_bytes,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
)
request = Request(
    "http://paper-parser:8000/v1/parse",
    data=body,
    method="POST",
    headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Parser-Token": os.environ["PAPER_PARSER_API_TOKEN"],
    },
)
with urlopen(request, timeout=900) as response:
    parsed = json.load(response)
print(json.dumps({"page_count": parsed["page_count"], "element_count": len(parsed["elements"])}))
