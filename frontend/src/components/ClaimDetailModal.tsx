import React, { useState } from 'react';
import { ExpenseClaim, UserRole } from '../types';
import { ShieldAlert, CheckCircle2, Clock, X, AlertTriangle, FileText, Send, User, Sparkles, CornerDownRight } from 'lucide-react';

interface ClaimDetailModalProps {
  claim: ExpenseClaim;
  currentRole: UserRole;
  currentActorName: string;
  onClose: () => void;
  onExecuteAction: (action: 'APPROVE' | 'REJECT' | 'DISBURSE' | 'FLAG_FRAUD', notes: string) => void;
}

export const ClaimDetailModal: React.FC<ClaimDetailModalProps> = ({
  claim,
  currentRole,
  currentActorName,
  onClose,
  onExecuteAction
}) => {
  const [notes, setNotes] = useState('');
  const [activeSubTab, setActiveSubTab] = useState<'policy' | 'fraud' | 'receipt' | 'workflow'>('policy');

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4 overflow-y-auto">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-4xl w-full shadow-2xl text-slate-200 my-8 overflow-hidden">
        {/* Modal Header */}
        <div className="p-6 bg-slate-950 border-b border-slate-800 flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3">
              <span className="font-mono text-sm font-bold text-indigo-400 bg-indigo-500/10 px-2.5 py-1 rounded border border-indigo-500/30">
                {claim.claimNumber}
              </span>
              <h2 className="text-xl font-bold text-white">${claim.amountUSD.toFixed(2)} USD</h2>
              <span className={`px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                claim.status === 'Auto_Approved' || claim.status === 'Approved' || claim.status === 'Disbursed'
                  ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                  : claim.status === 'Flagged_Fraud'
                  ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30 font-bold animate-pulse'
                  : 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
              }`}>
                {(claim.status || '').replace('_', ' ')}
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              Submitted by <strong className="text-slate-200">{claim.employeeName}</strong> (Grade {claim.employeeGrade} • {claim.department}) on {claim.submissionDate}
            </p>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal Inner Body */}
        <div className="p-6 space-y-6 max-h-[70vh] overflow-y-auto">
          {/* Metadata Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 bg-slate-950/60 p-4 rounded-xl border border-slate-800 text-xs">
            <div>
              <span className="text-slate-500 block">Category:</span>
              <strong className="text-slate-200">{claim.category}</strong>
            </div>
            <div>
              <span className="text-slate-500 block">Merchant Vendor:</span>
              <strong className="text-slate-200">{claim.merchantVendor}</strong>
            </div>
            <div>
              <span className="text-slate-500 block">Expense Date:</span>
              <strong className="text-slate-200">{claim.expenseDate}</strong>
            </div>
            <div>
              <span className="text-slate-500 block">Receipt Status:</span>
              <strong className={claim.receiptAttached ? 'text-emerald-400' : 'text-rose-400'}>
                {claim.receiptAttached ? 'Attached & OCR Parsed' : 'Missing'}
              </strong>
            </div>
          </div>

          <div>
            <span className="text-xs font-semibold text-slate-400 block mb-1">Business Purpose & Notes:</span>
            <p className="text-xs text-slate-200 bg-slate-950 p-3 rounded-lg border border-slate-800">
              {claim.purposeDescription}
            </p>
          </div>

          {claim.attendees && (
            <div>
              <span className="text-xs font-semibold text-slate-400 block mb-1">Attendees Listed:</span>
              <p className="text-xs text-indigo-300 bg-indigo-950/30 p-2.5 rounded-lg border border-indigo-500/20 font-mono">
                {claim.attendees}
              </p>
            </div>
          )}

          {/* Sub Tab Navigation */}
          <div className="border-b border-slate-800 flex space-x-4 text-xs font-medium">
            <button
              onClick={() => setActiveSubTab('policy')}
              className={`pb-2 transition-colors border-b-2 ${
                activeSubTab === 'policy'
                  ? 'border-indigo-500 text-indigo-400 font-bold'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              AI Policy Evaluation ({claim.policyValidation?.checks.length || 0} checks)
            </button>
            <button
              onClick={() => setActiveSubTab('fraud')}
              className={`pb-2 transition-colors border-b-2 ${
                activeSubTab === 'fraud'
                  ? 'border-indigo-500 text-indigo-400 font-bold'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              Fraud & Anomaly Matrix (Risk: {claim.fraudScreening?.riskScore ?? 0}/100)
            </button>
            <button
              onClick={() => setActiveSubTab('receipt')}
              className={`pb-2 transition-colors border-b-2 ${
                activeSubTab === 'receipt'
                  ? 'border-indigo-500 text-indigo-400 font-bold'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              OCR Receipt Breakdown
            </button>
            <button
              onClick={() => setActiveSubTab('workflow')}
              className={`pb-2 transition-colors border-b-2 ${
                activeSubTab === 'workflow'
                  ? 'border-indigo-500 text-indigo-400 font-bold'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              Step Functions Audit Trace
            </button>
          </div>

          {/* Sub Tab Content */}
          {activeSubTab === 'policy' && claim.policyValidation && (
            <div className="space-y-3">
              <div className="bg-slate-950 p-3.5 rounded-lg border border-slate-800 text-xs">
                <div className="font-semibold text-slate-200 flex items-center gap-1.5 mb-1">
                  <Sparkles className="w-4 h-4 text-indigo-400" /> AI Policy Reasoning Summary:
                </div>
                <p className="text-slate-300">{claim.policyValidation.reasoningSummary}</p>
              </div>

              <div className="space-y-2">
                {claim.policyValidation.checks.map((check, idx) => (
                  <div
                    key={idx}
                    className={`p-3 rounded-lg border text-xs flex items-start justify-between gap-3 ${
                      check.passed
                        ? 'bg-slate-950/60 border-slate-800'
                        : 'bg-rose-500/10 border-rose-500/30'
                    }`}
                  >
                    <div className="flex items-start gap-2.5">
                      {check.passed ? (
                        <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                      ) : (
                        <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                      )}
                      <div>
                        <div className="font-semibold text-slate-200">{check.ruleName}</div>
                        <div className="text-slate-400 text-[11px] mt-0.5">{check.message}</div>
                      </div>
                    </div>
                    <span className={`px-2 py-0.5 rounded text-[10px] font-semibold shrink-0 ${
                      check.passed ? 'bg-emerald-500/20 text-emerald-300' : 'bg-rose-500/20 text-rose-300'
                    }`}>
                      {check.severity}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {activeSubTab === 'fraud' && claim.fraudScreening && (
            <div className="space-y-4 text-xs">
              <div className="flex items-center justify-between p-4 bg-slate-950 rounded-xl border border-slate-800">
                <div>
                  <div className="text-slate-400">Calculated Anomaly Risk Score:</div>
                  <div className="text-2xl font-black text-white flex items-center gap-2">
                    {claim.fraudScreening.riskScore}/100
                    <span className={`text-xs px-2.5 py-0.5 rounded font-semibold ${
                      claim.fraudScreening.riskLevel === 'CRITICAL' || claim.fraudScreening.riskLevel === 'HIGH'
                        ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                        : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                    }`}>
                      {claim.fraudScreening.riskLevel} RISK
                    </span>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-slate-400">Recommended System Action:</div>
                  <div className="font-bold text-indigo-300">{claim.fraudScreening.recommendedAction}</div>
                </div>
              </div>

              {claim.fraudScreening.flags.length === 0 ? (
                <div className="p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-lg text-emerald-300 text-xs">
                  ✓ Zero fraud or anomaly flags detected. Clean submission pattern.
                </div>
              ) : (
                <div className="space-y-2">
                  {claim.fraudScreening.flags.map((flag, idx) => (
                    <div key={idx} className="p-3.5 bg-rose-500/10 border border-rose-500/30 rounded-lg text-rose-200">
                      <div className="font-bold text-rose-300 flex items-center gap-2">
                        <ShieldAlert className="w-4 h-4 text-rose-400" /> [{flag.code}] {flag.title}
                      </div>
                      <p className="mt-1 text-slate-300">{flag.description}</p>
                      <div className="mt-1.5 text-[11px] font-mono text-rose-300/80 bg-slate-950/60 p-2 rounded border border-rose-500/20">
                        Evidence: {flag.evidence}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {activeSubTab === 'receipt' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
              <div className="bg-slate-950 p-3 rounded-lg border border-slate-800 flex items-center justify-center">
                {claim.receiptUrl ? (
                  <img src={claim.receiptUrl} alt="Receipt" className="max-h-64 object-contain rounded" />
                ) : (
                  <div className="text-slate-500 text-center p-8">No receipt image URL provided</div>
                )}
              </div>
              <div className="bg-slate-950 p-4 rounded-lg border border-slate-800 space-y-3">
                <div className="font-bold text-slate-200 border-b border-slate-800 pb-2">Extracted Receipt Line Items:</div>
                {claim.extractedReceipt?.lineItems?.map((item, i) => (
                  <div key={i} className="flex justify-between text-slate-300 border-b border-slate-800/40 pb-1">
                    <span>{item.description}</span>
                    <strong className="text-slate-100">${item.amount.toFixed(2)}</strong>
                  </div>
                ))}
                <div className="flex justify-between font-bold text-emerald-400 pt-2 border-t border-slate-700">
                  <span>Extracted Total:</span>
                  <span>${claim.extractedReceipt?.totalAmount.toFixed(2) || claim.amountUSD.toFixed(2)}</span>
                </div>
              </div>
            </div>
          )}

          {activeSubTab === 'workflow' && (
            <div className="space-y-3 text-xs">
              <div className="font-semibold text-slate-300">AWS Step Functions Execution Logs:</div>
              <div className="space-y-2">
                {claim.workflowHistory.map((step, idx) => (
                  <div key={idx} className="p-3 bg-slate-950 rounded-lg border border-slate-800 flex items-start gap-3">
                    <CornerDownRight className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5" />
                    <div className="flex-1">
                      <div className="flex items-center justify-between">
                        <strong className="text-slate-200">{step.stepName}</strong>
                        <span className="text-[10px] font-mono text-slate-500">{step.timestamp}</span>
                      </div>
                      <div className="text-slate-400 mt-0.5">{step.action}</div>
                      {step.traceId && (
                        <div className="text-[10px] font-mono text-indigo-400/80 mt-1">Trace ID: {step.traceId}</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Manager & Finance Action Controls */}
          <div className="bg-slate-950 p-4 rounded-xl border border-slate-800 space-y-3">
            <label className="block text-xs font-semibold text-slate-300">
              Approval Decision / Manager Review Notes:
            </label>
            <textarea
              rows={2}
              placeholder="Add review notes or justification..."
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />

            <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
              <div className="text-xs text-slate-400">
                Acting as: <strong className="text-slate-200">{currentActorName}</strong> ({currentRole.toUpperCase()})
              </div>

              <div className="flex items-center space-x-2">
                <button
                  onClick={() => onExecuteAction('REJECT', notes)}
                  className="px-3.5 py-2 bg-rose-600/20 hover:bg-rose-600/30 text-rose-300 border border-rose-500/40 rounded-lg text-xs font-semibold transition-all"
                >
                  Reject Claim
                </button>
                <button
                  onClick={() => onExecuteAction('FLAG_FRAUD', notes || 'Flagged for fraud audit')}
                  className="px-3.5 py-2 bg-amber-600/20 hover:bg-amber-600/30 text-amber-300 border border-amber-500/40 rounded-lg text-xs font-semibold transition-all"
                >
                  Flag Fraud Audit
                </button>
                <button
                  onClick={() => onExecuteAction('APPROVE', notes)}
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-semibold shadow-lg transition-all"
                >
                  Approve Claim
                </button>
                {currentRole === 'finance' && (
                  <button
                    onClick={() => onExecuteAction('DISBURSE', notes || 'Reimbursement disbursed to employee account')}
                    className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold shadow-lg transition-all"
                  >
                    Disburse Payout
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
