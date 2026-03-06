import json


class ProtobufCodec:
    """Protobuf codec. Uses JSON as intermediate format until proto stubs are generated."""

    content_type = "application/protobuf"

    @staticmethod
    def encode(data: dict) -> bytes:
        # TODO: Use generated protobuf stubs once available
        return json.dumps(data).encode("utf-8")

    @staticmethod
    def decode(raw: bytes) -> dict:
        # TODO: Use generated protobuf stubs once available
        return json.loads(raw.decode("utf-8"))
