import { Employee, ExpenseClaim, AuditLogEntry } from '../types';

export const INITIAL_EMPLOYEES: Employee[] = [
  {
    id: 'emp-101',
    name: 'Sarah Chen',
    email: 'sarah.chen@acme.com',
    grade: 'L2',
    role: 'employee',
    status: 'active',
    managerId: 'emp-201',
    managerName: 'David Miller',
    monthlySpendUSD: 240
  },
  {
    id: 'emp-102',
    name: 'Marcus Brody',
    email: 'marcus.brody@acme.com',
    grade: 'L4',
    role: 'employee',
    status: 'active',
    managerId: 'emp-201',
    managerName: 'David Miller',
    monthlySpendUSD: 890
  },
  {
    id: 'emp-201',
    name: 'David Miller',
    email: 'david.miller@acme.com',
    grade: 'L5', // Manager+
    role: 'manager',
    status: 'active',
    managerId: 'emp-301',
    managerName: 'Alex Vance',
    monthlySpendUSD: 1450
  },
  {
    id: 'emp-301',
    name: 'Alex Vance',
    email: 'alex.vance@acme.com',
    grade: 'Director',
    role: 'finance',
    status: 'active',
    monthlySpendUSD: 3100
  }
];

export const INITIAL_CLAIMS: ExpenseClaim[] = [
  {
    id: 'claim-1001',
    claimNumber: 'EXP-2026-0841',
    employeeId: 'emp-101',
    employeeName: 'Sarah Chen',
    employeeGrade: 'L2',
    department: 'Engineering',
    expenseDate: '2026-07-20',
    submissionDate: '2026-07-22',
    category: 'Meals',
    subCategory: 'Working Late Dinner',
    amount: 22.50,
    currency: 'USD',
    amountUSD: 22.50,
    merchantVendor: 'Sweetgreen Salad Bar',
    purposeDescription: 'Working late past 8pm on sprint release hotfix',
    receiptAttached: true,
    receiptUrl: 'https://images.unsplash.com/photo-1546069901-ba9599a7e63c?auto=format&fit=crop&w=600&q=80',
    extractedReceipt: {
      fileName: 'sweetgreen_receipt_0720.png',
      vendorName: 'Sweetgreen #104',
      transactionDate: '2026-07-20',
      totalAmount: 22.50,
      currency: 'USD',
      lineItems: [
        { description: 'Harvest Bowl with Tofu', amount: 16.50, category: 'Food' },
        { description: 'Iced Green Tea', amount: 4.00, category: 'Beverage' },
        { description: 'Sales Tax', amount: 2.00, category: 'Tax' }
      ],
      hasAlcohol: false,
      confidenceScore: 0.98
    },
    policyValidation: {
      overallPassed: true,
      requiresManualReview: false,
      isWithinMaxLimit: true,
      isWithinAutoApproveLimit: true,
      maxLimitAllowed: 40,
      autoApproveLimit: 25,
      receiptRequired: false,
      receiptProvided: true,
      daysSinceExpense: 3,
      requiresDirectorApprovalForAge: false,
      checks: [
        {
          ruleId: 'MEAL_MAX_LIMIT',
          ruleName: 'Daily Meal Maximum',
          category: 'Meals',
          passed: true,
          severity: 'INFO',
          message: 'Claim $22.50 is under daily meal max ($40.00).'
        },
        {
          ruleId: 'MEAL_AUTO_APPROVE',
          ruleName: 'Auto-Approve Threshold',
          category: 'Meals',
          passed: true,
          severity: 'INFO',
          message: 'Amount $22.50 qualifies for auto-approval (<= $25.00).'
        },
        {
          ruleId: 'NO_ALCOHOL_CHECK',
          ruleName: 'Alcohol Policy Check',
          category: 'Meals',
          passed: true,
          severity: 'INFO',
          message: 'No alcohol detected on receipt.'
        }
      ],
      reasoningSummary: 'Claim passes all policy limits and falls under the $25 auto-approve threshold with valid itemized receipt.'
    },
    fraudScreening: {
      riskScore: 5,
      isFlagged: false,
      riskLevel: 'LOW',
      flags: [],
      rationale: 'Legitimate merchant, realistic amount, no duplicate transactions found.',
      recommendedAction: 'AUTO_APPROVE'
    },
    status: 'auto_approved',
    workflowHistory: [
      {
        timestamp: '2026-07-22T09:15:00Z',
        actorName: 'Sarah Chen',
        actorRole: 'employee',
        stepName: 'Submit Claim',
        action: 'Submitted expense claim with receipt attached',
        status: 'SUCCESS',
        traceId: 'trace-sf-1001-01'
      },
      {
        timestamp: '2026-07-22T09:15:04Z',
        actorName: 'AWS Lambda: OCR Textract',
        actorRole: 'admin',
        stepName: 'AI OCR Extraction',
        action: 'Successfully extracted vendor Sweetgreen #104 ($22.50)',
        status: 'SUCCESS',
        traceId: 'trace-sf-1001-02'
      },
      {
        timestamp: '2026-07-22T09:15:08Z',
        actorName: 'AWS Lambda: Policy Engine',
        actorRole: 'admin',
        stepName: 'Policy Validation',
        action: 'Verified within $25 auto-approve limit. Zero violations.',
        status: 'SUCCESS',
        traceId: 'trace-sf-1001-03'
      },
      {
        timestamp: '2026-07-22T09:15:10Z',
        actorName: 'Step Functions Workflow',
        actorRole: 'admin',
        stepName: 'Auto Approval',
        action: 'System auto-approved claim EXP-2026-0841',
        status: 'SUCCESS',
        traceId: 'trace-sf-1001-04'
      }
    ],
    comments: [
      {
        id: 'c-1',
        authorName: 'System Bot',
        authorRole: 'admin',
        timestamp: '2026-07-22T09:15:10Z',
        text: 'Claim auto-approved per Policy Engine rule MEAL_AUTO_APPROVE.'
      }
    ]
  },
  {
    id: 'claim-1002',
    claimNumber: 'EXP-2026-0842',
    employeeId: 'emp-102',
    employeeName: 'Marcus Brody',
    employeeGrade: 'L4',
    department: 'Product Development',
    expenseDate: '2026-07-18',
    submissionDate: '2026-07-21',
    category: 'Lodging',
    subCategory: 'Hotel Stay - Client Visit',
    amount: 180.00,
    currency: 'USD',
    amountUSD: 180.00,
    merchantVendor: 'Marriott Downtown',
    purposeDescription: '1 Night stay for client site architecture review in Chicago',
    receiptAttached: true,
    receiptUrl: 'https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=600&q=80',
    extractedReceipt: {
      fileName: 'marriott_folio_0718.pdf',
      vendorName: 'Marriott Downtown Chicago',
      transactionDate: '2026-07-18',
      totalAmount: 180.00,
      currency: 'USD',
      lineItems: [
        { description: 'Standard King Room', amount: 155.00 },
        { description: 'City Tourism Tax', amount: 25.00 }
      ],
      hasAlcohol: false,
      confidenceScore: 0.96
    },
    policyValidation: {
      overallPassed: true,
      requiresManualReview: true,
      isWithinMaxLimit: true,
      isWithinAutoApproveLimit: false,
      maxLimitAllowed: 250,
      autoApproveLimit: 0,
      receiptRequired: true,
      receiptProvided: true,
      daysSinceExpense: 5,
      requiresDirectorApprovalForAge: false,
      checks: [
        {
          ruleId: 'LODGING_GRADE_LIMIT',
          ruleName: 'Grade L4 Lodging Max ($250/night)',
          category: 'Lodging',
          passed: true,
          severity: 'INFO',
          message: 'Amount $180.00 is within grade L4 lodging limit ($250.00).'
        },
        {
          ruleId: 'LODGING_ALWAYS_MANUAL',
          ruleName: 'Lodging Manual Review Requirement',
          category: 'Lodging',
          passed: false,
          severity: 'REQUIREMENT',
          message: 'Lodging expenses ALWAYS require manual manager review per section 4.4.'
        }
      ],
      reasoningSummary: 'Lodging amount $180/night is compliant for grade L4 ($250 max), but requires mandatory human manager review per policy.'
    },
    fraudScreening: {
      riskScore: 12,
      isFlagged: false,
      riskLevel: 'LOW',
      flags: [],
      rationale: 'Itemized hotel folio matched perfectly, vendor is a standard corporate hotel chain.',
      recommendedAction: 'MANAGER_REVIEW'
    },
    status: 'manager_review',
    workflowHistory: [
      {
        timestamp: '2026-07-21T14:20:00Z',
        actorName: 'Marcus Brody',
        actorRole: 'employee',
        stepName: 'Submit Claim',
        action: 'Submitted lodging claim with itemized folio',
        status: 'SUCCESS',
        traceId: 'trace-sf-1002-01'
      },
      {
        timestamp: '2026-07-21T14:20:05Z',
        actorName: 'AWS Lambda: Policy Engine',
        actorRole: 'admin',
        stepName: 'Policy Validation',
        action: 'Passed amount check ($180 < $250). Flagged for mandatory Manager Review.',
        status: 'WARNING',
        traceId: 'trace-sf-1002-02'
      }
    ],
    comments: []
  },
  {
    id: 'claim-1003',
    claimNumber: 'EXP-2026-0843',
    employeeId: 'emp-101',
    employeeName: 'Sarah Chen',
    employeeGrade: 'L2',
    department: 'Engineering',
    expenseDate: '2026-07-19',
    submissionDate: '2026-07-23',
    category: 'Ground Transport',
    subCategory: 'Taxi / Rideshare',
    amount: 48.50,
    currency: 'USD',
    amountUSD: 48.50,
    merchantVendor: 'Uber Technologies',
    purposeDescription: 'Rideshare ride across town to partner meeting',
    receiptAttached: true,
    extractedReceipt: {
      fileName: 'uber_trip_1.pdf',
      vendorName: 'Uber Technologies',
      transactionDate: '2026-07-19',
      totalAmount: 48.50,
      currency: 'USD',
      lineItems: [{ description: 'UberX Trip', amount: 48.50 }],
      hasAlcohol: false,
      confidenceScore: 0.95
    },
    policyValidation: {
      overallPassed: true,
      requiresManualReview: false,
      isWithinMaxLimit: true,
      isWithinAutoApproveLimit: true,
      maxLimitAllowed: 150,
      autoApproveLimit: 50,
      receiptRequired: true,
      receiptProvided: true,
      daysSinceExpense: 4,
      requiresDirectorApprovalForAge: false,
      checks: [
        {
          ruleId: 'TAXI_AUTO_APPROVE',
          ruleName: 'Rideshare Limit',
          category: 'Ground Transport',
          passed: true,
          severity: 'INFO',
          message: '$48.50 is under $50 auto-approve threshold.'
        }
      ],
      reasoningSummary: 'Amount appears under $50 limit, but anomaly screening detected a split transaction pattern with same-day rideshare.'
    },
    fraudScreening: {
      riskScore: 82,
      isFlagged: true,
      riskLevel: 'HIGH',
      flags: [
        {
          code: 'SPLIT_TRANSACTION',
          title: 'Possible Split Transaction Detected',
          severity: 'HIGH',
          description: 'Two separate same-day claims (EXP-2026-0843 $48.50 and EXP-2026-0844 $49.20) submitted for the same merchant within 3 hours. Total = $97.70 exceeds single trip auto-approval threshold.',
          evidence: 'Uber trip 1: $48.50 (14:10), Uber trip 2: $49.20 (16:45) on 2026-07-19.'
        },
        {
          code: 'THRESHOLD_PROXIMITY',
          title: 'Suspicious Proximity to Auto-Approve Limit',
          severity: 'MEDIUM',
          description: 'Amount $48.50 is engineered just $1.50 below the $50.00 receipt auto-approval ceiling.',
          evidence: '$48.50 / $50.00 = 97% of threshold.'
        }
      ],
      rationale: 'High probability of artificial transaction splitting to bypass manager review and receipt verification limits.',
      recommendedAction: 'FINANCE_AUDIT'
    },
    status: 'fraud_review',
    workflowHistory: [
      {
        timestamp: '2026-07-23T10:00:00Z',
        actorName: 'Sarah Chen',
        actorRole: 'employee',
        stepName: 'Submit Claim',
        action: 'Submitted rideshare claim',
        status: 'SUCCESS'
      },
      {
        timestamp: '2026-07-23T10:00:04Z',
        actorName: 'AWS Lambda: Anomaly Engine',
        actorRole: 'admin',
        stepName: 'Fraud Screening',
        action: 'FLAGGED: Split transaction detected with claim EXP-2026-0844. Risk Score 82/100.',
        status: 'FAILED'
      }
    ],
    comments: [
      {
        id: 'c-2',
        authorName: 'Fraud AI Agent',
        authorRole: 'admin',
        timestamp: '2026-07-23T10:00:04Z',
        text: 'Automated Fraud Flag: High risk split transaction pattern detected across multiple Uber rides.'
      }
    ]
  },
  {
    id: 'claim-1004',
    claimNumber: 'EXP-2026-0845',
    employeeId: 'emp-201',
    employeeName: 'David Miller',
    employeeGrade: 'L5', // Manager+
    department: 'Engineering Lead',
    expenseDate: '2026-07-15',
    submissionDate: '2026-07-20',
    category: 'Client Entertainment',
    subCategory: 'Client Dinner',
    amount: 340.00,
    currency: 'USD',
    amountUSD: 340.00,
    merchantVendor: 'Prime Steakhouse',
    purposeDescription: 'Dinner meeting with VP of Technology from Enterprise Client (Nexus Corp)',
    attendees: 'David Miller (Manager), Jane Doe (Client VP), Bob Smith (Client Arch)',
    receiptAttached: true,
    extractedReceipt: {
      fileName: 'prime_steakhouse.png',
      vendorName: 'Prime Steakhouse NYC',
      transactionDate: '2026-07-15',
      totalAmount: 340.00,
      currency: 'USD',
      lineItems: [
        { description: 'Ribeye Steak (x2)', amount: 140.00 },
        { description: 'Pan Seared Salmon', amount: 42.00 },
        { description: 'Napa Valley Cabernet (3 glasses total)', amount: 54.00 },
        { description: 'Appetizers & Sides', amount: 50.00 },
        { description: 'Tax & Gratuity', amount: 54.00 }
      ],
      hasAlcohol: true,
      alcoholItemCount: 3,
      attendeesNoted: ['David Miller', 'Jane Doe', 'Bob Smith'],
      confidenceScore: 0.97
    },
    policyValidation: {
      overallPassed: true,
      requiresManualReview: true,
      isWithinMaxLimit: true,
      isWithinAutoApproveLimit: false,
      maxLimitAllowed: 500,
      autoApproveLimit: 0,
      receiptRequired: true,
      receiptProvided: true,
      daysSinceExpense: 8,
      requiresDirectorApprovalForAge: false,
      checks: [
        {
          ruleId: 'CLIENT_ENT_GRADE',
          ruleName: 'Manager+ Grade Requirement',
          category: 'Client Entertainment',
          passed: true,
          severity: 'INFO',
          message: 'Employee grade L5 meets Manager+ requirement.'
        },
        {
          ruleId: 'CLIENT_ENT_MAX',
          ruleName: 'Event Maximum ($500)',
          category: 'Client Entertainment',
          passed: true,
          severity: 'INFO',
          message: 'Amount $340.00 is under $500 event cap.'
        },
        {
          ruleId: 'ALCOHOL_CAP_CHECK',
          ruleName: 'Alcohol 2 Drink / Person Cap',
          category: 'Client Entertainment',
          passed: true,
          severity: 'INFO',
          message: '3 drinks split among 3 attendees (1.0 drink/person) is within 2 drink cap.'
        },
        {
          ruleId: 'ATTENDEE_LIST_CHECK',
          ruleName: 'Attendee Listing Requirement',
          category: 'Client Entertainment',
          passed: true,
          severity: 'INFO',
          message: 'All 3 internal and external attendees listed in documentation.'
        }
      ],
      reasoningSummary: 'Client entertainment claim complies with grade L5 authorization, $500 event limit, attendee list, and alcohol cap (1 drink/person). Requires Finance Review.'
    },
    fraudScreening: {
      riskScore: 8,
      isFlagged: false,
      riskLevel: 'LOW',
      flags: [],
      rationale: 'Detailed itemized receipt provided with exact attendee match and compliant drink ratio.',
      recommendedAction: 'MANAGER_REVIEW'
    },
    status: 'finance_review',
    workflowHistory: [
      {
        timestamp: '2026-07-20T11:00:00Z',
        actorName: 'David Miller',
        actorRole: 'employee',
        stepName: 'Submit Claim',
        action: 'Submitted client entertainment expense with attendee breakdown',
        status: 'SUCCESS'
      },
      {
        timestamp: '2026-07-20T11:00:05Z',
        actorName: 'AWS Lambda: Policy Engine',
        actorRole: 'admin',
        stepName: 'Policy Validation',
        action: 'Validated all rules. Passed. Routed to Finance Review.',
        status: 'SUCCESS'
      }
    ],
    comments: []
  },
  {
    id: 'claim-1005',
    claimNumber: 'EXP-2026-0710',
    employeeId: 'emp-101',
    employeeName: 'Sarah Chen',
    employeeGrade: 'L2',
    department: 'Engineering',
    expenseDate: '2026-03-10', // 135 days old
    submissionDate: '2026-07-23',
    category: 'Software & Subscriptions',
    subCategory: 'Developer Utility Tool',
    amount: 120.00,
    currency: 'USD',
    amountUSD: 120.00,
    merchantVendor: 'JetBrains Annual License',
    purposeDescription: 'IDE subscription renewal',
    receiptAttached: true,
    extractedReceipt: {
      fileName: 'jetbrains_invoice.pdf',
      vendorName: 'JetBrains s.r.o.',
      transactionDate: '2026-03-10',
      totalAmount: 120.00,
      currency: 'USD',
      lineItems: [{ description: 'WebStorm Annual', amount: 120.00 }],
      hasAlcohol: false,
      confidenceScore: 0.99
    },
    policyValidation: {
      overallPassed: false,
      requiresManualReview: true,
      isWithinMaxLimit: true,
      isWithinAutoApproveLimit: false,
      maxLimitAllowed: 300,
      autoApproveLimit: 100,
      receiptRequired: true,
      receiptProvided: true,
      daysSinceExpense: 135,
      requiresDirectorApprovalForAge: true,
      checks: [
        {
          ruleId: 'EXPIRED_SUBMISSION_WINDOW',
          ruleName: '90-Day Submission Limit',
          category: 'Software & Subscriptions',
          passed: false,
          severity: 'VIOLATION',
          message: 'Claim submitted 135 days after expense date. Exceeds mandatory 90-day window per Section 2.'
        },
        {
          ruleId: 'REQUIRES_FINANCE_DIRECTOR_SIGN_OFF',
          ruleName: 'Finance Director Special Escalation',
          category: 'Software & Subscriptions',
          passed: false,
          severity: 'REQUIREMENT',
          message: 'Claims submitted after 90 days REQUIRE Finance Director written approval and justification.'
        }
      ],
      reasoningSummary: 'Policy Violation: Claim submitted 135 days after expense date (max 90 days). Must be escalated directly to Finance Director (Alex Vance).'
    },
    fraudScreening: {
      riskScore: 35,
      isFlagged: false,
      riskLevel: 'MEDIUM',
      flags: [
        {
          code: 'AGED_CLAIM',
          title: 'Stale Expense Submission (>90 days)',
          severity: 'MEDIUM',
          description: 'Submission delayed by over 4 months.',
          evidence: 'Expense date: 2026-03-10. Submission date: 2026-07-23.'
        }
      ],
      rationale: 'No fraud indicators, but policy age threshold violated.',
      recommendedAction: 'FINANCE_AUDIT'
    },
    status: 'manager_review',
    workflowHistory: [
      {
        timestamp: '2026-07-23T11:30:00Z',
        actorName: 'Sarah Chen',
        actorRole: 'employee',
        stepName: 'Submit Claim',
        action: 'Submitted delayed software reimbursement',
        status: 'SUCCESS'
      },
      {
        timestamp: '2026-07-23T11:30:05Z',
        actorName: 'AWS Lambda: Policy Engine',
        actorRole: 'admin',
        stepName: 'Policy Validation',
        action: 'VIOLATION: Exceeds 90-day submission limit. Flagged for Finance Director review.',
        status: 'FAILED'
      }
    ],
    comments: [
      {
        id: 'c-3',
        authorName: 'Sarah Chen',
        authorRole: 'employee',
        timestamp: '2026-07-23T11:30:00Z',
        text: 'Apologies for late submission - receipt was buried in old email archives.'
      }
    ]
  }
];

export const INITIAL_AUDIT_LOGS: AuditLogEntry[] = [
  {
    id: 'audit-001',
    timestamp: '2026-07-23T11:30:05Z',
    actor: 'AWS Lambda: Policy Engine',
    role: 'admin',
    eventType: 'POLICY_EVALUATION',
    targetId: 'EXP-2026-0710',
    details: 'Flagged 90-day submission violation. Escalated to Finance Director.',
    ipAddress: '10.0.12.45'
  },
  {
    id: 'audit-002',
    timestamp: '2026-07-23T10:00:04Z',
    actor: 'AWS Lambda: Anomaly Engine',
    role: 'admin',
    eventType: 'FRAUD_FLAG',
    targetId: 'EXP-2026-0843',
    details: 'Flagged split transaction fraud risk score 82/100.',
    ipAddress: '10.0.12.46'
  },
  {
    id: 'audit-003',
    timestamp: '2026-07-22T09:15:10Z',
    actor: 'Step Functions Engine',
    role: 'admin',
    eventType: 'SUBMIT_CLAIM',
    targetId: 'EXP-2026-0841',
    details: 'Auto-approved claim $22.50 per section 4.1 Meals policy.',
    ipAddress: '10.0.12.40'
  }
];
