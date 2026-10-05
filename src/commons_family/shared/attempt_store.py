"""Private received attempts remain on an owned spool, indexed by call identity."""

import base64
import tempfile
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from uuid import UUID

from .native import Attempt


class AttemptStore(MutableMapping[UUID, Attempt]):
    """Keep only offsets in memory; each lookup validates one actual attempt."""

    def __init__(self):
        self.stream = tempfile.NamedTemporaryFile(mode="w+b", prefix="commons-private-attempt-", delete=False)
        self.path = Path(self.stream.name)
        self.offsets: dict[UUID, tuple[int, int]] = {}

    def __getitem__(self, identity: UUID) -> Attempt:
        offset, size = self.offsets[identity]
        if self.stream.closed:
            with self.path.open("rb") as reader:
                reader.seek(offset)
                data = reader.read(size)
        else:
            self.stream.seek(offset)
            data = self.stream.read(size)
        return Attempt.model_validate_json(data)

    def __setitem__(self, identity: UUID, attempt: Attempt) -> None:
        if str(identity) != attempt.attempt_id:
            raise ValueError("attempt spool identity differs from received call")
        data = attempt.model_dump_json().encode("utf-8")
        self.stream.seek(0, 2)
        offset = self.stream.tell()
        self.stream.write(data)
        self.stream.flush()
        self.offsets[identity] = (offset, len(data))

    def __delitem__(self, identity: UUID) -> None:
        del self.offsets[identity]

    def __iter__(self) -> Iterator[UUID]:
        return iter(self.offsets)

    def __len__(self) -> int:
        return len(self.offsets)

    def receive(self, identity: UUID, attempt: Attempt) -> None:
        """Started facts may grow; observed bytes and identities cannot be rewritten."""
        prior = self.get(identity)
        if prior is not None:
            if prior.response_reader_joined is True and attempt != prior:
                raise ValueError("native progress rewrites joined response evidence")
            for name in (
                "request",
                "slot",
                "purpose",
                "stage",
                "timeout_ms",
                "endpoint",
                "http_status",
                "platform_call_id",
                "provider_request_id",
                "model_identity",
                "tokenizer_identity",
                "chat_template_sha256",
                "model",
                "usage",
                "sampling_evidence",
                "stop_reason",
                "response",
            ):
                observed = getattr(prior, name)
                if observed is not None and getattr(attempt, name) != observed:
                    raise ValueError("native progress rewrites observed request or response facts")
            if prior.http_status is not None and attempt.received_header_pairs != prior.received_header_pairs:
                raise ValueError("native progress rewrites actual received header pairs")
            if prior.http_status is not None and attempt.response_headers != prior.response_headers:
                raise ValueError("native progress rewrites received normalized headers")
            if prior.response_body_b64 is not None:
                if attempt.response_body_b64 is None or not base64.b64decode(
                    attempt.response_body_b64, validate=True
                ).startswith(base64.b64decode(prior.response_body_b64, validate=True)):
                    raise ValueError("native progress rewrites received body prefix")
            if prior.response_complete is True and (
                attempt.response_complete is not True or attempt.response_body_b64 != prior.response_body_b64
            ):
                raise ValueError("native progress rewrites complete received bytes")
        self[identity] = attempt

    def close_joined(self) -> None:
        self.stream.close()
