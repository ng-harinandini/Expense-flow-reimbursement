import { PolicyRuleDefinition } from '../types';

/**
 * Authoritative Expense Reimbursement Policy Rules
 * Source of truth: Finance Department Expense Reimbursement Policy PDF
 */
export const DEFAULT_POLICY_RULES: PolicyRuleDefinition[] = [
  {
    category: 'Meals',
    gradeTier: 'All',
    maxAmountUSD: 40,
    autoApproveLimitUSD: 25,
    receiptRequiredAboveUSD: 25,
    specialRules: [
      'Covers breakfast, lunch, dinner, reasonable snacks while traveling or late work (past 8pm, pre-approved).',
      'Alcohol is NOT reimbursable except client entertainment, capped at 2 drinks per person.',
      'Itemized receipts preferred; merchant receipt with note of attendees acceptable for group meals.',
      'Daily team lunches/celebrations must be submitted under "Team events", not Meals.'
    ]
  },
  {
    category: 'Ground Transport',
    gradeTier: 'All',
    maxAmountUSD: 150,
    autoApproveLimitUSD: 50,
    receiptRequiredAboveUSD: 0,
    specialRules: [
      'Taxi / rideshare reimbursable for airport transfers, client visits, late-night travel (after 9pm) for safety.',
      'Commuting between home and regular office is NOT reimbursable.',
      'Personal vehicle mileage uses published mileage rate; requires trip log (origin, destination, purpose, miles).',
      'Parking/tolls max $30/day, auto-approve $30, receipt required above $10.'
    ]
  },
  {
    category: 'Flights',
    gradeTier: 'L1-L4: Economy (>=7 days advance) | L5-Director: Economy/Premium Economy >6hrs | VP+: Business >6hrs',
    maxAmountUSD: 'Per itinerary',
    autoApproveLimitUSD: null, // Always manual review
    receiptRequiredAboveUSD: 0,
    specialRules: [
      'ALWAYS manual review — flights are NEVER auto-approved.',
      'Change/cancellation fees reimbursable ONLY if trip cancelled for business reason beyond employee control.',
      'Personal travel added to a business trip is not reimbursable and must be cost-separated at booking.'
    ]
  },
  {
    category: 'Lodging',
    gradeTier: 'L1-L3: $120/night | L4+: $250/night',
    maxAmountUSD: 250,
    autoApproveLimitUSD: null, // Always manual review
    receiptRequiredAboveUSD: 0,
    specialRules: [
      'Covers standard hotel rooms in business-travel price range.',
      'Suites, resort fees, and minibar charges are NOT reimbursable.',
      'Conference negotiated rates above limits apply automatically if event registration attached.',
      'ALWAYS manual review.'
    ]
  },
  {
    category: 'Client Entertainment',
    gradeTier: 'Manager+',
    maxAmountUSD: 500,
    autoApproveLimitUSD: null, // Always manual review
    receiptRequiredAboveUSD: 50,
    specialRules: [
      'Requires Manager+ grade level.',
      'Requires listing all internal and external attendees and business purpose.',
      'Alcohol capped at 2 drinks per attendee.',
      'Gifts to clients capped separately at $75/recipient/year.'
    ]
  },
  {
    category: 'Communications & Connectivity',
    gradeTier: 'All / Remote roles',
    maxAmountUSD: 50,
    autoApproveLimitUSD: 50,
    receiptRequiredAboveUSD: 0,
    specialRules: [
      'Mobile phone reimbursement: $50/month (if role requires).',
      'Home internet: $40/month (remote/hybrid roles).',
      'Wi-Fi while traveling: $20/day (receipt required above $10).'
    ]
  },
  {
    category: 'Training & Professional Dev',
    gradeTier: 'All (with manager pre-approval)',
    maxAmountUSD: 2000,
    autoApproveLimitUSD: null, // Always manual review
    receiptRequiredAboveUSD: 0,
    specialRules: [
      'Includes courses, certifications, conference tickets, and relevant books.',
      'Requires manager pre-approval BEFORE expense is incurred.',
      'Claims without documented pre-approval route to Manager + Finance Director review.'
    ]
  },
  {
    category: 'Software & Subscriptions',
    gradeTier: 'All (role-relevant tools only)',
    maxAmountUSD: 300,
    autoApproveLimitUSD: 100,
    receiptRequiredAboveUSD: 0,
    specialRules: [
      'Must NOT duplicate a tool already provided centrally by IT.',
      'Personal productivity apps unrelated to job function are NOT reimbursable.',
      'Cap of $300/year per tool.'
    ]
  },
  {
    category: 'Team Events',
    gradeTier: 'Manager+ (organizer submits on behalf of team)',
    maxAmountUSD: 300, // $75/person x max 4 events/yr
    autoApproveLimitUSD: null, // Always manual review
    receiptRequiredAboveUSD: 0,
    specialRules: [
      '$75 per person per event, max 4 events per year.',
      'Organizer (Manager+) submits on behalf of team.',
      'Always routed for manual review.'
    ]
  },
  {
    category: 'Relocation',
    gradeTier: 'All approved relocations',
    maxAmountUSD: 'Per signed agreement',
    autoApproveLimitUSD: null, // Always manual review
    receiptRequiredAboveUSD: 0,
    specialRules: [
      'Governed by separate signed relocation agreement.',
      'Always manual review with Finance Director sign-off required.'
    ]
  },
  {
    category: 'Health & Wellness',
    gradeTier: 'All (if company benefit applies)',
    maxAmountUSD: 50,
    autoApproveLimitUSD: 50,
    receiptRequiredAboveUSD: 0,
    specialRules: [
      'Cap of $50/month.',
      'Auto-approve limit up to $50.'
    ]
  },
  {
    category: 'Misc / Other',
    gradeTier: 'All',
    maxAmountUSD: 50,
    autoApproveLimitUSD: null, // Always manual review
    receiptRequiredAboveUSD: 0,
    specialRules: [
      'Catch-all for legitimate business expenses (shipping, printing).',
      'Always routed to manual review as non-standard category.'
    ]
  }
];

export const GENERAL_POLICY_CONSTANTS = {
  CLAIM_AGE_MAX_DAYS: 90,
  CLAIMS_AGE_REQUIRES_FINANCE_DIRECTOR_DAYS: 90,
  LOST_RECEIPT_AFFIDAVIT_MAX_USD: 75,
  APPEAL_WINDOW_DAYS: 14
};
