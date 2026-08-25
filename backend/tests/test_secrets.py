from backend.app.services.secrets import mask_secret


def test_secret_mask_never_returns_full_value():
    raw = "sk-example-super-secret-1234"
    masked = mask_secret(raw)
    assert raw not in masked
    assert masked.endswith("1234")
    assert "super-secret" not in masked
