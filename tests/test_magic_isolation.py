"""Two bots on one MT5 login must not manage each other's positions."""

import os
from unittest.mock import patch

from bot.config import DEFAULT_MAGIC, load_settings


def _settings(**env):
    with patch.dict(os.environ, env, clear=False):
        return load_settings()


def test_magic_defaults_to_the_desk_magic():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MAGIC", None)
        assert load_settings().magic == DEFAULT_MAGIC


def test_magic_is_read_from_env():
    assert _settings(MAGIC="26082602").magic == 26082602


def test_blank_magic_falls_back_to_default():
    assert _settings(MAGIC="").magic == DEFAULT_MAGIC


def test_bot_only_owns_positions_matching_its_magic():
    from bot.broker import Position
    from bot.engine import BotEngine

    mac = BotEngine.__new__(BotEngine)
    mac.settings = _settings(MAGIC="26082602")
    mac._exits_by_ticket = {}

    server = BotEngine.__new__(BotEngine)
    server.settings = _settings(MAGIC=str(DEFAULT_MAGIC))
    server._exits_by_ticket = {}

    server_pos = Position("GBPJPY", "SELL", 0.1, 207.9, -4.36, "t1", DEFAULT_MAGIC)
    mac_pos = Position("EURUSD", "BUY", 0.1, 1.16, 1.0, "t2", 26082602)

    assert server._is_bot_position(server_pos) is True
    assert server._is_bot_position(mac_pos) is False
    assert mac._is_bot_position(mac_pos) is True
    assert mac._is_bot_position(server_pos) is False
