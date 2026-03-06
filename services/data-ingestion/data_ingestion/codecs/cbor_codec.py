import cbor2


class CborCodec:
    content_type = "application/cbor"

    @staticmethod
    def encode(data: dict) -> bytes:
        return cbor2.dumps(data)

    @staticmethod
    def decode(raw: bytes) -> dict:
        return cbor2.loads(raw)
