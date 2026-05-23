"""Tests for telegram/state_manager.py — simple FSM state management."""


from telegram.state_manager import state_manager


class TestStateManager:
    async def test_initial_state_is_none(self):
        state = await state_manager.get(123)
        assert state is None

    async def test_set_and_get_state(self):
        await state_manager.update(123, step="phone")
        state = await state_manager.get(123)
        assert state == {"step": "phone"}

    async def test_update_merges_fields(self):
        await state_manager.update(123, step="code", phone="+8613800138000")
        state = await state_manager.get(123)
        assert state["step"] == "code"
        assert state["phone"] == "+8613800138000"

    async def test_different_chats_are_isolated(self):
        await state_manager.update(1, step="a")
        await state_manager.update(2, step="b")
        assert (await state_manager.get(1))["step"] == "a"
        assert (await state_manager.get(2))["step"] == "b"

    async def test_clear_removes_state(self):
        await state_manager.update(456, step="done")
        await state_manager.clear(456)
        assert await state_manager.get(456) is None

    async def test_clear_missing_does_not_raise(self):
        await state_manager.clear(999999)
        # Should not raise

    async def test_update_overwrites_existing(self):
        await state_manager.update(789, step="first", data="old")
        await state_manager.update(789, step="second")
        state = await state_manager.get(789)
        assert state["step"] == "second"
        # state_manager.update merges, so old keys persist
        assert state.get("data") == "old"
        await state_manager.clear(789)
