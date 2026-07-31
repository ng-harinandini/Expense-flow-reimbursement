import React, { useState } from 'react';
import { Employee, ExpenseCategory, ExpenseClaim, ReceiptData } from '../types';
import { Upload, Sparkles, AlertCircle, CheckCircle2, Clock, FileText, ArrowRight, X } from 'lucide-react';

interface ExpenseSubmitModalProps {
  currentEmployee: Employee;
  onSubmitClaim: (claimData: Partial<ExpenseClaim>) => void;
  onClose?: () => void;
}

const SAMPLE_RECEIPTS = [
  {
    name: 'Sweetgreen Late Night Meal ($22.50)',
    vendor: 'Sweetgreen #104',
    category: 'Meals' as ExpenseCategory,
    amount: 22.50,
    date: '2026-07-22',
    description: 'Working late past 8pm on hotfix release',
    hasAlcohol: false,
    lineItems: [
      { description: 'Harvest Bowl Tofu', amount: 16.50 },
      { description: 'Iced Green Tea', amount: 4.00 },
      { description: 'Tax', amount: 2.00 }
    ],
    previewUrl: 'https://images.unsplash.com/photo-1546069901-ba9599a7e63c?auto=format&fit=crop&w=600&q=80'
  },
  {
    name: 'Marriott Chicago Hotel Stay ($180.00)',
    vendor: 'Marriott Downtown Chicago',
    category: 'Lodging' as ExpenseCategory,
    amount: 180.00,
    date: '2026-07-21',
    description: '1 Night stay for client Architecture review',
    hasAlcohol: false,
    lineItems: [
      { description: 'King Standard Room', amount: 155.00 },
      { description: 'City Tourism Tax', amount: 25.00 }
    ],
    previewUrl: 'https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=600&q=80'
  },
  {
    name: 'Uber Airport Ride ($48.50)',
    vendor: 'Uber Technologies',
    category: 'Ground Transport' as ExpenseCategory,
    amount: 48.50,
    date: '2026-07-23',
    description: 'Rideshare airport transfer for client meeting',
    hasAlcohol: false,
    lineItems: [
      { description: 'UberX Airport Transfer', amount: 48.50 }
    ],
    previewUrl: 'https://images.unsplash.com/photo-1557223562-6c77ef16210f?auto=format&fit=crop&w=600&q=80'
  },
  {
    name: 'Prime Steakhouse Client Dinner ($340.00 - Alcohol Included)',
    vendor: 'Prime Steakhouse',
    category: 'Client Entertainment' as ExpenseCategory,
    amount: 340.00,
    date: '2026-07-20',
    description: 'Client dinner with VP of Engineering from Nexus Corp',
    attendees: 'David Miller (Manager), Jane Doe (Client VP), Bob Smith (Client Arch)',
    hasAlcohol: true,
    lineItems: [
      { description: 'Ribeye Steak (x2)', amount: 140.00 },
      { description: 'Pan Seared Salmon', amount: 42.00 },
      { description: 'Napa Valley Wine (3 glasses)', amount: 54.00 },
      { description: 'Sides & Dessert', amount: 50.00 },
      { description: 'Tax & Tip', amount: 54.00 }
    ],
    previewUrl: 'https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?auto=format&fit=crop&w=600&q=80'
  },
  {
    name: 'JetBrains IDE Subscription ($120.00 - Stale >90 days)',
    vendor: 'JetBrains s.r.o.',
    category: 'Software & Subscriptions' as ExpenseCategory,
    amount: 120.00,
    date: '2026-03-10', // 135 days old
    description: 'Developer IDE WebStorm license renewal',
    hasAlcohol: false,
    lineItems: [
      { description: 'WebStorm Annual License', amount: 120.00 }
    ],
    previewUrl: 'https://images.unsplash.com/photo-1555066931-4365d14bab8c?auto=format&fit=crop&w=600&q=80'
  }
];

export const ExpenseSubmitModal: React.FC<ExpenseSubmitModalProps> = ({
  currentEmployee,
  onSubmitClaim,
  onClose
}) => {
  const [category, setCategory] = useState<ExpenseCategory>('Meals');
  const [vendor, setVendor] = useState('Sweetgreen #104');
  const [amount, setAmount] = useState('22.50');
  const [expenseDate, setExpenseDate] = useState('2026-07-22');
  const [description, setDescription] = useState('Working late past 8pm on hotfix release');
  const [attendees, setAttendees] = useState('');
  const [selectedReceiptUrl, setSelectedReceiptUrl] = useState('https://images.unsplash.com/photo-1546069901-ba9599a7e63c?auto=format&fit=crop&w=600&q=80');

  const [isOcrProcessing, setIsOcrProcessing] = useState(false);
  const [ocrResult, setOcrResult] = useState<ReceiptData | null>({
    fileName: 'sweetgreen_receipt.png',
    vendorName: 'Sweetgreen #104',
    transactionDate: '2026-07-22',
    totalAmount: 22.50,
    currency: 'USD',
    hasAlcohol: false,
    lineItems: [
      { description: 'Harvest Bowl Tofu', amount: 16.50 },
      { description: 'Iced Green Tea', amount: 4.00 },
      { description: 'Tax', amount: 2.00 }
    ],
    confidenceScore: 0.98
  });

  const handleSelectSample = (sample: typeof SAMPLE_RECEIPTS[0]) => {
    setIsOcrProcessing(true);
    setCategory(sample.category);
    setVendor(sample.vendor);
    setAmount(sample.amount.toString());
    setExpenseDate(sample.date);
    setDescription(sample.description);
    setAttendees(sample.attendees || '');
    setSelectedReceiptUrl(sample.previewUrl);

    setTimeout(() => {
      setOcrResult({
        fileName: `${(sample.vendor || 'receipt').toLowerCase().replace(/[^a-z0-9]/g, '_')}_receipt.png`,
        vendorName: sample.vendor,
        transactionDate: sample.date,
        totalAmount: sample.amount,
        currency: 'USD',
        hasAlcohol: sample.hasAlcohol,
        lineItems: sample.lineItems,
        confidenceScore: 0.97
      });
      setIsOcrProcessing(false);
    }, 600);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const parsedAmount = parseFloat(amount) || 0;

    onSubmitClaim({
      employeeId: currentEmployee.id,
      employeeName: currentEmployee.name,
      employeeGrade: currentEmployee.grade,
      category,
      merchantVendor: vendor,
      amount: parsedAmount,
      amountUSD: parsedAmount,
      expenseDate,
      purposeDescription: description,
      attendees,
      receiptAttached: true,
      receiptUrl: selectedReceiptUrl,
      extractedReceipt: ocrResult || undefined
    });

    if (onClose) onClose();
  };

  // Pre-check calculations
  const numAmount = parseFloat(amount) || 0;
  const expDateObj = new Date(expenseDate);
  const nowObj = new Date();
  const daysDiff = Math.ceil(Math.abs(nowObj.getTime() - expDateObj.getTime()) / (1000 * 3600 * 24));

  let preCheckStatus: 'AUTO_APPROVE' | 'MANAGER_REVIEW' | 'VIOLATION' = 'AUTO_APPROVE';
  let preCheckMsg = '';

  if (daysDiff > 90) {
    preCheckStatus = 'VIOLATION';
    preCheckMsg = `Exceeds 90-day submission limit (${daysDiff} days old). Requires Finance Director approval.`;
  } else if (category === 'Meals' && numAmount > 40) {
    preCheckStatus = 'VIOLATION';
    preCheckMsg = `Exceeds $40 daily meal limit.`;
  } else if (category === 'Meals' && numAmount > 25) {
    preCheckStatus = 'MANAGER_REVIEW';
    preCheckMsg = `Above $25 auto-approve limit. Will route to Manager Review.`;
  } else if (['Flights', 'Lodging', 'Client Entertainment', 'Training & Professional Dev', 'Team Events'].includes(category)) {
    preCheckStatus = 'MANAGER_REVIEW';
    preCheckMsg = `${category} category ALWAYS requires manual manager review.`;
  } else {
    preCheckMsg = `Compliant with policy rules. Eligible for auto-approval.`;
  }

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl text-slate-200 max-w-4xl mx-auto">
      <div className="flex items-center justify-between pb-4 border-b border-slate-800 mb-6">
        <div>
          <h2 className="text-lg font-bold text-white flex items-center gap-2">
            <FileText className="w-5 h-5 text-indigo-400" />
            Submit New Expense Claim
          </h2>
          <p className="text-xs text-slate-400">
            Submit expense claims with AI OCR verification and instant policy validation.
          </p>
        </div>
        {onClose && (
          <button onClick={onClose} className="text-slate-400 hover:text-white p-1">
            <X className="w-5 h-5" />
          </button>
        )}
      </div>

      {/* Quick Receipt Preset Picker */}
      <div className="mb-6">
        <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
          Load Sample Receipts for Verification:
        </label>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
          {SAMPLE_RECEIPTS.map((sample, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => handleSelectSample(sample)}
              className="text-left p-2.5 rounded-lg border border-slate-800 bg-slate-950/60 hover:bg-slate-800/80 hover:border-indigo-500/50 transition-all text-xs group"
            >
              <div className="font-semibold text-slate-200 group-hover:text-indigo-300 truncate">
                {sample.name}
              </div>
              <div className="text-slate-400 text-[11px] mt-0.5">
                {sample.category} • ${sample.amount.toFixed(2)}
              </div>
            </button>
          ))}
        </div>
      </div>

      <form onSubmit={handleSubmit} className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Left Column: Form Details */}
        <div className="space-y-4 text-xs">
          <div>
            <label className="block text-slate-300 font-medium mb-1">Expense Category</label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value as ExpenseCategory)}
              className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2.5 text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            >
              <option value="Meals">Meals (Max $40/day | Auto-approve $25)</option>
              <option value="Ground Transport">Ground Transport - Taxi / Rideshare (Max $150 | Auto $50)</option>
              <option value="Flights">Flights (Always Manual Review)</option>
              <option value="Lodging">Lodging (L1-L3: $120/night | L4+: $250/night)</option>
              <option value="Client Entertainment">Client Entertainment (Manager+ required | Max $500)</option>
              <option value="Communications & Connectivity">Communications & Connectivity ($50/mo)</option>
              <option value="Training & Professional Dev">Training & Professional Dev (Pre-approval required)</option>
              <option value="Software & Subscriptions">Software & Subscriptions (Max $300/yr)</option>
              <option value="Team Events">Team Events (Manager+ | $75/person)</option>
              <option value="Health & Wellness">Health & Wellness ($50/mo)</option>
              <option value="Misc / Other">Misc / Other (Max $50)</option>
            </select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-slate-300 font-medium mb-1">Merchant / Vendor</label>
              <input
                type="text"
                value={vendor}
                onChange={(e) => setVendor(e.target.value)}
                required
                className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-slate-300 font-medium mb-1">Amount ($ USD)</label>
              <input
                type="number"
                step="0.01"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                required
                className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500 font-semibold text-indigo-300"
              />
            </div>
          </div>

          <div>
            <label className="block text-slate-300 font-medium mb-1">Expense Transaction Date</label>
            <input
              type="date"
              value={expenseDate}
              onChange={(e) => setExpenseDate(e.target.value)}
              required
              className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          <div>
            <label className="block text-slate-300 font-medium mb-1">Business Purpose & Description</label>
            <textarea
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              required
              className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          {['Client Entertainment', 'Meals', 'Team Events'].includes(category) && (
            <div>
              <label className="block text-slate-300 font-medium mb-1">
                Attendees (Required for Client Entertainment & Group Meals)
              </label>
              <input
                type="text"
                placeholder="e.g. David Miller (Manager), Jane Doe (Client VP)"
                value={attendees}
                onChange={(e) => setAttendees(e.target.value)}
                className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
          )}

          {/* Pre-submission Policy Assessment Banner */}
          <div className={`p-3 rounded-lg border text-xs flex items-start gap-2.5 ${
            preCheckStatus === 'VIOLATION'
              ? 'bg-rose-500/10 border-rose-500/30 text-rose-300'
              : preCheckStatus === 'MANAGER_REVIEW'
              ? 'bg-amber-500/10 border-amber-500/30 text-amber-300'
              : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
          }`}>
            {preCheckStatus === 'VIOLATION' ? (
              <AlertCircle className="w-4 h-4 shrink-0 text-rose-400 mt-0.5" />
            ) : preCheckStatus === 'MANAGER_REVIEW' ? (
              <Clock className="w-4 h-4 shrink-0 text-amber-400 mt-0.5" />
            ) : (
              <CheckCircle2 className="w-4 h-4 shrink-0 text-emerald-400 mt-0.5" />
            )}
            <div>
              <div className="font-semibold">
                Pre-Validation: {(preCheckStatus || 'PASSED').replace('_', ' ')}
              </div>
              <div className="text-[11px] opacity-90 mt-0.5">{preCheckMsg}</div>
            </div>
          </div>
        </div>

        {/* Right Column: AI OCR Receipt Extractor */}
        <div className="space-y-4">
          <div className="bg-slate-950 p-4 rounded-xl border border-slate-800">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-semibold text-indigo-300 flex items-center gap-1.5">
                <Sparkles className="w-4 h-4 text-indigo-400" /> AI OCR Textract Processing
              </span>
              <span className="text-[11px] text-slate-500">Gemini 3.6 Flash Server-Side</span>
            </div>

            {/* Receipt Image Preview */}
            <div className="relative h-40 rounded-lg overflow-hidden border border-slate-800 bg-slate-900 flex items-center justify-center mb-3">
              <img
                src={selectedReceiptUrl}
                alt="Receipt"
                className="w-full h-full object-cover opacity-80"
              />
              {isOcrProcessing && (
                <div className="absolute inset-0 bg-slate-950/80 backdrop-blur-sm flex flex-col items-center justify-center text-xs text-indigo-300 gap-2">
                  <div className="w-6 h-6 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
                  <span>Scanning Receipt & Line Items...</span>
                </div>
              )}
            </div>

            {/* Extracted Receipt Summary */}
            {ocrResult && !isOcrProcessing && (
              <div className="space-y-2 text-xs border-t border-slate-800 pt-3">
                <div className="flex justify-between text-slate-400">
                  <span>Detected Merchant:</span>
                  <strong className="text-slate-200">{ocrResult.vendorName}</strong>
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>Extracted Total:</span>
                  <strong className="text-emerald-400 font-semibold">${ocrResult.totalAmount.toFixed(2)} USD</strong>
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>Alcohol Detected:</span>
                  <strong className={ocrResult.hasAlcohol ? 'text-rose-400 font-semibold' : 'text-slate-200'}>
                    {ocrResult.hasAlcohol ? 'YES (Flagged)' : 'No'}
                  </strong>
                </div>
                <div className="text-[11px] text-slate-500 pt-1">
                  Confidence Score: {(ocrResult.confidenceScore * 100).toFixed(0)}% • Line items matched: {ocrResult.lineItems.length}
                </div>
              </div>
            )}
          </div>

          <button
            type="submit"
            className="w-full py-3 px-4 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold rounded-xl shadow-lg transition-all flex items-center justify-center gap-2 text-xs"
          >
            <span>Submit Claim to Step Functions Workflow</span>
            <ArrowRight className="w-4 h-4" />
          </button>
        </div>
      </form>
    </div>
  );
};
