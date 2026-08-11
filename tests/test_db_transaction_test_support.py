import asyncio

from tests.support.db_transactions import OpaqueConnection, opaque_connection_transaction, opaque_transaction


def test_opaque_transaction_yields_a_fresh_connection_per_context():
    async def enter_transaction() -> object:
        async with opaque_transaction() as connection:
            return connection

    first = asyncio.run(enter_transaction())
    second = asyncio.run(enter_transaction())

    assert type(first) is object
    assert type(second) is object
    assert first is not second


def test_opaque_connection_transaction_yields_a_fresh_typed_marker_per_context():
    async def enter_transaction() -> OpaqueConnection:
        async with opaque_connection_transaction() as connection:
            return connection

    first = asyncio.run(enter_transaction())
    second = asyncio.run(enter_transaction())

    assert isinstance(first, OpaqueConnection)
    assert isinstance(second, OpaqueConnection)
    assert first is not second
