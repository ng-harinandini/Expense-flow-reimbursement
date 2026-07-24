import random
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any

# Initial Employees
INITIAL_EMPLOYEES = [
  {
    "id": "emp-101",
    "name": "Sarah Jenkins",
    "grade": "L3",
    "department": "Engineering",
    "email": "sarah.j@enterprise.com",
    "managerName": "Marcus Vance"
  },
  {
    "id": "emp-102",
    "name": "David Chen",
    "grade": "L5",
    "department": "Product Management",
    "email": "david.c@enterprise.com",
    "managerName": "Elena Rostova"
  },
  {
    "id": "emp-103",
    "name": "Elena Rostova",
    "grade": "Director",
    "department": "Product Management",
    "email": "elena.r@enterprise.com",
    "managerName": "VP Global Products"
  },
  {
    "id": "emp-104",
    "name": "Michael Chang",
    "grade": "L1",
    "department": "Customer Support",
    "email": "michael.c@enterprise.com",
    "managerName": "Sarah Jenkins"
  }
]

# Initial Policy Rules
DEFAULT_POLICY_RULES = [
  {
    "category": "Meals",
    "maxAmountUSD": 40.0,
    "autoApproveLimitUSD": 25.0,
    "receiptRequiredAboveUSD": 25.0,
    "requiresPreApproval": False,
    "gradeTier": "All Staff",
    "specialRules": [
      "Maximum $40.00 per day for meals.",
      "Itemized receipt required above $25.00.",
      "Alcohol is NOT reimbursable under Meals."
    ]
  },
  {
    "category": "Ground Transport",
    "maxAmountUSD": 150.0,
    "autoApproveLimitUSD": 50.0,
    "receiptRequiredAboveUSD": 0.0,
    "requiresPreApproval": False,
    "gradeTier": "All Staff",
    "specialRules": [
      "Taxi / Rideshare max $150 per trip.",
      "Itemized receipt ALWAYS required regardless of amount.",
      "Personal mileage reimbursed at $0.67/mile."
    ]
  },
  {
    "category": "Flights",
    "maxAmountUSD": 10000.0,
    "autoApproveLimitUSD": None,
    "receiptRequiredAboveUSD": 0.0,
    "requiresPreApproval": True,
    "gradeTier": "Grade Dependent",
    "specialRules": [
      "Flights are NEVER auto-approved. Mandatory Manager approval.",
      "Economy class mandatory for L1-L4.",
      "Premium Economy permitted for L5/Director on >6hr flights."
    ]
  },
  {
    "category": "Lodging",
    "maxAmountUSD": 250.0,
    "autoApproveLimitUSD": None,
    "receiptRequiredAboveUSD": 0.0,
    "requiresPreApproval": False,
    "gradeTier": "Grade Dependent",
    "specialRules": [
      "L1-L3 capped at $120/night. L4+ capped at $250/night.",
      "Itemized folio showing zero balance required.",
      "Lodging always requires manager approval."
    ]
  },
  {
    "category": "Client Entertainment",
    "maxAmountUSD": 500.0,
    "autoApproveLimitUSD": None,
    "receiptRequiredAboveUSD": 50.0,
    "requiresPreApproval": True,
    "gradeTier": "Manager+ (L5+)",
    "specialRules": [
      "Restricted to Grade L5+ (Manager / Director / VP).",
      "Itemized list of all internal and external attendees required.",
      "Capped at $500 per event."
    ]
  }
]

# Initial Claims
INITIAL_CLAIMS = [
  {
    "id": "claim-001",
    "claimNumber": "EXP-2026-8819",
    "employeeId": "emp-101",
    "employeeName": "Sarah Jenkins",
    "employeeGrade": "L3",
    "department": "Engineering",
    "expenseDate": "2026-07-20",
    "submissionDate": "2026-07-21",
    "category": "Meals",
    "subCategory": "Team Lunch",
    "amount": 22.50,
    "currency": "USD",
    "amountUSD": 22.50,
    "merchantVendor": "Sweetgreen SF",
    "purposeDescription": "Team project alignment lunch during Q3 sprint planning.",
    "receiptAttached": True,
    "receiptUrl": "https://picsum.photos/seed/receipt1/600/800",
    "extractedReceipt": {
      "fileName": "sweetgreen_receipt.png",
      "vendorName": "Sweetgreen SF",
      "transactionDate": "2026-07-20",
      "totalAmount": 22.50,
      "currency": "USD",
      "hasAlcohol": False,
      "lineItems": [
        {"description": "Harvest Bowl", "amount": 18.50, "category": "Food"},
        {"description": "Iced Green Tea", "amount": 4.00, "category": "Beverage"}
      ],
      "confidenceScore": 0.98
    },
    "policyValidation": {
      "overallPassed": True,
      "requiresManualReview": False,
      "isWithinMaxLimit": True,
      "isWithinAutoApproveLimit": True,
      "maxLimitAllowed": 40.0,
      "autoApproveLimit": 25.0,
      "receiptRequired": False,
      "receiptProvided": True,
      "daysSinceExpense": 1,
      "requiresDirectorApprovalForAge": False,
      "checks": [
        {
          "ruleId": "AGE_LIMIT_90_DAYS",
          "ruleName": "90-Day Submission Limit",
          "category": "Meals",
          "passed": True,
          "severity": "INFO",
          "message": "Submitted within 1 days of expense date."
        },
        {
          "ruleId": "MEAL_MAX_PASS",
          "ruleName": "Daily Meal Maximum ($40)",
          "category": "Meals",
          "passed": True,
          "severity": "INFO",
          "message": "Amount $22.50 is within $40.00 daily limit."
        }
      ],
      "reasoningSummary": "Claim satisfies all policy rules and falls within auto-approve threshold ($25.00)."
    },
    "fraudScreening": {
      "riskScore": 5,
      "isFlagged": False,
      "riskLevel": "LOW",
      "flags": [],
      "rationale": "No anomaly flags or suspicious patterns detected. Normal spend pattern.",
      "recommendedAction": "AUTO_APPROVE"
    },
    "status": "Auto_Approved",
    "workflowHistory": [
      {
        "timestamp": "2026-07-21T09:12:00Z",
        "actorName": "Sarah Jenkins",
        "actorRole": "employee",
        "stepName": "Submit Claim",
        "action": "Submitted expense claim with receipt",
        "status": "SUCCESS"
      },
      {
        "timestamp": "2026-07-21T09:12:02Z",
        "actorName": "AWS Lambda: Policy Engine",
        "actorRole": "admin",
        "stepName": "Policy Engine Check",
        "action": "Passed policy constraints",
        "status": "SUCCESS"
      }
    ],
    "comments": [
      {
        "id": "c-101",
        "authorName": "System Bot",
        "authorRole": "admin",
        "timestamp": "2026-07-21T09:12:03Z",
        "text": "Claim auto-approved per corporate meal policy."
      }
    ]
  },
  {
    "id": "claim-002",
    "claimNumber": "EXP-2026-9042",
    "employeeId": "emp-101",
    "employeeName": "Sarah Jenkins",
    "employeeGrade": "L3",
    "department": "Engineering",
    "expenseDate": "2026-07-18",
    "submissionDate": "2026-07-21",
    "category": "Ground Transport",
    "subCategory": "Taxi",
    "amount": 49.95,
    "currency": "USD",
    "amountUSD": 49.95,
    "merchantVendor": "Uber SF Airport",
    "purposeDescription": "Airport transport to SFO after customer onsite.",
    "receiptAttached": True,
    "receiptUrl": "https://picsum.photos/seed/receipt2/600/800",
    "extractedReceipt": {
      "fileName": "uber_ride.png",
      "vendorName": "Uber SF Airport",
      "transactionDate": "2026-07-18",
      "totalAmount": 49.95,
      "currency": "USD",
      "hasAlcohol": False,
      "lineItems": [{"description": "UberX Trip to SFO", "amount": 49.95}],
      "confidenceScore": 0.95
    },
    "policyValidation": {
      "overallPassed": True,
      "requiresManualReview": False,
      "isWithinMaxLimit": True,
      "isWithinAutoApproveLimit": True,
      "maxLimitAllowed": 150.0,
      "autoApproveLimit": 50.0,
      "receiptRequired": True,
      "receiptProvided": True,
      "daysSinceExpense": 3,
      "requiresDirectorApprovalForAge": False,
      "checks": [
        {
          "ruleId": "TAXI_LIMIT_PASS",
          "ruleName": "Ground Transport Limit ($150)",
          "category": "Ground Transport",
          "passed": True,
          "severity": "INFO",
          "message": "Amount $49.95 is within $150 limit."
        }
      ],
      "reasoningSummary": "Claim within auto-approve $50 ceiling."
    },
    "fraudScreening": {
      "riskScore": 15,
      "isFlagged": True,
      "riskLevel": "MEDIUM",
      "flags": [
        {
          "code": "THRESHOLD_ENGINEERING",
          "title": "Proximity to Policy Limit",
          "severity": "LOW",
          "description": "Expense amount ($49.95) is positioned within 1% of the $50 auto-approve ceiling.",
          "evidence": "Amount $49.95 vs limit $50.00"
        }
      ],
      "rationale": "Proximity to policy threshold flag triggered.",
      "recommendedAction": "MANAGER_REVIEW"
    },
    "status": "Manager_Review",
    "workflowHistory": [
      {
        "timestamp": "2026-07-21T10:15:00Z",
        "actorName": "Sarah Jenkins",
        "actorRole": "employee",
        "stepName": "Submit Claim",
        "action": "Submitted claim with receipt",
        "status": "SUCCESS"
      }
    ],
    "comments": []
  }
]

INITIAL_AUDIT_LOGS = [
  {
    "id": "audit-001",
    "timestamp": "2026-07-21T10:15:00Z",
    "actor": "Sarah Jenkins",
    "role": "employee",
    "eventType": "SUBMIT_CLAIM",
    "targetId": "EXP-2026-9042",
    "details": "Submitted claim for $49.95 (Ground Transport) -> Route: Manager Review",
    "ipAddress": "192.168.1.45"
  },
  {
    "id": "audit-002",
    "timestamp": "2026-07-21T09:12:00Z",
    "actor": "Sarah Jenkins",
    "role": "employee",
    "eventType": "SUBMIT_CLAIM",
    "targetId": "EXP-2026-8819",
    "details": "Submitted claim for $22.50 (Meals) -> Route: Auto Approved",
    "ipAddress": "192.168.1.45"
  }
]

# Global In-Memory Stores
claims_store: List[Dict[str, Any]] = list(INITIAL_CLAIMS)
policy_rules_store: List[Dict[str, Any]] = list(DEFAULT_POLICY_RULES)
audit_logs_store: List[Dict[str, Any]] = list(INITIAL_AUDIT_LOGS)

def add_audit_log(actor: str, role: str, event_type: str, target_id: str, details: str) -> Dict[str, Any]:
    entry = {
        "id": f"audit-{int(time.time()*1000)}-{random.randint(100, 999)}",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "actor": actor,
        "role": role,
        "eventType": event_type,
        "targetId": target_id,
        "details": details,
        "ipAddress": "127.0.0.1"
    }
    audit_logs_store.insert(0, entry)
    return entry
