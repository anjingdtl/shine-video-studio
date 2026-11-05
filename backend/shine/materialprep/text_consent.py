"""Document-, role- and provider-bound text consent on the authoritative DB."""

from shine.h3.rulebook import digest
from shine.llm.base import LLMError, text_provider_snapshot
from shine.models import MaterialTextConsent, ProjectDocument, utcnow
from shine.security import is_loopback_url
from shine.services import add_audit


class TextConsentRequired(LLMError):
    code = "REMOTE_AGENT_CONSENT_REQUIRED"


def fingerprint(profile):
    return digest(text_provider_snapshot(profile))


def identity(project_id, document, role, profile):
    return digest({"project": project_id, "document": document.id, "hash": document.sha256,
                   "role": role, "provider": fingerprint(profile)})


def allowed(db, project_id, document, role, profile):
    if profile.kind == "mock" or is_loopback_url(profile.base_url):
        return True
    if document is None or document.project_id != project_id:
        return False
    row = db.get(MaterialTextConsent, identity(project_id, document, role, profile))
    return row is not None and row.granted


def decide(db, project_id, document, role, profile, granted):
    key = identity(project_id, document, role, profile)
    row = db.get(MaterialTextConsent, key)
    if row is None:
        row = MaterialTextConsent(id=key, project_id=project_id, document_id=document.id,
                                  document_sha256=document.sha256, role=role,
                                  provider_fingerprint=fingerprint(profile))
        db.add(row)
    row.granted = granted
    row.updated_at = utcnow()
    add_audit(db, "local-user", "MATERIAL_TEXT_CONSENT_GRANTED" if granted else "MATERIAL_TEXT_CONSENT_REVOKED",
              "material_text_consent", key, {"document_sha256": document.sha256, "role": role,
              "provider_fingerprint": fingerprint(profile), "scope": "necessary_text_only_no_asset_bytes"},
              project_id=project_id)


def guard(project_id, document_id, document_hash, role, expected_profile, gateway):
    """Checked immediately before each call, including schema corrections.

    Revocation prevents future sends; already sent calls cannot be recalled.
    A route/configuration change is refused rather than silently changing provider.
    """
    def verify(db):
        profile = gateway.profile_for_role(db, role)
        document = db.get(ProjectDocument, document_id)
        if (fingerprint(profile) != expected_profile or document is None
                or document.sha256 != document_hash or not allowed(db, project_id, document, role, profile)):
            raise TextConsentRequired("Text consent missing, revoked or stale",
                                      details={"external_outcome": "NOT_SENT"})
    return verify
