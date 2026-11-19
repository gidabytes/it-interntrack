"""Authentication, authorisation and data-protection behaviour.

These are the controls that make the hour ledger trustworthy and the intern PII
defensible in a PCI-DSS environment, so they are tested as behaviour rather
than assumed from configuration.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core import crypto
from app.core.enums import ApprovalStatus, RoleName
from app.core.permissions import PERMISSIONS, ROLE_PERMISSIONS, validate_catalogue
from app.core.security import (
    AuthError, create_access_token, decode_access_token, hash_password,
    verify_password,
)
import app.models as m

pytestmark = pytest.mark.security

MON = date(2026, 11, 16)


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def test_password_hashing_is_salted_and_verifiable():
    h1 = hash_password("CorrectHorse12")
    h2 = hash_password("CorrectHorse12")
    assert h1 != h2, "each hash uses a fresh salt"
    assert verify_password("CorrectHorse12", h1)
    assert verify_password("CorrectHorse12", h2)
    assert not verify_password("correcthorse12", h1), "case sensitive"
    assert not verify_password("", h1)


def test_password_hash_is_not_reversible():
    h = hash_password("CorrectHorse12")
    assert "CorrectHorse12" not in h
    assert h.startswith("$2b$"), "bcrypt format"


def test_overlong_password_is_rejected_rather_than_truncated():
    """bcrypt silently ignores bytes past 72.

    Accepting a 200-character passphrase and only using the first 72 would give
    the user a false sense of strength, so it is refused outright.
    """
    with pytest.raises(AuthError) as exc:
        hash_password("x" * 100)
    assert exc.value.code == "PASSWORD_TOO_LONG"


def test_malformed_hash_fails_closed():
    assert verify_password("anything", "not-a-real-hash") is False
    assert verify_password("anything", "") is False


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def test_token_roundtrip_carries_identity_and_roles():
    token = create_access_token("gids", user_id=42, roles=["IT_MANAGER"])
    payload = decode_access_token(token)
    assert payload["sub"] == "gids"
    assert payload["uid"] == 42
    assert payload["roles"] == ["IT_MANAGER"]
    assert payload["exp"] > payload["iat"]


def test_tampered_token_is_rejected():
    token = create_access_token("gids", user_id=1, roles=["INTERN"])
    head, body, sig = token.split(".")
    # Re-sign is impossible without the secret; flipping the signature must fail.
    forged = f"{head}.{body}.{sig[:-4]}AAAA"
    with pytest.raises(AuthError):
        decode_access_token(forged)


def test_expired_token_is_rejected():
    token = create_access_token(
        "gids", user_id=1, roles=["INTERN"], expires_minutes=-5
    )
    with pytest.raises(AuthError):
        decode_access_token(token)


def test_token_signed_with_another_secret_is_rejected():
    from jose import jwt

    forged = jwt.encode(
        {"sub": "attacker", "uid": 1, "roles": ["SYSTEM_ADMINISTRATOR"]},
        "a-different-secret", algorithm="HS256",
    )
    with pytest.raises(AuthError):
        decode_access_token(forged)


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

def test_permission_catalogue_is_internally_consistent():
    validate_catalogue()
    for role, perms in ROLE_PERMISSIONS.items():
        assert perms, f"{role} has no permissions"
        assert perms <= set(PERMISSIONS), f"{role} references unknown permissions"


def test_intern_role_cannot_approve_or_manage():
    """The intern role must not hold any approval or management permission."""
    intern_perms = ROLE_PERMISSIONS[RoleName.INTERN]
    forbidden = {
        "attendance.approve", "attendance.manage", "overtime.approve",
        "leave.approve", "timesheet.sign", "ojt.adjust", "schedule.assign",
        "schedule.approve", "user.manage", "role.manage", "settings.manage",
        "intern.manage", "exposure.verify",
    }
    overlap = intern_perms & forbidden
    assert not overlap, f"intern role must not hold: {sorted(overlap)}"


def test_mentor_cannot_approve_attendance_or_sign_timesheets():
    mentor = ROLE_PERMISSIONS[RoleName.IT_MENTOR]
    assert "attendance.approve" not in mentor
    assert "timesheet.sign" not in mentor
    assert "ojt.adjust" not in mentor


def test_administrator_holds_every_permission():
    assert ROLE_PERMISSIONS[RoleName.SYSTEM_ADMINISTRATOR] == set(PERMISSIONS)


def test_user_permission_resolution(db, org):
    sup = org["supervisor_user"]
    assert sup.has_role(RoleName.IT_SUPERVISOR)
    assert not sup.has_role(RoleName.SYSTEM_ADMINISTRATOR)
    assert sup.has_permission("attendance.approve")
    assert not sup.has_permission("user.manage")

    admin = org["admin_user"]
    assert admin.has_permission("user.manage")
    assert admin.has_permission("settings.manage")


# ---------------------------------------------------------------------------
# Self-approval prevention
# ---------------------------------------------------------------------------

def test_intern_cannot_sign_their_own_timesheet(
    db, rules, shifts, make_intern, punch, org
):
    from app.services import timesheet_service
    from app.services.timesheet_service import TimesheetError

    intern = make_intern(shift=shifts["SHIFT-02"], with_user=True)
    ts = timesheet_service.generate(
        db, intern, anchor=MON, actor_user_id=org["supervisor_user"].id, rules=rules
    )
    own_user = db.get(m.User, intern.user_id)

    with pytest.raises(TimesheetError) as exc:
        timesheet_service.sign(db, ts, signer=own_user, require_all_approved=False)
    assert exc.value.code == "SELF_SIGN_FORBIDDEN"


def test_intern_role_cannot_sign_any_timesheet(
    db, rules, shifts, make_intern, org
):
    """Even somebody else's: the role itself is not permitted to sign."""
    from app.services import timesheet_service
    from app.services.timesheet_service import TimesheetError

    intern_a = make_intern(first_name="Ana", shift=shifts["SHIFT-02"], with_user=True)
    intern_b = make_intern(first_name="Ben", shift=shifts["SHIFT-02"], with_user=True)
    ts = timesheet_service.generate(
        db, intern_b, anchor=MON, actor_user_id=org["supervisor_user"].id, rules=rules
    )
    user_a = db.get(m.User, intern_a.user_id)

    with pytest.raises(TimesheetError) as exc:
        timesheet_service.sign(db, ts, signer=user_a, require_all_approved=False)
    assert exc.value.code == "NOT_AUTHORISED_TO_SIGN"


def test_intern_cannot_verify_their_own_exposure_log(
    db, rules, make_intern, org
):
    from app.services import exposure_service
    from app.services.exposure_service import ExposureError

    intern = make_intern(with_user=True)
    entry = exposure_service.log_activity(
        db, intern, activity_date=MON, exposure_area="NETWORKING",
        activity_title="VLAN change", minutes_spent=120,
        logged_by_id=org["mentor_user"].id,
    )
    with pytest.raises(ExposureError) as exc:
        exposure_service.verify_activity(db, entry, verifier_id=intern.user_id)
    assert exc.value.code == "SELF_VERIFY_FORBIDDEN"


def test_signing_refuses_a_period_with_unapproved_records(
    db, rules, shifts, make_intern, punch, org
):
    """A signature must not certify figures that are still in dispute."""
    from app.services import attendance_service, timesheet_service
    from app.services.timesheet_service import TimesheetError

    intern = make_intern(shift=shifts["SHIFT-02"])
    attendance_service.manual_entry(
        db, intern, MON, time_in_at=punch(MON, 8, 0),
        time_out_at=punch(MON, 17, 0), actor_user_id=org["supervisor_user"].id,
        rules=rules,
    )
    ts = timesheet_service.generate(
        db, intern, anchor=MON, actor_user_id=org["supervisor_user"].id, rules=rules
    )
    with pytest.raises(TimesheetError) as exc:
        timesheet_service.sign(db, ts, signer=org["supervisor_user"])
    assert exc.value.code == "UNAPPROVED_RECORDS"


# ---------------------------------------------------------------------------
# Encryption at rest
# ---------------------------------------------------------------------------

def test_encryption_is_active_in_tests():
    assert crypto.encryption_enabled(), (
        "tests must exercise the encrypted path, not a plaintext shortcut"
    )


def test_pii_is_ciphertext_on_disk(tmp_path, org):
    """Write an intern to a real file-backed database and grep the bytes."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from app.core.database import Base

    db_path = tmp_path / "enc_check.db"
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _fk(c, _r):
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    secret_email = "very.secret.person@school.example"
    secret_phone = "0917-123-4567"
    secret_emergency = "0918-999-8888"

    with Session() as s:
        intern = m.Intern(
            intern_code="OJT-2026-9999", first_name="Secret", last_name="Person",
            email=secret_email, contact_number=secret_phone,
            emergency_contact_number=secret_emergency,
            emergency_contact_name="Secret Guardian",
            ojt_start_date=MON, required_hours=486,
        )
        intern.sync_lookup_indexes()
        s.add(intern)
        s.commit()

    raw = db_path.read_bytes()
    for secret in (secret_email, secret_phone, secret_emergency, b"Secret Guardian".decode()):
        assert secret.encode() not in raw, f"{secret!r} found in plaintext on disk"

    # Ciphertext is present and is a Fernet token.
    con = sqlite3.connect(db_path)
    stored_email, stored_phone = con.execute(
        "select email, contact_number from interns"
    ).fetchone()
    con.close()
    assert stored_email.startswith(crypto.FERNET_TOKEN_PREFIX)
    assert stored_phone.startswith(crypto.FERNET_TOKEN_PREFIX)

    # And the application still reads it back correctly.
    with Session() as s:
        back = s.scalar(select(m.Intern))
        assert back.email == secret_email
        assert back.contact_number == secret_phone

    engine.dispose()


def test_blind_index_enables_lookup_without_plaintext(db, org, make_intern):
    """Encrypted columns are unsearchable; the keyed index is how lookup works."""
    from app.services import intern_service

    intern = make_intern(first_name="Lookup", last_name="Target")
    target_email = intern.email
    db.commit()

    found = intern_service.find_by_email(db, target_email)
    assert found is not None
    assert found.id == intern.id

    # Case and surrounding whitespace are normalised.
    assert intern_service.find_by_email(db, f"  {target_email.upper()}  ").id == intern.id
    assert intern_service.find_by_email(db, "nobody@nowhere.test") is None


def test_blind_index_is_keyed_not_a_bare_hash():
    """A bare SHA-256 would let an attacker confirm a guessed email offline."""
    import hashlib

    value = "known.person@example.com"
    bare = hashlib.sha256(value.encode()).hexdigest()[:32]
    keyed = crypto.blind_index(value)
    assert keyed != bare, "blind index must be HMAC-keyed, not an unkeyed digest"


def test_key_rotation_reads_old_ciphertext(monkeypatch):
    """MultiFernet must decrypt data written under a retired key."""
    from cryptography.fernet import Fernet

    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    monkeypatch.setenv("FERNET_KEYS", old_key)
    crypto.reset_cipher_cache()
    token = crypto.encrypt_text("sensitive-value")

    # Rotate: new key first, old key retained.
    monkeypatch.setenv("FERNET_KEYS", f"{new_key},{old_key}")
    crypto.reset_cipher_cache()
    assert crypto.decrypt_text(token) == "sensitive-value", (
        "old ciphertext must stay readable during rotation"
    )

    # Re-encrypting now uses the new primary key.
    new_token = crypto.encrypt_text("sensitive-value")
    assert new_token != token

    # Once the old key is dropped, the OLD token is no longer readable - which
    # is exactly why rotate-encryption must run before removing a key.
    monkeypatch.setenv("FERNET_KEYS", new_key)
    crypto.reset_cipher_cache()
    assert crypto.decrypt_text(new_token) == "sensitive-value"
    with pytest.raises(crypto.EncryptionNotConfigured):
        crypto.decrypt_text(token)


def test_tampered_ciphertext_raises_rather_than_returning_garbage(monkeypatch):
    """Fernet is authenticated: a modified token must fail, not decode wrongly."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("FERNET_KEYS", Fernet.generate_key().decode())
    crypto.reset_cipher_cache()
    token = crypto.encrypt_text("balance:1000")
    tampered = token[:-6] + ("AAAAAA" if not token.endswith("AAAAAA") else "BBBBBB")
    with pytest.raises(crypto.EncryptionNotConfigured):
        crypto.decrypt_text(tampered)


def test_audit_log_never_stores_decrypted_pii(db, org, make_intern):
    """The audit trail records THAT a field changed, not its plaintext value."""
    from app.services import audit_service

    intern = make_intern(first_name="Audited", last_name="Person")
    secret = intern.email

    entry = audit_service.record(
        db, action="UPDATE", entity_type="intern", entity_id=intern.id,
        actor=org["supervisor_user"], summary="Updated contact details",
        after=intern,
        fields=["intern_code", "email", "contact_number", "status"],
    )
    db.flush()

    assert entry.after_state["email"] == "[ENCRYPTED]"
    assert entry.after_state["contact_number"] == "[ENCRYPTED]"
    assert entry.after_state["intern_code"] == intern.intern_code
    assert secret not in str(entry.after_state)


def test_audit_log_redacts_password_hashes(db, org):
    from app.services import audit_service

    entry = audit_service.record(
        db, action="UPDATE", entity_type="user",
        entity_id=org["supervisor_user"].id, actor=org["admin_user"],
        summary="Changed password", after=org["supervisor_user"],
        fields=["username", "password_hash"],
    )
    db.flush()
    assert entry.after_state["password_hash"] == "[REDACTED]"
    assert org["supervisor_user"].password_hash not in str(entry.after_state)


def test_audit_log_is_append_only_in_practice(db, org, make_intern):
    """Entries accumulate; nothing in the service layer updates or deletes them."""
    from app.services import audit_service

    intern = make_intern()
    for i in range(3):
        audit_service.record(
            db, action="UPDATE", entity_type="intern", entity_id=intern.id,
            actor=org["supervisor_user"], summary=f"change {i}",
        )
    db.flush()
    trail = audit_service.trail_for(db, "intern", intern.id)
    assert len(trail) == 3
    assert [t.summary for t in trail] == ["change 2", "change 1", "change 0"], (
        "newest first"
    )


# ---------------------------------------------------------------------------
# Record locking
# ---------------------------------------------------------------------------

def test_signed_timesheet_locks_attendance_records(
    db, rules, shifts, make_intern, punch, org, approve
):
    from app.services import attendance_service, timesheet_service
    from app.services.attendance_service import AttendanceError

    intern = make_intern(shift=shifts["SHIFT-02"])
    rec = attendance_service.manual_entry(
        db, intern, MON, time_in_at=punch(MON, 8, 0),
        time_out_at=punch(MON, 17, 0),
        actor_user_id=org["supervisor_user"].id, rules=rules,
    )
    approve(rec, rules)

    ts = timesheet_service.generate(
        db, intern, anchor=MON, actor_user_id=org["supervisor_user"].id, rules=rules
    )
    timesheet_service.sign(db, ts, signer=org["supervisor_user"])
    db.flush()

    assert rec.is_locked is True
    with pytest.raises(AttendanceError) as exc:
        attendance_service.manual_entry(
            db, intern, MON, time_in_at=punch(MON, 9, 0),
            time_out_at=punch(MON, 18, 0),
            actor_user_id=org["supervisor_user"].id, rules=rules,
            session_sequence=1,
        )
    assert exc.value.code == "RECORD_LOCKED"


def test_signature_detects_tampering(
    db, rules, shifts, make_intern, punch, org, approve
):
    from app.services import attendance_service, timesheet_service

    intern = make_intern(shift=shifts["SHIFT-02"])
    rec = attendance_service.manual_entry(
        db, intern, MON, time_in_at=punch(MON, 8, 0),
        time_out_at=punch(MON, 17, 0),
        actor_user_id=org["supervisor_user"].id, rules=rules,
    )
    approve(rec, rules)
    ts = timesheet_service.generate(
        db, intern, anchor=MON, actor_user_id=org["supervisor_user"].id, rules=rules
    )
    timesheet_service.sign(db, ts, signer=org["supervisor_user"])
    db.flush()

    assert timesheet_service.verify_signature(ts)["valid"] is True

    ts.total_creditable_minutes += 480   # someone adds eight hours
    db.flush()
    result = timesheet_service.verify_signature(ts)
    assert result["valid"] is False
    assert "MISMATCH" in result["message"]
