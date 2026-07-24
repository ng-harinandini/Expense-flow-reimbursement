import React from 'react';
import { PolicyRuleDefinition } from '../types';
import { FileText, AlertCircle, Info } from 'lucide-react';

interface PolicyGuidelinesReadonlyProps {
  rules: PolicyRuleDefinition[];
}

export const PolicyGuidelinesReadonly: React.FC<PolicyGuidelinesReadonlyProps> = ({ rules }) => {
  return (
    <div className="space-y-6 text-slate-200">
      {/* Header Banner */}
      <div className="bg-slate-900 border border-slate-800 rounded p-6 shadow-lg">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded bg-sky-500/20 text-sky-400 border border-sky-500/30 flex items-center justify-center font-bold">
            <FileText className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">Company Expense & Reimbursement Policy Guidelines</h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Official corporate expense rules enforced automatically by the AI Policy Engine during submission.
            </p>
          </div>
        </div>
      </div>

      {/* Rules Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {rules.map((rule, index) => (
          <div key={rule.category || index} className="bg-slate-900 border border-slate-800 rounded p-5 shadow space-y-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
              <span className="text-xs font-bold text-white uppercase tracking-wider font-mono">
                {rule.category} Rules
              </span>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase bg-sky-500/20 text-sky-300 border border-sky-500/30">
                {rule.gradeTier}
              </span>
            </div>

            <div className="bg-slate-950 p-3 rounded border border-slate-800/80 space-y-1.5 font-mono text-[11px]">
              <div className="flex justify-between text-slate-400">
                <span>Max Ceiling:</span>
                <strong className="text-white">
                  {typeof rule.maxAmountUSD === 'number' ? `$${rule.maxAmountUSD}` : rule.maxAmountUSD}
                </strong>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Auto-Approve Limit:</span>
                <strong className="text-emerald-400">
                  {rule.autoApproveLimitUSD !== null ? `$${rule.autoApproveLimitUSD}` : 'Manual Review Only'}
                </strong>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Receipt Required Above:</span>
                <strong className="text-amber-300">${rule.receiptRequiredAboveUSD}</strong>
              </div>
            </div>

            {rule.specialRules && rule.specialRules.length > 0 && (
              <div className="pt-2 border-t border-slate-800/60">
                <span className="block text-[10px] text-slate-500 uppercase font-mono mb-1">Enforced Guidelines:</span>
                <ul className="space-y-1 text-xs text-slate-300 font-sans">
                  {rule.specialRules.map((sr, i) => (
                    <li key={i} className="flex items-start gap-1.5">
                      <span className="text-sky-400 font-bold">•</span>
                      <span>{sr}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Helpful Employee Tips */}
      <div className="bg-slate-900 border border-slate-800 rounded p-4 text-xs space-y-2">
        <h4 className="font-bold text-slate-300 uppercase tracking-widest font-mono text-[10px] flex items-center gap-2">
          <Info className="w-3.5 h-3.5 text-sky-400" /> Fast Reimbursement Best Practices
        </h4>
        <ul className="list-disc list-inside text-slate-400 space-y-1 pl-1 font-sans">
          <li>Always attach a clear photo or PDF receipt for expenses above $25.</li>
          <li>For team meals or client entertainment, list all attendee names in the submission form.</li>
          <li>Expenses adhering strictly to policy guidelines are auto-approved instantly in under 2 seconds.</li>
        </ul>
      </div>
    </div>
  );
};
