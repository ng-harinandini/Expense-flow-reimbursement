"""category custom fields: 15-category reseed + common-fields row

Two independent changes bundled because the second is meaningless without the first:

  1. Adds ``expense_categories.custom_fields`` (JSONB list of extraction field definitions —
     ``{name, label, description, data_type, options, required}``) and
     ``expense_categories.is_common`` (true only for a single non-selectable "COMMON" row holding
     the fields every invoice carries regardless of category — employee_id, total_amount,
     invoice_number, …). See :mod:`app.models.category` for the rationale.

  2. Replaces the five categories seeded by ``0008`` (Meals, Ground Transport, Flights, Lodging,
     Client Entertainment) with the fifteen from the invoice-classification reference doc, each
     carrying its own ``custom_fields``. Done as **updates**, not delete+insert, for the five
     overlapping concepts, because ``expense_items.category_id`` has ``ondelete=RESTRICT`` — any
     historical item referencing one of these rows would block a delete outright, and even where it
     wouldn't, reusing the row/id preserves the FK link instead of orphaning it to NULL. The ten net
     -new categories are inserted fresh.

     Renames performed on the five existing rows:
       MEALS                unchanged           -> "Meals"
       GROUND_TRANSPORT  -> TAXI_CAB             -> "Taxi / Cab / Ride-hailing"
       FLIGHTS           -> AIR_TRAVEL           -> "Air Travel"
       LODGING           -> HOTEL_LODGING        -> "Hotel / Lodging"
       CLIENT_ENTERTAINMENT -> CLIENT_ENTERTAINMENT -> "Client / Business Entertainment"

  3. Updates the five ``policy_rules`` rows seeded by ``0003`` to the same new ``category`` string
     (data-only ``UPDATE``, matching the ``0011`` convention of never editing an already-applied
     seed migration in place). The hardcoded branches in ``app.services.policy_engine`` /
     ``app.services.fraud_engine`` are updated in lockstep by this same change (application code,
     not this migration) — see ``tests/test_migrations.py::test_expense_categories_match_policy_rule_categories``
     for the invariant this must preserve.

Revision ID: 0012_category_custom_fields
Revises: 0011_retire_disburse_action
Create Date: 2026-08-09
"""
import json
import uuid
from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0012_category_custom_fields"
down_revision: Union[str, None] = "0011_retire_disburse_action"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SEED_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def _seed_id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"expenseflow:{kind}:{key}")


def _field(
    name: str,
    label: str,
    description: str,
    data_type: str,
    *,
    options: Optional[list] = None,
    required: bool = False,
) -> dict:
    field = {
        "name": name,
        "label": label,
        "description": description,
        "data_type": data_type,
        "required": required,
    }
    if options is not None:
        field["options"] = options
    return field


COMMON_FIELDS: tuple[dict, ...] = (
    _field("employee_id", "Employee ID", "Unique employee identifier tied to the claim", "text", required=True),
    _field("employee_name", "Employee Name", "Name of the person submitting the claim", "text", required=True),
    _field("expense_category", "Expense Category", "Classified category name, drives which policy/approval rule applies", "text", required=True),
    _field("cost_center", "Cost Center", "Budget/department code for finance allocation", "text"),
    _field("project_code", "Project Code", "Optional project reference, if expense is billed to a client/project", "text"),
    _field("total_amount", "Total Amount", "Reimbursable amount extracted from invoice", "number", required=True),
    _field("currency", "Currency", "Currency of the invoice (INR, USD, EUR, etc.)", "text", required=True),
    _field("invoice_date", "Invoice Date", "Date on invoice, checked against claim window/trip dates", "date", required=True),
    _field("vendor_name", "Vendor Name", "Biller name, used for duplicate and preferred-vendor checks", "text", required=True),
    _field("invoice_number", "Invoice Number", "Unique invoice identifier for duplicate detection", "text", required=True),
    _field("receipt_image_link", "Receipt Image Link", "Link/path to scanned invoice image for manual visual verification", "text"),
    _field("policy_limit_flag", "Policy Limit Flag", "True/False - flags if amount exceeds category policy cap", "boolean"),
    _field("duplicate_check_flag", "Duplicate Check Flag", "True/False - flags if matching invoice already submitted", "boolean"),
    _field(
        "approval_tier", "Approval Tier",
        "Auto-approve / Manager approval / Manager + Finance approval, based on category + amount",
        "enum", options=["Auto-approve", "Manager approval", "Manager + Finance approval"],
    ),
    _field("confidence_score", "Confidence Score", "OCR/extraction confidence score, flags low-confidence extractions for manual review", "number"),
)

CLASS_OF_TRAVEL_OPTIONS = ["Economy", "Premium Economy", "Business", "First"]
TRAIN_CLASS_OPTIONS = ["AC1", "AC2", "AC3", "Sleeper", "Chair Car"]
MEAL_TYPE_OPTIONS = ["Breakfast", "Lunch", "Dinner", "Snacks"]
APPROVAL_TIER_OPTIONS = ["Auto-approve", "Manager approval", "Manager + Finance approval"]

# (code, name, display_order, description, custom_fields)
CATEGORIES: tuple[tuple[str, str, int, str, tuple], ...] = (
    (
        "AIR_TRAVEL", "Air Travel", 10, "Domestic/international flight tickets.",
        (
            _field("travel_route", "Travel Route", "Origin-destination, e.g. 'HYD-DEL', matched against approved travel request", "text", required=True),
            _field("travel_date", "Travel Date", "Date of journey", "date", required=True),
            _field("passenger_name", "Passenger Name", "Confirms ticket belongs to employee, not a third party", "text", required=True),
            _field("class_of_travel", "Class of Travel", "Economy / Premium Economy / Business / First - policy caps often restrict to Economy", "enum", options=CLASS_OF_TRAVEL_OPTIONS),
            _field("ticket_amount", "Ticket Amount", "Base fare amount", "number", required=True),
            _field("airline_name", "Airline Name", "Vendor name, checked against preferred airline list", "text"),
        ),
    ),
    (
        "TRAIN_TRAVEL", "Train Travel", 20, "Rail tickets.",
        (
            _field("travel_route", "Travel Route", "Origin-destination station", "text", required=True),
            _field("travel_date", "Travel Date", "Date of journey", "date", required=True),
            _field("class_of_travel", "Class of Travel", "AC1 / AC2 / AC3 / Sleeper / Chair Car - policy caps by employee grade", "enum", options=TRAIN_CLASS_OPTIONS),
            _field("ticket_amount", "Ticket Amount", "Reimbursable fare amount", "number", required=True),
        ),
    ),
    (
        "TAXI_CAB", "Taxi / Cab / Ride-hailing", 30, "Local taxi, cab, and ride-hailing service invoices.",
        (
            _field("service_days", "Service Days", "Number of days taxi service used, cross-checked against trip duration", "number"),
            _field("pickup_drop_locations", "Pickup / Drop Locations", "Route detail if available, validates business purpose", "text"),
            _field("daily_amount", "Daily Amount", "Per-day charge, if itemized", "number"),
            _field("total_amount", "Total Amount", "Total reimbursable value", "number", required=True),
            _field("vendor_name", "Vendor Name", "Cab service provider, checked against preferred vendor/rate card", "text"),
        ),
    ),
    (
        "RENTAL_CAR", "Rental Car", 40, "Self-drive or chauffeur-driven rental car invoices.",
        (
            _field("rental_start_date", "Rental Start Date", "Rental pickup date", "date", required=True),
            _field("rental_end_date", "Rental End Date", "Rental return date, used to validate duration", "date", required=True),
            _field("vehicle_category", "Vehicle Category", "Policy may cap reimbursement to Economy/Compact category", "text"),
            _field("mileage_driven", "Mileage Driven", "Distance driven, if listed, cross-checked with fuel claims", "number"),
            _field("total_amount", "Total Amount", "Reimbursable rental cost", "number", required=True),
        ),
    ),
    (
        "FUEL_MILEAGE", "Fuel / Mileage", 50, "Fuel purchases and personal-vehicle mileage claims.",
        (
            _field("vehicle_registration_number", "Vehicle Registration Number", "Confirms company-authorized or registered personal vehicle", "text"),
            _field("mileage", "Mileage", "Distance in KM/miles, basis for mileage-rate reimbursement", "number"),
            _field("fuel_quantity", "Fuel Quantity", "Liters/gallons purchased, used for reasonableness check", "number"),
            _field("fuel_amount", "Fuel Amount", "Reimbursable fuel cost", "number", required=True),
            _field("trip_purpose", "Trip Purpose", "Business justification for the trip", "text"),
        ),
    ),
    (
        "HOTEL_LODGING", "Hotel / Lodging", 60, "Hotel and lodging stays.",
        (
            _field("check_in_date", "Check-in Date", "Confirms stay start aligns with trip", "date", required=True),
            _field("check_out_date", "Check-out Date", "Confirms stay end aligns with trip", "date", required=True),
            _field("number_of_nights", "Number of Nights", "Basis for per-night policy cap", "number"),
            _field("room_rate", "Room Rate", "Nightly rate, compared against per-night limit", "number", required=True),
            _field("meals_included_flag", "Meals Included", "True if breakfast/dinner bundled in room rate, prevents double-claiming meals separately", "boolean"),
            _field("hotel_star_category", "Hotel Star Category", "Star rating if stated, some policies cap by category", "text"),
            _field("total_amount", "Total Amount", "Reimbursable total for the stay", "number", required=True),
        ),
    ),
    (
        "MEALS", "Meals", 70, "Standalone meals not bundled into lodging.",
        (
            _field("meal_type", "Meal Type", "Breakfast / Lunch / Dinner / Snacks - per-meal policy caps often differ", "enum", options=MEAL_TYPE_OPTIONS),
            _field("meal_date", "Meal Date", "Matched against trip/claim dates", "date", required=True),
            _field("number_of_persons", "Number of Persons", "Distinguishes self-meal vs. group/client meal", "number"),
            _field("amount", "Amount", "Compared against per-meal policy limit", "number", required=True),
        ),
    ),
    (
        "CLIENT_ENTERTAINMENT", "Client / Business Entertainment", 80, "Client and business entertainment expenses.",
        (
            _field("attendees", "Attendees", "Names/count of attendees, often mandatory for approval", "text", required=True),
            _field("client_company_name", "Client Company Name", "Business purpose validation", "text"),
            _field("event_purpose", "Event Purpose", "Justification note, may require manual entry but flagged as required", "text", required=True),
            _field("amount", "Amount", "Usually routed to a higher/separate approval tier", "number", required=True),
        ),
    ),
    (
        "PARKING_TOLLS", "Parking & Tolls", 90, "Parking fees and road tolls.",
        (
            _field("expense_date", "Expense Date", "Matched against trip dates", "date", required=True),
            _field("location", "Location", "Business-relevance check, if available", "text"),
            _field("amount", "Amount", "Usually small-value, often auto-approved under threshold", "number", required=True),
        ),
    ),
    (
        "COMMUNICATION", "Communication", 100, "Phone, internet, and roaming charges.",
        (
            _field("billing_period", "Billing Period", "Confirms charge falls within claim window", "text", required=True),
            _field("plan_type", "Plan Type", "Roaming / Postpaid / Data Plan / Broadband - policy may only cover roaming during travel", "enum", options=["Roaming", "Postpaid", "Data Plan", "Broadband"]),
            _field("amount", "Amount", "Reimbursable value, often capped monthly", "number", required=True),
        ),
    ),
    (
        "TRAINING_CERTIFICATION", "Training / Certification / Conference", 110, "Training, certification, and conference fees.",
        (
            _field("course_or_event_name", "Course or Event Name", "Business justification for the expense", "text", required=True),
            _field("provider_name", "Provider Name", "Verifies legitimate training/certification body", "text"),
            _field("training_dates", "Training Dates", "Validation of attendance period", "text"),
            _field("approval_reference", "Approval Reference", "Pre-approval code/PO number, often required before reimbursement", "text"),
            _field("amount", "Amount", "Reimbursable fee", "number", required=True),
        ),
    ),
    (
        "OFFICE_SUPPLIES", "Office Supplies / Equipment", 120, "Office supplies and small equipment purchases.",
        (
            _field("item_description", "Item Description", "Itemized line description, checked to exclude personal items", "text", required=True),
            _field("quantity", "Quantity", "Reasonableness check against role/need", "number"),
            _field("amount", "Amount", "Reimbursable value, usually has a low auto-approval threshold", "number", required=True),
        ),
    ),
    (
        "SOFTWARE_SUBSCRIPTIONS", "Software / Subscriptions", 130, "Software licenses and recurring subscriptions.",
        (
            _field("subscription_name", "Subscription Name", "Business tool/software validation", "text", required=True),
            _field("billing_period", "Billing Period", "Monthly / Annual / One-time - confirms recurring vs one-time charge", "enum", options=["Monthly", "Annual", "One-time"]),
            _field("amount", "Amount", "Often requires IT/finance co-approval", "number", required=True),
        ),
    ),
    (
        "COURIER_POSTAGE", "Courier / Postage", 140, "Courier and postage charges.",
        (
            _field("sender_receiver", "Sender / Receiver", "Business-purpose check, if available", "text"),
            _field("amount", "Amount", "Usually low-value, fast-track approval", "number", required=True),
        ),
    ),
    (
        "MISCELLANEOUS", "Miscellaneous / Others", 150, "Anything that doesn't fit a standard category template.",
        (
            _field("item_description", "Item Description", "Primary review field since no standard template applies", "text", required=True),
            _field("amount", "Amount", "Reimbursable value", "number", required=True),
            _field("manual_justification_required_flag", "Manual Justification Required", "Forces employee to add explanatory note; triggers stricter manager review", "boolean"),
        ),
    ),
)

# Renames applied to the five rows seeded by 0008, keyed by their old ``code`` (preserves id/FKs).
RENAMES: dict[str, tuple[str, str]] = {
    # old_code: (new_code, new_name) -- new_code/new_name must match an entry in CATEGORIES above.
    "MEALS": ("MEALS", "Meals"),
    "GROUND_TRANSPORT": ("TAXI_CAB", "Taxi / Cab / Ride-hailing"),
    "FLIGHTS": ("AIR_TRAVEL", "Air Travel"),
    "LODGING": ("HOTEL_LODGING", "Hotel / Lodging"),
    "CLIENT_ENTERTAINMENT": ("CLIENT_ENTERTAINMENT", "Client / Business Entertainment"),
}

CATEGORIES_BY_CODE = {code: (name, order, description, fields) for code, name, order, description, fields in CATEGORIES}


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column(
        "expense_categories",
        sa.Column("custom_fields", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "expense_categories",
        sa.Column("is_common", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_expense_categories_is_common", "expense_categories", ["is_common"])

    # --- 1. rename the five existing rows in place (preserves id -> keeps FKs intact) ---
    for old_code, (new_code, new_name) in RENAMES.items():
        _new_name, order, description, fields = CATEGORIES_BY_CODE[new_code]
        bind.execute(
            sa.text(
                """
                UPDATE expense_categories
                   SET code = :new_code, name = :new_name, description = :description,
                       display_order = :display_order, custom_fields = CAST(:fields AS jsonb)
                 WHERE code = :old_code
                """
            ),
            {
                "old_code": old_code,
                "new_code": new_code,
                "new_name": new_name,
                "description": description,
                "display_order": order,
                "fields": json.dumps(list(fields)),
            },
        )

    # --- 2. insert the ten net-new categories ---
    new_codes = set(CATEGORIES_BY_CODE) - {new for new, _ in RENAMES.values()}
    categories_table = sa.table(
        "expense_categories",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("display_order", sa.Integer),
        sa.column("custom_fields", postgresql.JSONB),
    )
    for code in new_codes:
        name, order, description, fields = CATEGORIES_BY_CODE[code]
        bind.execute(
            postgresql.insert(categories_table)
            .values(
                id=_seed_id("expense_category", code),
                code=code,
                name=name,
                description=description,
                display_order=order,
                custom_fields=list(fields),
            )
            .on_conflict_do_nothing(index_elements=["code"])
        )

    # --- 3. seed the common-fields row ---
    bind.execute(
        postgresql.insert(
            sa.table(
                "expense_categories",
                sa.column("id", postgresql.UUID(as_uuid=True)),
                sa.column("code", sa.String),
                sa.column("name", sa.String),
                sa.column("description", sa.Text),
                sa.column("display_order", sa.Integer),
                sa.column("is_common", sa.Boolean),
                sa.column("custom_fields", postgresql.JSONB),
            )
        )
        .values(
            id=_seed_id("expense_category", "COMMON"),
            code="COMMON",
            name="Common Fields",
            description="Fields extracted for every invoice regardless of category.",
            display_order=0,
            is_common=True,
            custom_fields=list(COMMON_FIELDS),
        )
        .on_conflict_do_nothing(index_elements=["code"])
    )

    # --- 4. rename policy_rules.category to match, for the five rows seeded by 0003 ---
    for old_code, (_new_code, new_name) in RENAMES.items():
        old_name = {
            "MEALS": "Meals",
            "GROUND_TRANSPORT": "Ground Transport",
            "FLIGHTS": "Flights",
            "LODGING": "Lodging",
            "CLIENT_ENTERTAINMENT": "Client Entertainment",
        }[old_code]
        if old_name == new_name:
            continue
        bind.execute(
            sa.text(
                "UPDATE policy_rules SET category = :new_name WHERE category = :old_name"
            ),
            {"old_name": old_name, "new_name": new_name},
        )
        bind.execute(
            sa.text(
                "UPDATE policy_rules SET conditions = jsonb_set(conditions, '{category}', "
                "to_jsonb(CAST(:new_name AS text))) WHERE conditions->>'category' = :old_name"
            ),
            {"old_name": old_name, "new_name": new_name},
        )


def downgrade() -> None:
    bind = op.get_bind()

    # Reverse the policy_rules.category renames.
    OLD_NAMES = {
        "TAXI_CAB": ("Taxi / Cab / Ride-hailing", "Ground Transport"),
        "AIR_TRAVEL": ("Air Travel", "Flights"),
        "HOTEL_LODGING": ("Hotel / Lodging", "Lodging"),
        "CLIENT_ENTERTAINMENT": ("Client / Business Entertainment", "Client Entertainment"),
    }
    for new_name, old_name in OLD_NAMES.values():
        bind.execute(
            sa.text(
                "UPDATE policy_rules SET category = :old_name WHERE category = :new_name"
            ),
            {"old_name": old_name, "new_name": new_name},
        )
        bind.execute(
            sa.text(
                "UPDATE policy_rules SET conditions = jsonb_set(conditions, '{category}', "
                "to_jsonb(CAST(:old_name AS text))) WHERE conditions->>'category' = :new_name"
            ),
            {"old_name": old_name, "new_name": new_name},
        )

    # Reverse the ten net-new inserts and the common row.
    new_codes = set(CATEGORIES_BY_CODE) - {new for new, _ in RENAMES.values()}
    for code in new_codes:
        bind.execute(sa.text("DELETE FROM expense_categories WHERE code = :code"), {"code": code})
    bind.execute(sa.text("DELETE FROM expense_categories WHERE code = 'COMMON'"))

    # Reverse the five renames back to 0008's original code/name/display_order, dropping fields.
    ORIGINAL: tuple[tuple[str, str, str, int], ...] = (
        ("MEALS", "MEALS", "Meals", 10),
        ("TAXI_CAB", "GROUND_TRANSPORT", "Ground Transport", 20),
        ("AIR_TRAVEL", "FLIGHTS", "Flights", 30),
        ("HOTEL_LODGING", "LODGING", "Lodging", 40),
        ("CLIENT_ENTERTAINMENT", "CLIENT_ENTERTAINMENT", "Client Entertainment", 50),
    )
    for current_code, old_code, old_name, old_order in ORIGINAL:
        bind.execute(
            sa.text(
                """
                UPDATE expense_categories
                   SET code = :old_code, name = :old_name, display_order = :old_order,
                       description = NULL, custom_fields = NULL
                 WHERE code = :current_code
                """
            ),
            {
                "current_code": current_code,
                "old_code": old_code,
                "old_name": old_name,
                "old_order": old_order,
            },
        )

    op.drop_index("ix_expense_categories_is_common", table_name="expense_categories")
    op.drop_column("expense_categories", "is_common")
    op.drop_column("expense_categories", "custom_fields")
