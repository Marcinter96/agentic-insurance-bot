from pydantic import BaseModel, Field
from typing import Literal


IntentType = Literal["policy_question", "offer", "claim", "emergency", "unknown"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]
VerificationLevel = Literal["VERIFIED_RETURNING", "VERIFIED_NEW", "ESCALATED", "UNVERIFIED"]


class CustomerIdentifiers(BaseModel):
    phone: str | None = None
    birthdate: str | None = None
    policy_number: str | None = None
    license_plate: str | None = None


class IntentClassification(BaseModel):
    intent: IntentType
    sub_intent: str = ""
    risk_level: RiskLevel
    customer_identifiers: CustomerIdentifiers
    confidence: float = Field(ge=0.0, le=1.0)
    raw_query: str


class VerificationResult(BaseModel):
    customer_id: str | None = None
    verification_level: VerificationLevel
    allowed_actions: list[IntentType] = []
    failure_reason: str | None = None
    customer_data: dict = {}


class PolicyInfo(BaseModel):
    policy_id: str
    type: str
    coverage: str
    expiry: str
    premium: float
    status: str


class ClaimInfo(BaseModel):
    claim_id: str
    policy_id: str
    status: str
    date_filed: str
    description: str
    amount: float | None = None


class InvoiceInfo(BaseModel):
    invoice_id: str
    amount: float
    due_date: str
    status: str
