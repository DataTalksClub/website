from dataclasses import dataclass
from typing import Any

import requests

from ..client import DatamailerClient
from ..payloads.send import (
    recipient_list_member_sync_payload,
)


@dataclass(frozen=True)
class RecipientListBulkUpsertData:
    config: Any
    list_key: str
    payload: dict[str, Any]


def bulk_upsert_recipient_list_members_before_send(data) -> bool:
    """Sync a recipient list's members directly before a send.

    The datamailer outbox used to carry this upsert with
    ``dispatch_immediately`` so the send could act on the synchronous
    ACK; the direct call keeps that contract without the queue. A
    transport failure returns ``False`` so the caller records the
    not-acknowledged audit and skips the send, exactly as before.
    """

    client = DatamailerClient(data.config)
    member_sync_payload = recipient_list_member_sync_payload(
        data.config,
        data.payload,
    )
    try:
        response = client.recipient_lists.members.bulk_upsert(
            data.list_key,
            member_sync_payload,
        )
    except requests.RequestException:
        return False
    return response is not None
