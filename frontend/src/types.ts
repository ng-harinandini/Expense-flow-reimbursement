export type UserRole = 'employee' | 'manager' | 'finance' | 'admin' | 'auditor';

export type UserRoleIds= 1 | 2 | 3 | 4 | 5;

export interface AuthenticatedUser {
  sub: string | null;
  email: string | null;
  name?: string | null;    // employees.full_name
  role: UserRole | null;
  employeeId: string | null;  // employees.id
  employeeCode: string;
}

export type EmployeeGrade = 'L1' | 'L2' | 'L3' | 'L4' | 'L5' | 'Director' | 'VP';

export type EmployeeStatus = 'active' | 'inactive';

export interface Employee {
  id: string;
  name: string;
  email: string;
  grade: EmployeeGrade;
  role: UserRole;
  status: EmployeeStatus;
  managerId?: string;
  managerName?: string;
  avatarUrl?: string;
  monthlySpendUSD: number;
  employeeRecordId?: string;
  roleId?: number;
}

export type ExpenseCategory =
  | 'Meals'
  | 'Ground Transport'
  | 'Flights'
  | 'Lodging'
  | 'Client Entertainment'
  | 'Communications & Connectivity'
  | 'Training & Professional Dev'
  | 'Software & Subscriptions'
  | 'Team Events'
  | 'Relocation'
  | 'Health & Wellness'
  | 'Misc / Other';

export type ClaimStatus =
  | 'Draft'
  | 'Submitted'
  | 'Processing_AI'
  | 'Auto_Approved'
  | 'Manager_Review'
  | 'Finance_Review'
  | 'Approved'
  | 'Rejected'
  | 'Disbursed'
  | 'Flagged_Fraud'
  | 'Withdrawn';

export type ExpenseItemStatus =
  | 'Submitted'
  | 'Auto_Approved'
  | 'Policy_Hold'
  | 'Fraud_Flag'
  | 'Manager_Approved'
  | 'Rejected';

export interface ReceiptData {
  fileName?: string;
  vendorName: string;
  transactionDate: string;
  totalAmount: number;
  currency: string;
  taxAmount?: number;
  lineItems: Array<{
    description: string;
    amount: number;
    category?: string;
  }>;
  hasAlcohol: boolean;
  alcoholItemCount?: number;
  attendeesNoted?: string[];
  rawOcrText?: string;
  confidenceScore: number;
}

export interface PolicyCheckResult {
  ruleId: string;
  ruleName: string;
  category: ExpenseCategory;
  passed: boolean;
  severity: 'INFO' | 'WARNING' | 'VIOLATION' | 'REQUIREMENT';
  message: string;
  details?: string;
}

export interface PolicyValidationReport {
  overallPassed: boolean;
  requiresManualReview: boolean;
  isWithinMaxLimit: boolean;
  isWithinAutoApproveLimit: boolean;
  maxLimitAllowed: number;
  autoApproveLimit: number;
  receiptRequired: boolean;
  receiptProvided: boolean;
  daysSinceExpense: number;
  requiresDirectorApprovalForAge: boolean;
  checks: PolicyCheckResult[];
  reasoningSummary: string;
}

export type AnomalySeverity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export interface AnomalyFlag {
  code: string;
  title: string;
  severity: AnomalySeverity;
  description: string;
  evidence: string;
}

export interface FraudScreeningReport {
  riskScore: number; // 0 to 100
  isFlagged: boolean;
  riskLevel: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  flags: AnomalyFlag[];
  rationale: string;
  recommendedAction: 'AUTO_APPROVE' | 'MANAGER_REVIEW' | 'FINANCE_AUDIT' | 'INVESTIGATE_FRAUD';
}

export interface WorkflowStepLog {
  timestamp: string;
  actorName: string;
  actorRole: UserRole;
  stepName: string;
  action: string;
  status: 'SUCCESS' | 'WARNING' | 'FAILED' | 'PENDING';
  notes?: string;
  traceId?: string;
}

export interface ClaimComment {
  id: string;
  authorName: string;
  authorRole: UserRole;
  timestamp: string;
  text: string;
}

export interface ExpenseClaim {
  id: string;
  claimNumber: string;
  employeeId: string;
  employeeName: string;
  employeeGrade: EmployeeGrade;
  department: string;
  
  expenseDate: string;
  submissionDate: string;
  category: ExpenseCategory;
  subCategory?: string;
  amount: number;
  currency: string;
  amountUSD: number;
  
  merchantVendor: string;
  purposeDescription: string;
  attendees?: string;
  tripLog?: {
    origin: string;
    destination: string;
    purpose: string;
    miles: number;
  };
  hasPreApproval?: boolean;
  preApprovalDocRef?: string;
  
  receiptAttached: boolean;
  receiptUrl?: string;
  extractedReceipt?: ReceiptData;
  
  policyValidation?: PolicyValidationReport;
  fraudScreening?: FraudScreeningReport;
  
  status: ClaimStatus;
  workflowHistory: WorkflowStepLog[];
  comments: ClaimComment[];
}

/** One expense line item within a multi-item Claim (see submit-expense flow). */
export interface ClaimExpenseItem {
  id: string;
  category: ExpenseCategory;
  merchantVendor: string;
  expenseDate: string;
  description: string;
  amount: number;
  currency: string;
  receiptUrl: string;
  status: ExpenseItemStatus;
}

/** A claim raised for a trip/purchase, grouping one or more expense items. */
export interface Claim {
  id: string;
  claimNumber: string;
  employeeId: string;
  employeeName: string;
  claimTitle: string;
  fromDate: string;
  toDate: string;
  status: ClaimStatus;
  totalAmount: number;
  items: ClaimExpenseItem[];
  workflowHistory: WorkflowStepLog[];
  withdrawnAt?: string | null;
  withdrawalReason?: string | null;
}

export interface PolicyRuleDefinition {
  category: ExpenseCategory;
  gradeTier: string;
  maxAmountUSD: number | string; // e.g. 40 or 'Per signed agreement'
  autoApproveLimitUSD: number | null; // null means 'always manual'
  receiptRequiredAboveUSD: number;
  specialRules: string[];
}

/** A single admin-managed policy rule row, shown on the Policy Guidelines page. */
export interface AdminPolicyRule {
  id: number;
  code?: string;
  category: ExpenseCategory;
  gradeApplicable: string; // e.g. 'All', 'L1-L3', 'L4+', 'Manager+'
  maxAmount: number;
  maxAmountUnit: string; // e.g. 'day', 'trip', 'night', 'event'
  autoApproveLimit: number | null; // null means 'always manual review'
  requiresReceiptAbove: number;
  effectiveFrom: string; // ISO date
}

export interface AuditLogEntry {
  id: string;
  timestamp: string;
  actor: string;
  role: UserRole;
  eventType: 'SUBMIT_CLAIM' | 'POLICY_EVALUATION' | 'FRAUD_FLAG' | 'APPROVAL_ACTION' | 'POLICY_UPDATE' | 'IAM_REFINED';
  targetId: string;
  details: string;
  ipAddress: string;
}

export type AuditActorType = 'human' | 'ai';

/** One timeline entry within a claim's audit trail, shown when its row is expanded. */
export interface AuditTrailEvent {
  id: string;
  timestamp: string; // ISO 8601
  actor: string;
  actorType: AuditActorType;
  label: string; // e.g. 'Claim submitted'
  detail: string; // e.g. 'Submitted' or 'Risk 5 · Auto Approved'
}

/** A claim's full audit trail, grouping every event logged against it, for the Audit Logs page. */
export interface AuditTrailClaim {
  id: string;
  claimRef: string;
  status: ClaimStatus;
  riskScore?: number;
  events: AuditTrailEvent[];
}

export interface IamPolicyRefinementRequest {
  currentPolicyJson: string;
  environmentName: string;
  useCaseDescription: string;
}

export interface IamPolicyRefinementResponse {
  refinedPolicyJson: string;
  securityScore: number; // 0 to 100
  summaryOfChanges: string[];
  leastPrivilegeViolationsFixed: string[];
  recommendedAwsServices: string[];
  terraformCode: string;
  samTemplateYaml: string;
}
