from bson import ObjectId

from app.bson_utils import from_extended_json, to_extended_json


def test_extended_json_round_trip_preserves_object_id() -> None:
    object_id = "65f000000000000000000001"

    decoded = from_extended_json({"_id": {"$oid": object_id}})
    encoded = to_extended_json(decoded)

    assert isinstance(decoded["_id"], ObjectId)
    assert encoded == {"_id": {"$oid": object_id}}
