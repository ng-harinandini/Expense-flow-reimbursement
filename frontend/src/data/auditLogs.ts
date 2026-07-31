import { AuditTrailClaim } from '../types';

/**
 * Per-claim audit trail, grouping every event (human or AI) logged against a
 * claim. TODO: replace with GET /api/audit-logs once the endpoint exists.
 */
export const INITIAL_AUDIT_TRAIL: AuditTrailClaim[] = [
  {
    id: 'audit-0841',
    claimRef: 'EXP-2026-0841',
    status: 'submitted',
    events: [
      {
        id: 'audit-0841-1',
        timestamp: '2026-07-20T10:14:02Z',
        actor: 'Sarah Chen',
        actorType: 'human',
        label: 'Claim submitted',
        detail: 'Submitted',
      },
      {
        id: 'audit-0841-2',
        timestamp: '2026-07-20T10:14:03Z',
        actor: 'AI Engine',
        actorType: 'ai',
        label: 'Receipt scanned',
        detail: '98% confidence',
      },
      {
        id: 'audit-0841-3',
        timestamp: '2026-07-20T10:14:04Z',
        actor: 'AI Engine',
        actorType: 'ai',
        label: 'Risk score computed',
        detail: 'Risk 5 · Auto Approved',
      },
    ],
  },
  {
    id: 'audit-0843',
    claimRef: 'EXP-2026-0843',
    status: 'fraud_review',
    riskScore: 82,
    events: [
      {
        id: 'audit-0843-1',
        timestamp: '2026-07-19T16:40:05Z',
        actor: 'AI Engine',
        actorType: 'ai',
        label: 'Fraud screening completed',
        detail: 'Risk 82 · High-risk anomalies detected',
      },
      {
        id: 'audit-0843-2',
        timestamp: '2026-07-19T16:41:17Z',
        actor: 'Priya Nair',
        actorType: 'human',
        label: 'Claim escalated',
        detail: 'Sent to fraud investigation queue',
      },
    ],
  },
  {
    id: 'audit-0842',
    claimRef: 'EXP-2026-0842',
    status: 'approved',
    events: [
      {
        id: 'audit-0842-1',
        timestamp: '2026-07-18T09:10:12Z',
        actor: 'Priya Nair',
        actorType: 'human',
        label: 'Manager review completed',
        detail: 'Approved, sent to finance',
      },
      {
        id: 'audit-0842-2',
        timestamp: '2026-07-18T09:15:31Z',
        actor: 'Alan Brooks',
        actorType: 'human',
        label: 'Finance review completed',
        detail: 'Approved for disbursement',
      },
    ],
  },
  {
    id: 'audit-0845',
    claimRef: 'EXP-2026-0845',
    status: 'submitted',
    events: [
      {
        id: 'audit-0845-1',
        timestamp: '2026-07-15T09:00:00Z',
        actor: 'David Miller',
        actorType: 'human',
        label: 'Claim submitted',
        detail: 'Submitted',
      },
    ],
  },
  {
    id: 'audit-0710',
    claimRef: 'EXP-2026-0710',
    status: 'submitted',
    events: [
      {
        id: 'audit-0710-1',
        timestamp: '2026-03-10T13:12:40Z',
        actor: 'Sarah Chen',
        actorType: 'human',
        label: 'Claim submitted',
        detail: 'Submitted',
      },
    ],
  },
];
