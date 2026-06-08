from app.security.password import dummy_verify, hash_password, verify_password


def test_hash_verify_roundtrip():
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert verify_password("correct horse battery staple", h) is True
    assert verify_password("wrong password", h) is False


def test_hash_is_salted():
    # Two hashes of the same password must differ (random salt).
    assert hash_password("samepw123") != hash_password("samepw123")


def test_long_password_not_truncated_at_72_bytes():
    # bcrypt truncates at 72 bytes; the SHA-256 pre-hash must make the
    # full password matter so two 72+ byte passwords sharing a prefix
    # do NOT collide.
    base = "A" * 72
    h = hash_password(base + "tail-one")
    assert verify_password(base + "tail-one", h) is True
    assert verify_password(base + "tail-two", h) is False


def test_unicode_password():
    h = hash_password("señha-açaí-🔒")
    assert verify_password("señha-açaí-🔒", h) is True


def test_verify_with_garbage_hash_returns_false():
    assert verify_password("whatever", "not-a-bcrypt-hash") is False


def test_dummy_verify_does_not_raise():
    dummy_verify()
