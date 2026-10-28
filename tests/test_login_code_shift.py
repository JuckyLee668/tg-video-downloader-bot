"""Login codes are typed with every digit +1 so the real code never appears
verbatim in the Telegram chat (Telethon: such a code expires immediately)."""

from telegram.client import decode_shifted_code


def test_decode_shifted_code_roundtrip():
    assert decode_shifted_code("23456") == "12345"
    assert decode_shifted_code("11111") == "00000"
    assert decode_shifted_code("01234") == "90123"   # 9 wraps to 0 when shifting
    assert decode_shifted_code("12345") == "01234"


def test_decode_shifted_code_ignores_spaces():
    assert decode_shifted_code(" 2 3 4 5 6 ") == "12345"


def test_decode_shifted_code_rejects_non_digits():
    assert decode_shifted_code("") is None
    assert decode_shifted_code(None) is None
    assert decode_shifted_code("12a45") is None
    assert decode_shifted_code("+447561450377") is None
