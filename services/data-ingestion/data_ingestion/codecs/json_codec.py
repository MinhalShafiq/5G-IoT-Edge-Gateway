import json


class JsonCodec:
    content_type = "application/json"

    @staticmethod
    def encode(data: dict) -> bytes:
        return json.dumps(data).encode("utf-8")

    @staticmethod
    def decode(raw: bytes) -> dict:
        return json.loads(raw.decode("utf-8"))
