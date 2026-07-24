from sqlalchemy import text


async def test_session_connects_and_extensions_are_available(session):
    result = await session.execute(text("SELECT extname FROM pg_extension"))
    extensions = {row[0] for row in result}
    assert "timescaledb" in extensions
    assert "vector" in extensions
