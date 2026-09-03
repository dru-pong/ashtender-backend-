"""
Enumerations from build spec section 5.5.

Verdict is deliberately NOT a boolean.  UNKNOWN is the most important state in
the system: it is what routes a tender to a human instead of silently binning
it.  Any code that collapses Verdict to a truthy check is a bug.
"""
from django.db import models


class ValidityMode(models.TextChoices):
    PRINTED_EXPIRY = "PRINTED_EXPIRY", "Printed expiry date"
    ISSUE_RECENCY = "ISSUE_RECENCY", "Issued within N months"
    PERPETUAL = "PERPETUAL", "Perpetual"
    ANNUAL_RENEWAL = "ANNUAL_RENEWAL", "Annual renewal"


class Verdict(models.TextChoices):
    PASS = "PASS", "Pass"
    FAIL = "FAIL", "Fail"
    UNKNOWN = "UNKNOWN", "Unknown - human review"
    WAIVABLE = "WAIVABLE", "Waivable"


#: Verdicts that must stop a bid reaching READY_TO_SUBMIT.
BLOCKING_VERDICTS = frozenset({Verdict.FAIL, Verdict.UNKNOWN})


class RequirementTier(models.TextChoices):
    UNIVERSAL = "UNIVERSAL", "Universal"
    DOMAIN = "DOMAIN", "Domain"
    TENDER_EXPLICIT = "TENDER_EXPLICIT", "Tender explicit"


#: Higher wins.  Spec section 6: EXPLICIT overrides DOMAIN overrides UNIVERSAL.
TIER_PRECEDENCE = {
    RequirementTier.UNIVERSAL: 0,
    RequirementTier.DOMAIN: 1,
    RequirementTier.TENDER_EXPLICIT: 2,
}


class RequirementOrigin(models.TextChoices):
    EXPLICIT = "EXPLICIT", "Explicit in tender text"
    INFERRED = "INFERRED", "Inferred from domain"


class ObjectType(models.TextChoices):
    WORKS = "WORKS", "Works"
    GOODS = "GOODS", "Goods"
    CONSULTANCY = "CONSULTANCY", "Consultancy"
    NON_CONSULTANCY = "NON_CONSULTANCY", "Non-consultancy services"
    DISPOSAL = "DISPOSAL", "Disposal"


class Method(models.TextChoices):
    RFQ = "RFQ", "Request for quotations"
    NCT = "NCT", "National competitive tendering"
    ICT = "ICT", "International competitive tendering"
    RESTRICTED = "RESTRICTED", "Restricted tendering"
    SINGLE_SOURCE = "SINGLE_SOURCE", "Single source"
    FRAMEWORK = "FRAMEWORK", "Framework agreement"
    TWO_STAGE = "TWO_STAGE", "Two-stage tendering"


class ContractForm(models.TextChoices):
    ADMEASUREMENT = "ADMEASUREMENT", "Admeasurement"
    LUMP_SUM = "LUMP_SUM", "Lump sum"
    DESIGN_BUILD = "DESIGN_BUILD", "Design and build"
    LABOUR_ONLY = "LABOUR_ONLY", "Labour only"
    TERM_MAINT = "TERM_MAINT", "Term maintenance"
    FRAMEWORK_CALLOFF = "FRAMEWORK_CALLOFF", "Framework call-off"


class FundingSource(models.TextChoices):
    GOG = "GOG", "Government of Ghana"
    DACF = "DACF", "District Assemblies Common Fund"
    GETFUND = "GETFUND", "GETFund"
    ROAD_FUND = "ROAD_FUND", "Road Fund"
    IGF = "IGF", "Internally generated funds"
    IFI = "IFI", "International financial institution"
    UN = "UN", "United Nations"
    INGO = "INGO", "International NGO"
    PRIVATE = "PRIVATE", "Private"


class SubmissionMode(models.TextChoices):
    GHANEPS = "GHANEPS", "GHANEPS"
    SEALED_PHYSICAL = "SEALED_PHYSICAL", "Sealed physical"
    EMAIL = "EMAIL", "Email"
    PORTAL_OTHER = "PORTAL_OTHER", "Other portal"


class BidState(models.TextChoices):
    DISCOVERED = "DISCOVERED", "Discovered"
    SCREENED_OUT = "SCREENED_OUT", "Screened out"
    ELIGIBLE = "ELIGIBLE", "Eligible"
    AWAITING_GO_NOGO = "AWAITING_GO_NOGO", "Awaiting go/no-go"
    DOCS_PURCHASED = "DOCS_PURCHASED", "Documents purchased"
    ASSEMBLING = "ASSEMBLING", "Assembling"
    AWAITING_PRICING = "AWAITING_PRICING", "Awaiting pricing"
    READY_TO_SUBMIT = "READY_TO_SUBMIT", "Ready to submit"
    SUBMITTED = "SUBMITTED", "Submitted"
    OPENED = "OPENED", "Opened"
    WON = "WON", "Won"
    LOST = "LOST", "Lost"
    MISSED = "MISSED", "Missed"


class IPCStatus(models.TextChoices):
    PREPARED = "PREPARED", "Prepared"
    SUBMITTED = "SUBMITTED", "Submitted"
    CERTIFIED = "CERTIFIED", "Certified"
    VALIDATED = "VALIDATED", "Validated"
    REJECTED = "REJECTED", "Rejected"
    PART_PAID = "PART_PAID", "Part paid"
    PAID = "PAID", "Paid"
    EXPUNGED = "EXPUNGED", "Expunged"


class HarvestStrategy(models.TextChoices):
    FEED = "FEED", "Feed (RSS/JSON/API)"
    LISTING = "LISTING", "HTML listing + selectors"
    MAILBOX = "MAILBOX", "Mailbox (IMAP notification parse)"
    NEWSPRINT = "NEWSPRINT", "Newsprint (e-paper, VLM)"
    AGENT = "AGENT", "Agent (login-walled)"
    MANUAL = "MANUAL", "Manual entry"


class Channel(models.TextChoices):
    """
    Client type, NOT work domain.  Aggregators conflate 'NGO' with a category
    of work; it is a category of buyer.  Spec section 5.5.
    """

    GOV_GHANEPS = "GOV_GHANEPS", "Government via GHANEPS"
    GOV_ENTITY = "GOV_ENTITY", "Government entity direct"
    UN = "UN", "United Nations"
    INGO = "INGO", "International NGO"
    DONOR_IFI = "DONOR_IFI", "Donor / IFI"
    PRIVATE = "PRIVATE", "Private sector"


class EntityCategory(models.TextChoices):
    """Approving-authority band, L.I. 2516 2nd Schedule."""

    A = "A", "Category A"
    B = "B", "Category B"
    C = "C", "Category C"
    D = "D", "Category D"
    E = "E", "Category E"


class SourceTier(models.TextChoices):
    """Provenance ranking for enriched entity data.  Spec section 9."""

    NOTICE_TEXT = "NOTICE_TEXT", "Notice text (highest)"
    OCDS = "OCDS", "OCDS award record"
    ENTITY_SITE = "ENTITY_SITE", "Entity website"
    DIRECTORY = "DIRECTORY", "Directory / third party"
    HUMAN = "HUMAN", "Human confirmed"


class LLMTask(models.TextChoices):
    DOC_CLASSIFY = "DOC_CLASSIFY", "Classify document"
    DOC_EXTRACT = "DOC_EXTRACT", "Extract fields from document"
    REQ_EXTRACT = "REQ_EXTRACT", "Extract requirements from tender"
    ENTITY_ENRICH = "ENTITY_ENRICH", "Enrich procuring entity"
    NARRATIVE = "NARRATIVE", "Draft narrative"
    TITLE_PARSE = "TITLE_PARSE", "Parse tender title"
    DISCIPLINE_TAG = "DISCIPLINE_TAG", "Tag disciplines"


class VerificationMethod(models.TextChoices):
    REGISTER = "REGISTER", "Public register lookup (oracle)"
    DOCUMENT = "DOCUMENT", "Uploaded document"
    DECLARATION = "DECLARATION", "Self declaration"
    HUMAN = "HUMAN", "Human verification"


class ClaimOutcome(models.TextChoices):
    PENDING = "PENDING", "Pending"
    VALIDATED = "VALIDATED", "Validated"
    REDUCED = "REDUCED", "Reduced"
    REJECTED = "REJECTED", "Rejected"
    UNKNOWN = "UNKNOWN", "Unknown"


class InteractionKind(models.TextChoices):
    CALL = "CALL", "Phone call"
    VISIT = "VISIT", "Visit"
    EMAIL = "EMAIL", "Email"
    CLARIFICATION = "CLARIFICATION", "Formal clarification request"
    PREBID_MEETING = "PREBID_MEETING", "Pre-bid meeting"
    SITE_VISIT = "SITE_VISIT", "Site visit"
